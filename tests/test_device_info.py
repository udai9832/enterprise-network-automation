"""Tests for src/device_info.py.

The SSH layer is mocked in every test (src.device_info.connect is replaced with
fake Netmiko connections that return saved Cisco outputs from tests/fixtures/).
No SSH session, network access, Cisco device or GNS3 lab is used.
"""

import csv
from unittest import mock

import pytest

from src.device_info import (
    DEFAULT_REPORT_PATH,
    REPORT_FIELDS,
    UnsupportedCommandError,
    collect_device_info,
    format_results_table,
    format_summary,
    main,
    parse_interfaces_status,
    parse_ip_interface_brief,
    parse_show_version,
    run_device_collection,
    run_show_command,
    summarize_collection,
    summarize_interfaces,
    summarize_switchports,
)
from src.inventory import InventoryError
from src.ssh_client import CredentialsError, SSHAuthenticationError, SSHTimeoutError
from tests.conftest import load_fixture

R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}
SW1 = {"hostname": "SW1", "device_type": "cisco_ios", "host": "192.168.100.21", "role": "access-switch"}
CREDENTIALS = {"username": "labuser", "password": "Test-Pass-123"}

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


def router_outputs(hostname="R1"):
    return {
        "show version": load_fixture("show_version_iosv.txt").replace("R1 uptime", f"{hostname} uptime"),
        "show ip interface brief": load_fixture("show_ip_interface_brief_router.txt"),
        "show interfaces status": load_fixture("invalid_input.txt"),
    }


def switch_outputs():
    return {
        "show version": load_fixture("show_version_iosvl2.txt"),
        "show ip interface brief": load_fixture("show_ip_interface_brief_switch.txt"),
        "show interfaces status": load_fixture("show_interfaces_status_switch.txt"),
    }


def fake_connection(outputs):
    """A stand-in for a Netmiko connection whose send_command returns saved outputs."""
    connection = mock.Mock()
    connection.send_command.side_effect = lambda command: outputs[command]
    return connection


LAB_OUTPUTS = {
    "192.168.100.11": router_outputs("R1"),
    "192.168.100.12": router_outputs("R2"),
    "192.168.100.21": switch_outputs(),
}


def fake_connect(failures=None):
    """Return a fake connect(): hosts in `failures` raise the given exception."""
    failures = failures or {}

    def _connect(device, credentials, timeout=10):
        if device["host"] in failures:
            raise failures[device["host"]]
        return fake_connection(LAB_OUTPUTS[device["host"]])
    return _connect


@pytest.fixture
def inventory_file(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text(INVENTORY, encoding="utf-8")
    return path


# --- show version parsing ---------------------------------------------------

def test_parse_show_version_iosv_router():
    info = parse_show_version(load_fixture("show_version_iosv.txt"))

    assert info == {"device_hostname": "R1", "ios_version": "15.9(3)M6", "uptime": "2 hours, 14 minutes"}


def test_parse_show_version_iosvl2_switch():
    info = parse_show_version(load_fixture("show_version_iosvl2.txt"))

    assert info["device_hostname"] == "SW1"
    assert info["ios_version"] == "15.2(20170321:233949)"
    assert info["uptime"] == "1 day, 3 hours, 5 minutes"


def test_parse_show_version_ios_xe():
    info = parse_show_version(load_fixture("show_version_iosxe.txt"))

    assert info["device_hostname"] == "csr1"
    assert info["ios_version"] == "16.09.05"
    assert info["uptime"] == "3 weeks, 2 days, 1 hour, 7 minutes"


def test_parse_show_version_windows_line_endings():
    output = load_fixture("show_version_iosv.txt").replace("\n", "\r\n")

    info = parse_show_version(output)

    assert info == {"device_hostname": "R1", "ios_version": "15.9(3)M6", "uptime": "2 hours, 14 minutes"}


def test_parse_show_version_unrecognized_output_returns_none():
    info = parse_show_version("some unexpected text\nwithout version details\n")

    assert info == {"device_hostname": None, "ios_version": None, "uptime": None}


def test_parse_show_version_hostname_with_dash():
    info = parse_show_version("CORE-RTR-01 uptime is 5 minutes\n")

    assert info["device_hostname"] == "CORE-RTR-01"
    assert info["uptime"] == "5 minutes"
    assert info["ios_version"] is None


# --- interface parsing ------------------------------------------------------

def test_parse_ip_interface_brief_router():
    interfaces = parse_ip_interface_brief(load_fixture("show_ip_interface_brief_router.txt"))

    assert len(interfaces) == 5
    assert interfaces[0] == {
        "interface": "GigabitEthernet0/0",
        "ip_address": "192.168.100.11",
        "status": "up",
        "protocol": "up",
    }
    assert interfaces[2]["status"] == "administratively down"
    assert interfaces[2]["protocol"] == "down"
    assert interfaces[3]["status"] == "up" and interfaces[3]["protocol"] == "down"


def test_parse_ip_interface_brief_spacing_variations():
    output = (
        "R1#show ip interface brief\n"
        "Interface  IP-Address  OK? Method Status Protocol\n"
        "\n"
        "Gi0/0 10.0.0.1 YES manual up up\n"
        "Gi0/1\t\tunassigned\tYES\tunset\tadministratively   down\tdown   \n"
        "R1#\n"
    )

    interfaces = parse_ip_interface_brief(output)

    assert [intf["interface"] for intf in interfaces] == ["Gi0/0", "Gi0/1"]
    assert interfaces[1]["status"] == "administratively down"


def test_parse_ip_interface_brief_empty_output():
    assert parse_ip_interface_brief("") == []


def test_summarize_interfaces_router_counts():
    summary = summarize_interfaces(parse_ip_interface_brief(load_fixture("show_ip_interface_brief_router.txt")))

    assert summary["interface_count"] == 5
    assert summary["interfaces_up"] == 3      # Gi0/0, Gi0/1, Loopback0
    assert summary["interfaces_down"] == 2    # admin down + up/down
    assert summary["ip_interface_summary"].startswith("GigabitEthernet0/0 192.168.100.11 up/up; ")
    assert "GigabitEthernet0/2 unassigned administratively down/down" in summary["ip_interface_summary"]


def test_summarize_interfaces_switch_counts():
    summary = summarize_interfaces(parse_ip_interface_brief(load_fixture("show_ip_interface_brief_switch.txt")))

    assert (summary["interface_count"], summary["interfaces_up"], summary["interfaces_down"]) == (5, 3, 2)


def test_summarize_no_interfaces():
    assert summarize_interfaces([]) == {
        "interface_count": 0,
        "interfaces_up": 0,
        "interfaces_down": 0,
        "ip_interface_summary": None,
    }


def test_parse_interfaces_status_switch():
    ports = parse_interfaces_status(load_fixture("show_interfaces_status_switch.txt"))

    assert ports == [
        {"port": "Gi0/0", "status": "connected"},   # Name column "Uplink to R1" contains spaces
        {"port": "Gi0/1", "status": "connected"},
        {"port": "Gi0/2", "status": "notconnect"},
        {"port": "Gi0/3", "status": "disabled"},
    ]


def test_summarize_switchports():
    ports = parse_interfaces_status(load_fixture("show_interfaces_status_switch.txt"))

    assert summarize_switchports(ports) == "4 ports: 2 connected, 1 notconnect, 1 disabled"
    assert summarize_switchports([]) is None


# --- running commands -------------------------------------------------------

def test_run_show_command_returns_output():
    connection = fake_connection(router_outputs())

    assert "Version 15.9(3)M6" in run_show_command(connection, "show version")


@pytest.mark.parametrize(
    "output",
    [
        load_fixture("invalid_input.txt"),
        "% Incomplete command.\n",
        "% Ambiguous command:  \"show int\"\n",
    ],
)
def test_run_show_command_unsupported(output):
    connection = fake_connection({"show interfaces status": output})

    with pytest.raises(UnsupportedCommandError, match="'show interfaces status' is not supported"):
        run_show_command(connection, "show interfaces status")


# --- collecting from one device ---------------------------------------------

@mock.patch("src.device_info.connect")
def test_collect_router_success_with_unsupported_optional_command(mock_connect):
    connection = fake_connection(router_outputs())
    mock_connect.return_value = connection

    result = collect_device_info(R1, CREDENTIALS)

    assert result["inventory_hostname"] == "R1"
    assert result["host"] == "192.168.100.11"
    assert result["role"] == "core-router"
    assert result["ssh_status"] == "success"
    assert result["device_hostname"] == "R1"
    assert result["ios_version"] == "15.9(3)M6"
    assert result["uptime"] == "2 hours, 14 minutes"
    assert (result["interface_count"], result["interfaces_up"], result["interfaces_down"]) == (5, 3, 2)
    assert result["switchport_summary"] is None
    assert result["unsupported_commands"] == "show interfaces status"
    assert result["error"] is None
    assert result["checked_at"]
    assert list(result) == REPORT_FIELDS
    connection.disconnect.assert_called_once()


@mock.patch("src.device_info.connect")
def test_collect_switch_success(mock_connect):
    mock_connect.return_value = fake_connection(switch_outputs())

    result = collect_device_info(SW1, CREDENTIALS)

    assert result["ssh_status"] == "success"
    assert result["device_hostname"] == "SW1"
    assert result["switchport_summary"] == "4 ports: 2 connected, 1 notconnect, 1 disabled"
    assert result["unsupported_commands"] is None


@mock.patch("src.device_info.connect")
def test_collect_sends_only_read_only_show_commands(mock_connect):
    connection = fake_connection(switch_outputs())
    mock_connect.return_value = connection

    collect_device_info(SW1, CREDENTIALS)

    commands = [call.args[0] for call in connection.send_command.call_args_list]
    assert commands == ["show version", "show ip interface brief", "show interfaces status"]
    assert all(command.startswith("show ") for command in commands)
    connection.send_config_set.assert_not_called()


@mock.patch("src.device_info.connect")
def test_collect_passes_credentials_and_timeout_to_connect(mock_connect):
    mock_connect.return_value = fake_connection(router_outputs())

    collect_device_info(R1, CREDENTIALS, timeout=7)

    mock_connect.assert_called_once_with(R1, CREDENTIALS, 7)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SSHAuthenticationError("SSH authentication failed for 192.168.100.11"), "auth_failed"),
        (SSHTimeoutError("SSH connection to 192.168.100.11 timed out"), "timeout"),
    ],
)
@mock.patch("src.device_info.connect")
def test_collect_connection_failures(mock_connect, error, status):
    mock_connect.side_effect = error

    result = collect_device_info(R1, CREDENTIALS)

    assert result["ssh_status"] == status
    assert result["error"] == str(error)
    assert result["device_hostname"] is None
    assert result["ios_version"] is None
    assert result["interface_count"] is None
    assert result["checked_at"]


@mock.patch("src.device_info.connect")
def test_collect_required_command_unsupported(mock_connect):
    outputs = router_outputs()
    outputs["show ip interface brief"] = load_fixture("invalid_input.txt")
    connection = fake_connection(outputs)
    mock_connect.return_value = connection

    result = collect_device_info(R1, CREDENTIALS)

    assert result["ssh_status"] == "failed"
    assert "'show ip interface brief' is not supported" in result["error"]
    connection.disconnect.assert_called_once()


@mock.patch("src.device_info.connect")
def test_collect_keeps_partial_data_when_a_later_command_fails(mock_connect):
    connection = fake_connection(router_outputs())
    connection.send_command.side_effect = [load_fixture("show_version_iosv.txt"), OSError("Socket is closed")]
    mock_connect.return_value = connection

    result = collect_device_info(R1, CREDENTIALS)

    assert result["ssh_status"] == "failed"
    assert result["error"] == "collection failed: Socket is closed"
    assert result["ios_version"] == "15.9(3)M6"   # collected before the failure
    assert result["interface_count"] is None       # never collected
    connection.disconnect.assert_called_once()


@mock.patch("src.device_info.connect")
def test_collect_does_not_invent_values_for_unparseable_output(mock_connect):
    outputs = router_outputs()
    outputs["show version"] = "unexpected output\n"
    mock_connect.return_value = fake_connection(outputs)

    result = collect_device_info(R1, CREDENTIALS)

    assert result["ssh_status"] == "success"
    assert result["device_hostname"] is None
    assert result["ios_version"] is None
    assert result["uptime"] is None


@mock.patch("src.device_info.connect")
def test_result_never_contains_password(mock_connect):
    mock_connect.return_value = fake_connection(router_outputs())

    result = collect_device_info(R1, CREDENTIALS)

    assert "Test-Pass-123" not in str(result)


# --- multi-device workflow --------------------------------------------------

@mock.patch("src.device_info.connect", side_effect=fake_connect())
def test_run_all_devices_success(mock_connect, inventory_file, lab_credentials):
    results = run_device_collection(inventory_file)

    assert [result["inventory_hostname"] for result in results] == ["R1", "R2", "SW1"]
    assert [result["device_hostname"] for result in results] == ["R1", "R2", "SW1"]
    assert all(result["ssh_status"] == "success" for result in results)
    assert summarize_collection(results) == {"total": 3, "succeeded": 3, "failed": 0}
    assert mock_connect.call_count == 3
    assert mock_connect.call_args.args[1] == lab_credentials


@mock.patch(
    "src.device_info.connect",
    side_effect=fake_connect({"192.168.100.12": SSHAuthenticationError("SSH authentication failed for 192.168.100.12")}),
)
def test_run_mixed_success_and_failure(mock_connect, inventory_file, lab_credentials):
    results = run_device_collection(inventory_file)

    status = {result["inventory_hostname"]: result["ssh_status"] for result in results}
    assert status == {"R1": "success", "R2": "auth_failed", "SW1": "success"}
    assert summarize_collection(results) == {"total": 3, "succeeded": 2, "failed": 1}


@mock.patch("src.device_info.connect", side_effect=fake_connect({"192.168.100.11": RuntimeError("unexpected")}))
def test_one_device_failure_does_not_stop_run(mock_connect, inventory_file, lab_credentials):
    results = run_device_collection(inventory_file)

    assert len(results) == 3
    assert results[0]["ssh_status"] == "failed"
    assert results[0]["error"] == "collection failed: unexpected"
    assert results[1]["ssh_status"] == "success"
    assert results[2]["ssh_status"] == "success"


@mock.patch("src.device_info.connect")
def test_run_missing_credentials_contacts_no_devices(mock_connect, inventory_file, no_credentials):
    with pytest.raises(CredentialsError):
        run_device_collection(inventory_file)
    mock_connect.assert_not_called()


@mock.patch("src.device_info.connect")
def test_run_missing_inventory_contacts_no_devices(mock_connect, tmp_path, lab_credentials):
    with pytest.raises(InventoryError, match="not found"):
        run_device_collection(tmp_path / "missing.yaml")
    mock_connect.assert_not_called()


def test_summarize_collection_empty():
    assert summarize_collection([]) == {"total": 0, "succeeded": 0, "failed": 0}


def test_format_summary():
    assert format_summary({"total": 3, "succeeded": 2, "failed": 1}).splitlines() == [
        "Total devices: 3",
        "Collected:     2",
        "Failed:        1",
    ]


@mock.patch("src.device_info.connect", side_effect=fake_connect({"192.168.100.12": SSHTimeoutError("SSH connection to 192.168.100.12 timed out")}))
def test_format_results_table(mock_connect, inventory_file, lab_credentials):
    lines = format_results_table(run_device_collection(inventory_file)).splitlines()

    assert lines[0].startswith("HOSTNAME")
    assert "success" in lines[2] and "15.9(3)M6" in lines[2] and "3/5" in lines[2]
    assert "timeout" in lines[3] and "timed out" in lines[3]
    assert lines[3].count(" - ") >= 3   # missing values are shown as "-"


# --- CLI and CSV report -----------------------------------------------------

def read_csv(path):
    with open(path, newline="", encoding="utf-8") as report_file:
        return list(csv.DictReader(report_file))


@mock.patch("src.device_info.connect", side_effect=fake_connect())
def test_main_all_success_writes_report(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    report = tmp_path / "reports" / "device_info_report.csv"

    exit_code = main(["--inventory", str(inventory_file), "--report", str(report)])

    output = capsys.readouterr().out
    rows = read_csv(report)
    assert exit_code == 0
    assert "Total devices: 3" in output
    assert "Collected:     3" in output
    assert f"Report saved to: {report}" in output
    assert list(rows[0]) == REPORT_FIELDS
    assert [row["inventory_hostname"] for row in rows] == ["R1", "R2", "SW1"]
    assert rows[0]["ios_version"] == "15.9(3)M6"
    assert rows[0]["interfaces_up"] == "3"
    assert rows[0]["unsupported_commands"] == "show interfaces status"
    assert rows[2]["switchport_summary"] == "4 ports: 2 connected, 1 notconnect, 1 disabled"
    assert rows[2]["unsupported_commands"] == ""


@mock.patch(
    "src.device_info.connect",
    side_effect=fake_connect({"192.168.100.12": SSHAuthenticationError("SSH authentication failed for 192.168.100.12")}),
)
def test_main_mixed_results_report(mock_connect, inventory_file, tmp_path, lab_credentials):
    report = tmp_path / "device_info_report.csv"

    exit_code = main(["--inventory", str(inventory_file), "--report", str(report)])

    rows = read_csv(report)
    assert exit_code == 1
    assert [row["ssh_status"] for row in rows] == ["success", "auth_failed", "success"]
    assert rows[1]["ios_version"] == ""
    assert rows[1]["error"] == "SSH authentication failed for 192.168.100.12"
    assert "Test-Pass-123" not in report.read_text(encoding="utf-8")


@mock.patch("src.device_info.connect")
def test_main_missing_credentials(mock_connect, inventory_file, tmp_path, no_credentials, capsys):
    report = tmp_path / "device_info_report.csv"

    exit_code = main(["--inventory", str(inventory_file), "--report", str(report)])

    assert exit_code == 2
    assert "NETWORK_USERNAME" in capsys.readouterr().err
    assert not report.exists()
    mock_connect.assert_not_called()


@mock.patch("src.device_info.connect")
def test_main_missing_inventory(mock_connect, tmp_path, lab_credentials, capsys):
    exit_code = main(["--inventory", str(tmp_path / "missing.yaml"), "--report", str(tmp_path / "r.csv")])

    assert exit_code == 2
    assert "Inventory file not found" in capsys.readouterr().err
    mock_connect.assert_not_called()


@mock.patch("src.device_info.write_csv_report", side_effect=PermissionError("file is open in another program"))
@mock.patch("src.device_info.connect", side_effect=fake_connect())
def test_main_report_write_error(mock_connect, mock_write, inventory_file, lab_credentials, capsys):
    exit_code = main(["--inventory", str(inventory_file)])

    assert exit_code == 2
    assert "could not write report" in capsys.readouterr().err


@mock.patch("src.device_info.connect", side_effect=fake_connect())
def test_main_passes_timeout(mock_connect, inventory_file, tmp_path, lab_credentials):
    main(["--inventory", str(inventory_file), "--report", str(tmp_path / "r.csv"), "--timeout", "20"])

    assert mock_connect.call_args.args[2] == 20


def test_main_rejects_invalid_timeout():
    with pytest.raises(SystemExit) as exc_info:
        main(["--timeout", "0"])
    assert exc_info.value.code == 2


def test_default_report_location():
    assert DEFAULT_REPORT_PATH.parts[-3:] == ("data", "reports", "device_info_report.csv")


# --- dry run (no SSH) -------------------------------------------------------

@mock.patch("src.device_info.connect")
def test_dry_run_with_credentials(mock_connect, inventory_file, lab_credentials, capsys):
    exit_code = main(["--dry-run", "--inventory", str(inventory_file)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "No SSH connections will be opened" in output
    assert "Inventory OK: 3 device(s)" in output
    assert "192.168.100.21" in output
    assert "Credentials OK" in output
    assert "Test-Pass-123" not in output
    assert "labuser" not in output
    mock_connect.assert_not_called()


@mock.patch("src.device_info.connect")
def test_dry_run_missing_credentials(mock_connect, inventory_file, no_credentials, capsys):
    exit_code = main(["--dry-run", "--inventory", str(inventory_file)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Inventory OK" in captured.out
    assert "Missing environment variable(s): NETWORK_USERNAME, NETWORK_PASSWORD" in captured.err
    mock_connect.assert_not_called()


def test_dry_run_invalid_inventory(tmp_path, lab_credentials, capsys):
    path = tmp_path / "devices.yaml"
    path.write_text("devices: []\n", encoding="utf-8")

    exit_code = main(["--dry-run", "--inventory", str(path)])

    assert exit_code == 2
    assert "non-empty list" in capsys.readouterr().err
