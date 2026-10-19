"""Tests for src/network_check.py.

ping_host is mocked in every test, so no real ping commands are executed and
no network, Cisco devices or GNS3 lab are needed.
"""

import csv
from unittest import mock

import pytest

from src.connectivity import PingResult
from src.inventory import InventoryError
from src.network_check import (
    DEFAULT_REPORT_PATH,
    REPORT_FIELDS,
    check_device,
    format_results_table,
    format_summary,
    main,
    run_connectivity_check,
    summarize_results,
)

INVENTORY = """
devices:
  - hostname: R1
    device_type: cisco_ios
    host: 192.168.100.11
    role: core-router
  - hostname: R2
    device_type: cisco_ios
    host: 192.168.100.12
    role: edge-router
  - hostname: SW1
    device_type: cisco_ios
    host: 192.168.100.21
    role: access-switch
"""

R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}


def up(host, count=2, timeout=2):
    return PingResult(host, True, count, count, 0.0, avg_rtt_ms=1.5)


def down(host, count=2, timeout=2):
    return PingResult(host, False, count, 0, 100.0, error="no replies received (exit code 1)")


def fake_ping(down_hosts=(), broken_hosts=()):
    """Return a fake ping_host: hosts in down_hosts fail, broken_hosts raise an exception."""
    def _ping(host, count=2, timeout=2):
        if host in broken_hosts:
            raise RuntimeError("something went wrong")
        if host in down_hosts:
            return down(host, count, timeout)
        return up(host, count, timeout)
    return _ping


@pytest.fixture
def inventory_file(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text(INVENTORY, encoding="utf-8")
    return path


# --- single device ----------------------------------------------------------

@mock.patch("src.network_check.ping_host", side_effect=up)
def test_check_device_reachable(mock_ping):
    result = check_device(R1)

    assert result["hostname"] == "R1"
    assert result["host"] == "192.168.100.11"
    assert result["role"] == "core-router"
    assert result["reachable"] is True
    assert result["packets_sent"] == 2
    assert result["packets_received"] == 2
    assert result["packet_loss_percent"] == 0.0
    assert result["average_rtt_ms"] == 1.5
    assert result["error"] is None
    assert result["checked_at"]
    assert list(result) == REPORT_FIELDS


@mock.patch("src.network_check.ping_host", side_effect=down)
def test_check_device_unreachable(mock_ping):
    result = check_device(R1)

    assert result["reachable"] is False
    assert result["packets_received"] == 0
    assert result["packet_loss_percent"] == 100.0
    assert result["average_rtt_ms"] is None
    assert "no replies" in result["error"]


@mock.patch("src.network_check.ping_host", side_effect=RuntimeError("boom"))
def test_check_device_unexpected_error(mock_ping):
    result = check_device(R1)

    assert result["hostname"] == "R1"
    assert result["reachable"] is False
    assert result["error"] == "unexpected error: boom"


@mock.patch("src.network_check.ping_host", side_effect=up)
def test_check_device_passes_count_and_timeout(mock_ping):
    check_device(R1, count=4, timeout=1)

    mock_ping.assert_called_once_with("192.168.100.11", count=4, timeout=1)


# --- full inventory run -----------------------------------------------------

@mock.patch("src.network_check.ping_host", side_effect=fake_ping())
def test_run_all_devices_reachable(mock_ping, inventory_file):
    results = run_connectivity_check(inventory_file)

    assert [result["hostname"] for result in results] == ["R1", "R2", "SW1"]
    assert all(result["reachable"] for result in results)
    assert mock_ping.call_count == 3


@mock.patch("src.network_check.ping_host", side_effect=fake_ping(down_hosts={"192.168.100.11", "192.168.100.12", "192.168.100.21"}))
def test_run_all_devices_unreachable(mock_ping, inventory_file):
    results = run_connectivity_check(inventory_file)

    assert not any(result["reachable"] for result in results)
    assert summarize_results(results) == {"total": 3, "reachable": 0, "unreachable": 3}


@mock.patch("src.network_check.ping_host", side_effect=fake_ping(down_hosts={"192.168.100.12"}))
def test_run_mixed_connectivity(mock_ping, inventory_file):
    results = run_connectivity_check(inventory_file)

    status = {result["hostname"]: result["reachable"] for result in results}
    assert status == {"R1": True, "R2": False, "SW1": True}
    assert summarize_results(results) == {"total": 3, "reachable": 2, "unreachable": 1}


@mock.patch("src.network_check.ping_host", side_effect=fake_ping(broken_hosts={"192.168.100.12"}))
def test_one_device_failure_does_not_stop_run(mock_ping, inventory_file):
    results = run_connectivity_check(inventory_file)

    assert len(results) == 3
    assert results[0]["reachable"] is True
    assert results[1]["reachable"] is False
    assert "unexpected error" in results[1]["error"]
    assert results[2]["reachable"] is True


@mock.patch("src.network_check.ping_host")
def test_run_missing_inventory(mock_ping, tmp_path):
    with pytest.raises(InventoryError, match="not found"):
        run_connectivity_check(tmp_path / "missing.yaml")
    mock_ping.assert_not_called()


@mock.patch("src.network_check.ping_host")
def test_run_malformed_inventory(mock_ping, tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text("devices:\n  - hostname: R1\n", encoding="utf-8")

    with pytest.raises(InventoryError, match="missing required field"):
        run_connectivity_check(path)
    mock_ping.assert_not_called()


# --- summary and table ------------------------------------------------------

def test_summarize_empty_results():
    assert summarize_results([]) == {"total": 0, "reachable": 0, "unreachable": 0}


def test_format_summary():
    text = format_summary({"total": 3, "reachable": 2, "unreachable": 1})

    assert text.splitlines() == ["Total devices: 3", "Reachable:     2", "Unreachable:   1"]


@mock.patch("src.network_check.ping_host", side_effect=fake_ping(down_hosts={"192.168.100.12"}))
def test_format_results_table(mock_ping, inventory_file):
    lines = format_results_table(run_connectivity_check(inventory_file)).splitlines()

    assert lines[0].split() == ["HOSTNAME", "HOST", "ROLE", "STATUS", "LOSS", "AVG", "RTT", "ERROR"]
    assert set(lines[1].replace(" ", "")) == {"-"}
    assert "UP" in lines[2] and "1.5 ms" in lines[2]
    assert "DOWN" in lines[3] and "100%" in lines[3] and "no replies" in lines[3]
    # Columns line up: the HOST column starts at the same position on every row.
    host_column = lines[0].index("HOST ")
    assert all(line[host_column:].startswith("192.168.100.") for line in lines[2:])


# --- CLI --------------------------------------------------------------------

def read_csv(path):
    with open(path, newline="", encoding="utf-8") as report_file:
        return list(csv.DictReader(report_file))


@mock.patch("src.network_check.ping_host", side_effect=fake_ping())
def test_main_all_reachable(mock_ping, inventory_file, tmp_path, capsys):
    report = tmp_path / "reports" / "connectivity_report.csv"

    exit_code = main(["--inventory", str(inventory_file), "--report", str(report)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Total devices: 3" in output
    assert "Reachable:     3" in output
    assert "Unreachable:   0" in output
    assert f"Report saved to: {report}" in output


@mock.patch("src.network_check.ping_host", side_effect=fake_ping(down_hosts={"192.168.100.21"}))
def test_main_writes_csv_report(mock_ping, inventory_file, tmp_path):
    report = tmp_path / "connectivity_report.csv"

    exit_code = main(["--inventory", str(inventory_file), "--report", str(report)])

    rows = read_csv(report)
    assert exit_code == 1
    assert list(rows[0]) == REPORT_FIELDS
    assert [row["hostname"] for row in rows] == ["R1", "R2", "SW1"]
    assert [row["reachable"] for row in rows] == ["True", "True", "False"]
    assert rows[2]["packet_loss_percent"] == "100.0"
    assert rows[2]["average_rtt_ms"] == ""
    assert rows[2]["error"] == "no replies received (exit code 1)"


@mock.patch("src.network_check.ping_host", side_effect=up)
def test_main_passes_count_and_timeout(mock_ping, inventory_file, tmp_path):
    main(["--inventory", str(inventory_file), "--report", str(tmp_path / "r.csv"), "--count", "4", "--timeout", "1"])

    mock_ping.assert_any_call("192.168.100.11", count=4, timeout=1)


@mock.patch("src.network_check.ping_host")
def test_main_missing_inventory(mock_ping, tmp_path, capsys):
    report = tmp_path / "connectivity_report.csv"

    exit_code = main(["--inventory", str(tmp_path / "missing.yaml"), "--report", str(report)])

    assert exit_code == 2
    assert "Inventory file not found" in capsys.readouterr().err
    assert not report.exists()
    mock_ping.assert_not_called()


@mock.patch("src.network_check.write_csv_report", side_effect=PermissionError("file is open in another program"))
@mock.patch("src.network_check.ping_host", side_effect=up)
def test_main_report_write_error(mock_ping, mock_write, inventory_file, capsys):
    exit_code = main(["--inventory", str(inventory_file)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Total devices: 3" in captured.out
    assert "could not write report" in captured.err


@pytest.mark.parametrize("option", ["--count", "--timeout"])
def test_main_rejects_invalid_numbers(option):
    with pytest.raises(SystemExit) as exc_info:
        main([option, "0"])
    assert exc_info.value.code == 2


def test_default_report_location():
    assert DEFAULT_REPORT_PATH.parts[-3:] == ("data", "reports", "connectivity_report.csv")
