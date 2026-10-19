"""Tests for src/connectivity.py.

The real `ping` command is never executed: subprocess.run is mocked, so these
tests do not need network access, Cisco devices or a running GNS3 lab.
"""

import subprocess
from unittest import mock

import pytest

from src.connectivity import build_ping_command, parse_ping_output, ping_host

WINDOWS_SUCCESS = """
Pinging 192.168.100.11 with 32 bytes of data:
Reply from 192.168.100.11: bytes=32 time=12ms TTL=255
Reply from 192.168.100.11: bytes=32 time=9ms TTL=255

Ping statistics for 192.168.100.11:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 9ms, Maximum = 12ms, Average = 10ms
"""

WINDOWS_PARTIAL = """
Pinging 192.168.100.12 with 32 bytes of data:
Request timed out.
Reply from 192.168.100.12: bytes=32 time<1ms TTL=255

Ping statistics for 192.168.100.12:
    Packets: Sent = 2, Received = 1, Lost = 1 (50% loss),
"""

WINDOWS_TIMEOUT = """
Pinging 192.168.100.99 with 32 bytes of data:
Request timed out.
Request timed out.

Ping statistics for 192.168.100.99:
    Packets: Sent = 2, Received = 0, Lost = 2 (100% loss),
"""

# Windows counts this as "Received = 2" and exits with code 0,
# but the target host never replied.
WINDOWS_DEST_UNREACHABLE = """
Pinging 192.168.100.99 with 32 bytes of data:
Reply from 192.168.100.1: Destination host unreachable.
Reply from 192.168.100.1: Destination host unreachable.

Ping statistics for 192.168.100.99:
    Packets: Sent = 2, Received = 2, Lost = 0 (0% loss),
"""

LINUX_SUCCESS = """
PING 192.168.100.21 (192.168.100.21) 56(84) bytes of data.
64 bytes from 192.168.100.21: icmp_seq=1 ttl=255 time=1.20 ms
64 bytes from 192.168.100.21: icmp_seq=2 ttl=255 time=0.80 ms

--- 192.168.100.21 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 0.800/1.000/1.200/0.200 ms
"""

LINUX_UNREACHABLE = """
PING 192.168.100.99 (192.168.100.99) 56(84) bytes of data.

--- 192.168.100.99 ping statistics ---
2 packets transmitted, 0 received, 100% packet loss, time 1023ms
"""


# --- command building -------------------------------------------------------

def test_build_ping_command_windows():
    command = build_ping_command("192.168.100.11", count=3, timeout=2, system="Windows")
    assert command == ["ping", "-n", "3", "-w", "2000", "192.168.100.11"]


def test_build_ping_command_linux():
    command = build_ping_command("192.168.100.11", count=3, timeout=2, system="Linux")
    assert command == ["ping", "-c", "3", "-W", "2", "192.168.100.11"]


# --- output parsing ---------------------------------------------------------

def test_parse_windows_success():
    result = parse_ping_output("192.168.100.11", WINDOWS_SUCCESS, count=2)

    assert result.reachable is True
    assert result.packets_sent == 2
    assert result.packets_received == 2
    assert result.packet_loss == 0.0
    assert result.avg_rtt_ms == 10.5


def test_parse_windows_partial_loss():
    result = parse_ping_output("192.168.100.12", WINDOWS_PARTIAL, count=2)

    assert result.reachable is True
    assert result.packets_received == 1
    assert result.packet_loss == 50.0
    assert result.avg_rtt_ms == 1.0


def test_parse_windows_timeout():
    result = parse_ping_output("192.168.100.99", WINDOWS_TIMEOUT, count=2)

    assert result.reachable is False
    assert result.packets_received == 0
    assert result.packet_loss == 100.0
    assert result.avg_rtt_ms is None


def test_parse_windows_destination_unreachable_is_not_reachable():
    result = parse_ping_output("192.168.100.99", WINDOWS_DEST_UNREACHABLE, count=2)

    assert result.reachable is False
    assert result.packets_received == 0


def test_parse_linux_success():
    result = parse_ping_output("192.168.100.21", LINUX_SUCCESS, count=2)

    assert result.reachable is True
    assert result.packets_received == 2
    assert result.avg_rtt_ms == 1.0


def test_parse_linux_unreachable():
    result = parse_ping_output("192.168.100.99", LINUX_UNREACHABLE, count=2)

    assert result.reachable is False
    assert result.packet_loss == 100.0


# --- ping_host with a mocked subprocess -------------------------------------

def completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["ping"], returncode=returncode, stdout=stdout, stderr=stderr)


@mock.patch("src.connectivity.subprocess.run")
def test_ping_host_reachable(mock_run):
    mock_run.return_value = completed(WINDOWS_SUCCESS)

    result = ping_host("192.168.100.11", count=2)

    assert result.reachable is True
    assert result.error is None
    assert result.to_dict()["host"] == "192.168.100.11"
    command = mock_run.call_args.args[0]
    assert command[0] == "ping"
    assert command[-1] == "192.168.100.11"


@mock.patch("src.connectivity.subprocess.run")
def test_ping_host_unreachable(mock_run):
    mock_run.return_value = completed(WINDOWS_TIMEOUT, returncode=1)

    result = ping_host("192.168.100.99", count=2)

    assert result.reachable is False
    assert result.packet_loss == 100.0
    assert "exit code 1" in result.error


@mock.patch("src.connectivity.subprocess.run")
def test_ping_host_reports_stderr(mock_run):
    mock_run.return_value = completed("", returncode=2, stderr="ping: unknown host bad-host")

    result = ping_host("bad-host")

    assert result.reachable is False
    assert result.error == "ping: unknown host bad-host"


@mock.patch("src.connectivity.subprocess.run", side_effect=FileNotFoundError)
def test_ping_host_ping_not_installed(mock_run):
    result = ping_host("192.168.100.11")

    assert result.reachable is False
    assert "not found" in result.error


@mock.patch("src.connectivity.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ping", timeout=5))
def test_ping_host_command_timeout(mock_run):
    result = ping_host("192.168.100.11")

    assert result.reachable is False
    assert result.packet_loss == 100.0
    assert "timed out" in result.error


@mock.patch("src.connectivity.subprocess.run", side_effect=PermissionError("access denied"))
def test_ping_host_os_error(mock_run):
    result = ping_host("192.168.100.11")

    assert result.reachable is False
    assert "could not run ping" in result.error


@pytest.mark.parametrize("host", ["", "   ", None, "-n"])
def test_ping_host_rejects_invalid_host(host):
    with pytest.raises(ValueError):
        ping_host(host)


def test_ping_host_rejects_invalid_count():
    with pytest.raises(ValueError):
        ping_host("192.168.100.11", count=0)
