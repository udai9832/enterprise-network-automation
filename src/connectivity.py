"""Basic ICMP (ping) reachability testing for lab devices.

Uses the operating system's own `ping` command, so no extra libraries or
administrator rights are needed. Works on Windows and Linux.
"""

import platform
import re
import subprocess
from dataclasses import asdict, dataclass

# A successful echo reply contains "TTL=" on Windows and "ttl=" on Linux.
# Counting these lines avoids a common Windows trap: "Reply from <gateway>:
# Destination host unreachable" is counted as "Received" by Windows ping
# and returns exit code 0, even though the target never answered.
REPLY_PATTERN = re.compile(r"ttl=\d+", re.IGNORECASE)

# Round-trip time, e.g. "time=12ms", "time<1ms" (Windows) or "time=0.045 ms" (Linux).
RTT_PATTERN = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


@dataclass
class PingResult:
    """Structured result of a ping test against one host."""

    host: str
    reachable: bool
    packets_sent: int
    packets_received: int
    packet_loss: float
    avg_rtt_ms: float | None = None
    error: str | None = None

    def to_dict(self):
        return asdict(self)


def build_ping_command(host, count=2, timeout=2, system=None):
    """Build the ping command for the current (or given) operating system.

    `timeout` is the per-packet wait time in seconds.
    """
    system = (system or platform.system()).lower()
    if system == "windows":
        # -n = number of echo requests, -w = timeout in milliseconds
        return ["ping", "-n", str(count), "-w", str(timeout * 1000), host]
    # Linux: -c = number of echo requests, -W = timeout in seconds
    return ["ping", "-c", str(count), "-W", str(timeout), host]


def parse_ping_output(host, output, count):
    """Turn raw ping output into a PingResult."""
    received = len(REPLY_PATTERN.findall(output))
    received = min(received, count)
    rtts = [float(value) for value in RTT_PATTERN.findall(output)]

    return PingResult(
        host=host,
        reachable=received > 0,
        packets_sent=count,
        packets_received=received,
        packet_loss=round((count - received) / count * 100, 1),
        avg_rtt_ms=round(sum(rtts) / len(rtts), 2) if rtts else None,
    )


def ping_host(host, count=2, timeout=2):
    """Ping a host and return a PingResult.

    Never raises for network problems: an unreachable host or a failing
    ping command is reported through `reachable=False` and `error`.
    Raises ValueError only for invalid arguments.
    """
    host = str(host or "").strip()
    if not host or host.startswith("-"):
        raise ValueError(f"Invalid host: {host!r}")
    if count < 1 or timeout < 1:
        raise ValueError("count and timeout must be at least 1")

    command = build_ping_command(host, count, timeout)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            # Upper bound for the whole command so a hung ping cannot block the script.
            timeout=count * (timeout + 1) + 5,
        )
    except FileNotFoundError:
        return _failed_result(host, count, "ping command not found on this system")
    except subprocess.TimeoutExpired:
        return _failed_result(host, count, "ping command timed out")
    except OSError as exc:
        return _failed_result(host, count, f"could not run ping: {exc}")

    result = parse_ping_output(host, completed.stdout, count)
    if not result.reachable:
        details = (completed.stderr or "").strip()
        result.error = details or f"no replies received (exit code {completed.returncode})"
    return result


def _failed_result(host, count, error):
    return PingResult(
        host=host,
        reachable=False,
        packets_sent=count,
        packets_received=0,
        packet_loss=100.0,
        error=error,
    )
