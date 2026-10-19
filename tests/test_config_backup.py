"""Tests for src/config_backup.py.

The SSH layer is mocked in every test: src.config_backup.connect is replaced
with fake connections built from Netmiko's real CiscoIosSSH class (autospec),
so only real Netmiko methods can be called and every call can be checked.
Backups and reports are written to pytest's tmp_path only.
No SSH session, network access, Cisco device or GNS3 lab is used.
"""

import csv
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from netmiko.cisco import CiscoIosSSH

from src.config_backup import (
    BACKUP_DIR,
    COMMAND_READ_TIMEOUT,
    DEFAULT_REPORT_PATH,
    FAILED,
    REPORT_FIELDS,
    SUCCESS,
    BackupError,
    backup_device,
    build_backup_filename,
    check_running_config,
    format_results_table,
    format_summary,
    get_running_config,
    main,
    run_backup,
    sanitize_hostname,
    save_backup,
    summarize_backups,
)
from src.inventory import InventoryError
from src.ssh_client import CredentialsError, SSHAuthenticationError, SSHConnectionError, SSHTimeoutError
from tests.conftest import load_fixture

R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}
CREDENTIALS = {"username": "labuser", "password": "Test-Pass-123"}
FIXED_TIME = datetime(2026, 10, 19, 12, 55, 0)

RUNNING_CONFIG_R1 = load_fixture("show_running_config_r1.txt")

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


def running_config(hostname):
    return RUNNING_CONFIG_R1.replace("hostname R1", f"hostname {hostname}")


def fake_connection(output=RUNNING_CONFIG_R1):
    """A fake Netmiko Cisco IOS connection. `output` is what 'show running-config' returns
    (or an exception to raise)."""
    connection = mock.create_autospec(CiscoIosSSH, instance=True)
    if isinstance(output, Exception):
        connection.send_command.side_effect = output
    else:
        connection.send_command.return_value = output
    return connection


LAB_HOSTS = {"192.168.100.11": "R1", "192.168.100.12": "R2", "192.168.100.21": "SW1"}


def fake_connect(failures=None, connections=None):
    """Return a fake connect(). Hosts in `failures` raise the given exception.
    Every connection created is appended to `connections` so tests can inspect it."""
    failures = failures or {}

    def _connect(device, credentials, timeout=10):
        if device["host"] in failures:
            raise failures[device["host"]]
        connection = fake_connection(running_config(LAB_HOSTS[device["host"]]))
        if connections is not None:
            connections.append(connection)
        return connection
    return _connect


def all_failures(error_class=SSHTimeoutError):
    return {host: error_class(f"SSH connection to {host} timed out") for host in LAB_HOSTS}


@pytest.fixture
def inventory_file(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text(INVENTORY, encoding="utf-8")
    return path


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


def cfg_files(folder):
    return sorted(path.name for path in Path(folder).glob("*.cfg")) if Path(folder).exists() else []


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as report_file:
        return list(csv.DictReader(report_file))


# --- filenames --------------------------------------------------------------

@pytest.mark.parametrize("hostname", ["R1", "SW1", "CORE-RTR_01", "r1.lab.local"])
def test_sanitize_normal_hostname_unchanged(hostname):
    assert sanitize_hostname(hostname) == hostname


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("core/rtr:1", "core_rtr_1"),
        ("SW 1*?<>|", "SW_1"),
        ("..\\..\\windows", "windows"),
        ("../../etc/passwd", "etc_passwd"),
        ("...", "device"),
        ("", "device"),
        (None, "None"),
    ],
)
def test_sanitize_unsafe_hostname(hostname, expected):
    safe = sanitize_hostname(hostname)

    assert safe == expected
    assert "/" not in safe and "\\" not in safe and ".." not in safe


def test_build_backup_filename():
    assert build_backup_filename("R1", FIXED_TIME) == "R1_20261019_125500.cfg"


def test_build_backup_filename_zero_padded_timestamp():
    assert build_backup_filename("SW1", datetime(2026, 1, 2, 3, 4, 5)) == "SW1_20260102_030405.cfg"


def test_build_backup_filename_with_counter():
    assert build_backup_filename("R1", FIXED_TIME, counter=2) == "R1_20261019_125500_2.cfg"


def test_build_backup_filename_sanitizes_hostname():
    assert build_backup_filename("core/rtr", FIXED_TIME) == "core_rtr_20261019_125500.cfg"


# --- saving -----------------------------------------------------------------

def test_save_backup_writes_exact_content(backup_dir):
    path, size = save_backup(RUNNING_CONFIG_R1, backup_dir, "R1", FIXED_TIME)

    assert path == backup_dir / "R1_20261019_125500.cfg"
    assert path.read_bytes() == RUNNING_CONFIG_R1.encode("utf-8")
    assert size == len(RUNNING_CONFIG_R1.encode("utf-8"))


def test_save_backup_creates_missing_folder(tmp_path):
    path, _ = save_backup(RUNNING_CONFIG_R1, tmp_path / "new" / "backups", "R1", FIXED_TIME)

    assert path.is_file()


def test_save_backup_never_overwrites_existing_backup(backup_dir):
    first, _ = save_backup("config one\nend\n", backup_dir, "R1", FIXED_TIME)
    second, _ = save_backup("config two\nend\n", backup_dir, "R1", FIXED_TIME)
    third, _ = save_backup("config three\nend\n", backup_dir, "R1", FIXED_TIME)

    assert [first.name, second.name, third.name] == [
        "R1_20261019_125500.cfg",
        "R1_20261019_125500_1.cfg",
        "R1_20261019_125500_2.cfg",
    ]
    assert first.read_text(encoding="utf-8") == "config one\nend\n"
    assert second.read_text(encoding="utf-8") == "config two\nend\n"


def test_save_backup_keeps_non_ascii_banner_text(backup_dir):
    text = "banner motd ^C\nZugang nur für Laborbenutzer\n^C\nend\n"

    path, size = save_backup(text, backup_dir, "R1", FIXED_TIME)

    assert path.read_text(encoding="utf-8") == text
    assert size == len(text.encode("utf-8"))


@mock.patch("src.config_backup._write_new_file", side_effect=PermissionError("Access is denied"))
def test_save_backup_write_failure(mock_write, backup_dir):
    with pytest.raises(BackupError, match="could not write backup file .*Access is denied"):
        save_backup(RUNNING_CONFIG_R1, backup_dir, "R1", FIXED_TIME)


def test_save_backup_folder_is_a_file(tmp_path):
    not_a_folder = tmp_path / "backups"
    not_a_folder.write_text("x", encoding="utf-8")

    with pytest.raises(BackupError, match="could not write backup file"):
        save_backup(RUNNING_CONFIG_R1, not_a_folder, "R1", FIXED_TIME)


def test_save_backup_removes_half_written_file(backup_dir, monkeypatch):
    real_open = Path.open

    class DiskFullFile:
        """Wraps a real file but fails on write, like a full disk."""
        def __init__(self, real_file):
            self.real_file = real_file

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.real_file.close()

        def write(self, text):
            raise OSError("No space left on device")

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: DiskFullFile(real_open(self, *args, **kwargs)))

    with pytest.raises(BackupError, match="No space left on device"):
        save_backup(RUNNING_CONFIG_R1, backup_dir, "R1", FIXED_TIME)
    assert cfg_files(backup_dir) == []


# --- checking device output -------------------------------------------------

def test_check_running_config_accepts_valid_config():
    check_running_config(RUNNING_CONFIG_R1)  # must not raise


def test_check_running_config_ignores_percent_sign_inside_config():
    # The fixture's login banner contains "% Authorized access only ... %".
    assert "% Authorized access only" in RUNNING_CONFIG_R1
    check_running_config(RUNNING_CONFIG_R1)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "empty output"),
        ("   \n\n", "empty output"),
        (None, "empty output"),
        (load_fixture("invalid_input.txt"), "% Invalid input detected"),
        ("% Authorization failed.\n", "% Authorization failed"),
        ("Command authorization failed.\n", "Command authorization failed"),
        ("% Incomplete command.\n", "% Incomplete command"),
        ("% Bad IP address or host name\n", "% Bad IP address"),
        (RUNNING_CONFIG_R1.rsplit("end", 1)[0], "incomplete"),
    ],
)
def test_check_running_config_rejects_bad_output(output, message):
    with pytest.raises(BackupError, match=message):
        check_running_config(output)


def test_get_running_config_sends_show_running_config():
    connection = fake_connection()

    assert get_running_config(connection) == RUNNING_CONFIG_R1
    connection.send_command.assert_called_once_with("show running-config", read_timeout=COMMAND_READ_TIMEOUT)


# --- one device -------------------------------------------------------------

@mock.patch("src.config_backup.datetime")
@mock.patch("src.config_backup.connect")
def test_backup_device_success(mock_connect, mock_datetime, backup_dir):
    mock_datetime.now.return_value = FIXED_TIME
    connection = fake_connection()
    mock_connect.return_value = connection

    result = backup_device(R1, CREDENTIALS, backup_dir)

    expected_path = backup_dir / "R1_20261019_125500.cfg"
    assert result == {
        "hostname": "R1",
        "host": "192.168.100.11",
        "role": "core-router",
        "status": SUCCESS,
        "backup_path": str(expected_path),
        "bytes_written": len(RUNNING_CONFIG_R1.encode("utf-8")),
        "error": None,
        "backed_up_at": "2026-10-19T12:55:00",
    }
    assert expected_path.read_text(encoding="utf-8") == RUNNING_CONFIG_R1
    assert list(result) == REPORT_FIELDS
    connection.send_command.assert_called_once_with("show running-config", read_timeout=COMMAND_READ_TIMEOUT)
    connection.disconnect.assert_called_once()
    mock_connect.assert_called_once_with(R1, CREDENTIALS, 10)


@mock.patch("src.config_backup.connect")
def test_backup_preserves_device_config_unchanged(mock_connect, backup_dir):
    mock_connect.return_value = fake_connection()

    result = backup_device(R1, CREDENTIALS, backup_dir)

    saved = Path(result["backup_path"]).read_text(encoding="utf-8")
    # Device-local lines such as username/secret are kept exactly as the device returned them.
    assert "username admin privilege 15 secret 9" in saved
    assert saved == RUNNING_CONFIG_R1


@mock.patch("src.config_backup.connect")
def test_backup_file_contains_no_credentials_or_debug_info(mock_connect, backup_dir):
    mock_connect.return_value = fake_connection()

    result = backup_device(R1, CREDENTIALS, backup_dir)

    saved = Path(result["backup_path"]).read_text(encoding="utf-8")
    assert "Test-Pass-123" not in saved
    assert "labuser" not in saved
    assert "conn_timeout" not in saved
    assert "Traceback" not in saved


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (SSHAuthenticationError("SSH authentication failed for 192.168.100.11"), "authentication failed"),
        (SSHTimeoutError("SSH connection to 192.168.100.11 timed out"), "timed out"),
        (SSHConnectionError("SSH connection to 192.168.100.11 failed: connection refused"), "connection refused"),
    ],
)
@mock.patch("src.config_backup.connect")
def test_backup_device_connection_failures(mock_connect, error, message, backup_dir):
    mock_connect.side_effect = error

    result = backup_device(R1, CREDENTIALS, backup_dir)

    assert result["status"] == FAILED
    assert message in result["error"]
    assert result["backup_path"] is None
    assert result["bytes_written"] is None
    assert result["backed_up_at"]
    assert cfg_files(backup_dir) == []


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "empty output"),
        (load_fixture("invalid_input.txt"), "% Invalid input detected"),
        ("% Authorization failed.\n", "% Authorization failed"),
        (RUNNING_CONFIG_R1[:200], "incomplete"),
    ],
)
@mock.patch("src.config_backup.connect")
def test_backup_device_bad_output_is_not_saved(mock_connect, output, message, backup_dir):
    connection = fake_connection(output)
    mock_connect.return_value = connection

    result = backup_device(R1, CREDENTIALS, backup_dir)

    assert result["status"] == FAILED
    assert message in result["error"]
    assert result["backup_path"] is None
    assert cfg_files(backup_dir) == []
    connection.disconnect.assert_called_once()


@mock.patch("src.config_backup.connect")
def test_backup_device_session_drops_during_command(mock_connect, backup_dir):
    connection = fake_connection(OSError("Socket is closed"))
    mock_connect.return_value = connection

    result = backup_device(R1, CREDENTIALS, backup_dir)

    assert result["status"] == FAILED
    assert result["error"] == "backup failed: Socket is closed"
    connection.disconnect.assert_called_once()


@mock.patch("src.config_backup._write_new_file", side_effect=PermissionError("Access is denied"))
@mock.patch("src.config_backup.connect")
def test_backup_device_write_failure(mock_connect, mock_write, backup_dir):
    connection = fake_connection()
    mock_connect.return_value = connection

    result = backup_device(R1, CREDENTIALS, backup_dir)

    assert result["status"] == FAILED
    assert "could not write backup file" in result["error"]
    assert result["bytes_written"] is None
    connection.disconnect.assert_called_once()


@mock.patch("src.config_backup.connect")
def test_backup_device_disconnect_error_does_not_change_result(mock_connect, backup_dir):
    connection = fake_connection()
    connection.disconnect.side_effect = OSError("already closed")
    mock_connect.return_value = connection

    result = backup_device(R1, CREDENTIALS, backup_dir)

    assert result["status"] == SUCCESS


# --- read-only safety -------------------------------------------------------

@mock.patch("src.config_backup.connect")
def test_backup_only_sends_read_only_show_command(mock_connect, backup_dir):
    connection = fake_connection()
    mock_connect.return_value = connection

    backup_device(R1, CREDENTIALS, backup_dir)

    commands = [call.args[0] for call in connection.send_command.call_args_list]
    assert commands == ["show running-config"]
    assert all(command.startswith("show ") for command in commands)


@mock.patch("src.config_backup.connect")
def test_backup_never_changes_the_device(mock_connect, backup_dir):
    connection = fake_connection()
    mock_connect.return_value = connection

    backup_device(R1, CREDENTIALS, backup_dir)

    connection.config_mode.assert_not_called()
    connection.send_config_set.assert_not_called()
    connection.send_config_from_file.assert_not_called()
    connection.save_config.assert_not_called()
    connection.send_command_timing.assert_not_called()
    connection.write_channel.assert_not_called()


def test_backup_device_cannot_reach_real_netmiko(backup_dir):
    # Without mocking connect(), the conftest.py safeguard must stop the real ConnectHandler.
    with pytest.raises(pytest.fail.Exception, match="Real SSH connection attempted"):
        backup_device(R1, CREDENTIALS, backup_dir)


# --- all devices ------------------------------------------------------------

def test_run_backup_all_successful(inventory_file, backup_dir, lab_credentials):
    connections = []
    with mock.patch("src.config_backup.connect", side_effect=fake_connect(connections=connections)) as mock_connect:
        results = run_backup(inventory_file, backup_dir)

    assert [result["status"] for result in results] == [SUCCESS, SUCCESS, SUCCESS]
    assert summarize_backups(results) == {"total": 3, "succeeded": 3, "failed": 0}
    files = cfg_files(backup_dir)
    assert len(files) == 3
    assert [name.split("_")[0] for name in files] == ["R1", "R2", "SW1"]
    assert "hostname SW1" in Path(results[2]["backup_path"]).read_text(encoding="utf-8")
    assert mock_connect.call_args.args[1] == lab_credentials
    assert all(conn.disconnect.call_count == 1 for conn in connections)
    for conn in connections:
        conn.send_config_set.assert_not_called()
        conn.config_mode.assert_not_called()


@mock.patch("src.config_backup.connect", side_effect=fake_connect(all_failures()))
def test_run_backup_all_failed(mock_connect, inventory_file, backup_dir, lab_credentials):
    results = run_backup(inventory_file, backup_dir)

    assert [result["status"] for result in results] == [FAILED, FAILED, FAILED]
    assert summarize_backups(results) == {"total": 3, "succeeded": 0, "failed": 3}
    assert cfg_files(backup_dir) == []


@mock.patch(
    "src.config_backup.connect",
    side_effect=fake_connect({"192.168.100.12": SSHAuthenticationError("SSH authentication failed for 192.168.100.12")}),
)
def test_run_backup_mixed(mock_connect, inventory_file, backup_dir, lab_credentials):
    results = run_backup(inventory_file, backup_dir)

    assert {r["hostname"]: r["status"] for r in results} == {"R1": SUCCESS, "R2": FAILED, "SW1": SUCCESS}
    assert summarize_backups(results) == {"total": 3, "succeeded": 2, "failed": 1}
    assert [name.split("_")[0] for name in cfg_files(backup_dir)] == ["R1", "SW1"]


@mock.patch("src.config_backup.connect", side_effect=fake_connect({"192.168.100.11": RuntimeError("unexpected")}))
def test_one_failure_does_not_stop_other_devices(mock_connect, inventory_file, backup_dir, lab_credentials):
    results = run_backup(inventory_file, backup_dir)

    assert len(results) == 3
    assert results[0]["status"] == FAILED
    assert results[0]["error"] == "backup failed: unexpected"
    assert results[1]["status"] == SUCCESS
    assert results[2]["status"] == SUCCESS


@mock.patch("src.config_backup.datetime")
@mock.patch("src.config_backup.connect", side_effect=fake_connect())
def test_run_backup_twice_in_same_second_keeps_both(mock_connect, mock_datetime, inventory_file, backup_dir, lab_credentials):
    mock_datetime.now.return_value = FIXED_TIME

    run_backup(inventory_file, backup_dir)
    run_backup(inventory_file, backup_dir)

    assert "R1_20261019_125500.cfg" in cfg_files(backup_dir)
    assert "R1_20261019_125500_1.cfg" in cfg_files(backup_dir)
    assert len(cfg_files(backup_dir)) == 6


@mock.patch("src.config_backup.connect")
def test_run_backup_missing_credentials_contacts_no_devices(mock_connect, inventory_file, backup_dir, no_credentials):
    with pytest.raises(CredentialsError):
        run_backup(inventory_file, backup_dir)
    mock_connect.assert_not_called()
    assert not backup_dir.exists()


@mock.patch("src.config_backup.connect")
def test_run_backup_missing_inventory_contacts_no_devices(mock_connect, tmp_path, lab_credentials):
    with pytest.raises(InventoryError, match="not found"):
        run_backup(tmp_path / "missing.yaml", tmp_path / "backups")
    mock_connect.assert_not_called()


def test_summarize_backups_empty():
    assert summarize_backups([]) == {"total": 0, "succeeded": 0, "failed": 0}


def test_format_summary():
    assert format_summary({"total": 3, "succeeded": 2, "failed": 1}).splitlines() == [
        "Total devices: 3",
        "Successful backups: 2",
        "Failed backups: 1",
    ]


@mock.patch("src.config_backup.datetime")
@mock.patch(
    "src.config_backup.connect",
    side_effect=fake_connect({"192.168.100.12": SSHTimeoutError("SSH connection to 192.168.100.12 timed out")}),
)
def test_format_results_table(mock_connect, mock_datetime, inventory_file, backup_dir, lab_credentials):
    mock_datetime.now.return_value = FIXED_TIME

    lines = format_results_table(run_backup(inventory_file, backup_dir)).splitlines()

    assert lines[0].split() == ["HOSTNAME", "HOST", "ROLE", "STATUS", "BACKUP", "ERROR"]
    assert "SUCCESS" in lines[2] and "R1_20261019_125500.cfg" in lines[2]
    assert str(backup_dir) not in lines[2]   # only the file name is shown
    assert "FAILED" in lines[3] and " - " in lines[3] and "timed out" in lines[3]


# --- CLI and CSV report -----------------------------------------------------

def cli_args(inventory_file, backup_dir, report):
    return ["--inventory", str(inventory_file), "--backup-dir", str(backup_dir), "--report", str(report)]


@mock.patch("src.config_backup.connect", side_effect=fake_connect())
def test_main_all_successful_writes_report(mock_connect, inventory_file, backup_dir, tmp_path, lab_credentials, capsys):
    report = tmp_path / "reports" / "config_backup_report.csv"

    exit_code = main(cli_args(inventory_file, backup_dir, report))

    output = capsys.readouterr().out
    rows = read_csv(report)
    assert exit_code == 0
    assert "Total devices: 3" in output
    assert "Successful backups: 3" in output
    assert "Failed backups: 0" in output
    assert f"Report saved to: {report}" in output
    assert list(rows[0]) == REPORT_FIELDS
    assert [row["status"] for row in rows] == [SUCCESS, SUCCESS, SUCCESS]
    assert Path(rows[0]["backup_path"]).is_file()
    assert int(rows[0]["bytes_written"]) == Path(rows[0]["backup_path"]).stat().st_size


@mock.patch(
    "src.config_backup.connect",
    side_effect=fake_connect({"192.168.100.12": SSHAuthenticationError("SSH authentication failed for 192.168.100.12")}),
)
def test_main_mixed_results(mock_connect, inventory_file, backup_dir, tmp_path, lab_credentials, capsys):
    report = tmp_path / "config_backup_report.csv"

    exit_code = main(cli_args(inventory_file, backup_dir, report))

    rows = read_csv(report)
    output = capsys.readouterr().out
    assert exit_code == 1
    assert [row["status"] for row in rows] == [SUCCESS, FAILED, SUCCESS]
    assert rows[1]["backup_path"] == ""
    assert rows[1]["bytes_written"] == ""
    assert rows[1]["error"] == "SSH authentication failed for 192.168.100.12"
    assert "Failed backups: 1" in output


@mock.patch("src.config_backup.connect", side_effect=fake_connect())
def test_credentials_never_appear_in_output_report_or_filenames(mock_connect, inventory_file, backup_dir, tmp_path, lab_credentials, capsys):
    report = tmp_path / "config_backup_report.csv"

    main(cli_args(inventory_file, backup_dir, report))

    output = capsys.readouterr()
    report_text = report.read_text(encoding="utf-8")
    for secret in ("Test-Pass-123", "labuser"):
        assert secret not in output.out
        assert secret not in output.err
        assert secret not in report_text
        assert all(secret not in name for name in cfg_files(backup_dir))


@mock.patch("src.config_backup.connect")
def test_main_missing_credentials(mock_connect, inventory_file, backup_dir, tmp_path, no_credentials, capsys):
    report = tmp_path / "config_backup_report.csv"

    exit_code = main(cli_args(inventory_file, backup_dir, report))

    assert exit_code == 2
    assert "Missing environment variable(s): NETWORK_USERNAME, NETWORK_PASSWORD" in capsys.readouterr().err
    mock_connect.assert_not_called()
    assert not report.exists()
    assert not backup_dir.exists()


@mock.patch("src.config_backup.connect")
def test_main_missing_inventory(mock_connect, tmp_path, lab_credentials, capsys):
    exit_code = main(cli_args(tmp_path / "missing.yaml", tmp_path / "backups", tmp_path / "r.csv"))

    assert exit_code == 2
    assert "Inventory file not found" in capsys.readouterr().err
    mock_connect.assert_not_called()


@mock.patch("src.config_backup.write_csv_report", side_effect=PermissionError("file is open in another program"))
@mock.patch("src.config_backup.connect", side_effect=fake_connect())
def test_main_report_write_error(mock_connect, mock_write, inventory_file, backup_dir, tmp_path, lab_credentials, capsys):
    exit_code = main(cli_args(inventory_file, backup_dir, tmp_path / "r.csv"))

    assert exit_code == 2
    assert "could not write report" in capsys.readouterr().err


@mock.patch("src.config_backup.connect", side_effect=fake_connect())
def test_main_passes_timeout(mock_connect, inventory_file, backup_dir, tmp_path, lab_credentials):
    main(cli_args(inventory_file, backup_dir, tmp_path / "r.csv") + ["--timeout", "25"])

    assert mock_connect.call_args.args[2] == 25


def test_main_rejects_invalid_timeout():
    with pytest.raises(SystemExit) as exc_info:
        main(["--timeout", "0"])
    assert exc_info.value.code == 2


def test_default_locations():
    assert BACKUP_DIR.parts[-2:] == ("data", "backups")
    assert DEFAULT_REPORT_PATH.parts[-3:] == ("data", "reports", "config_backup_report.csv")


# --- dry run (no SSH, no files) ---------------------------------------------

@mock.patch("src.config_backup.connect")
def test_dry_run_opens_no_ssh_and_writes_no_files(mock_connect, inventory_file, backup_dir, tmp_path, lab_credentials, capsys):
    report = tmp_path / "config_backup_report.csv"

    exit_code = main(["--dry-run"] + cli_args(inventory_file, backup_dir, report))

    output = capsys.readouterr().out
    assert exit_code == 0
    mock_connect.assert_not_called()
    assert not backup_dir.exists()
    assert not report.exists()
    assert "No SSH connections will be opened and no files will be written" in output
    assert "Inventory OK: 3 device(s)" in output
    assert f"Backup directory: {backup_dir}" in output
    assert "R1_YYYYMMDD_HHMMSS.cfg" in output
    assert "192.168.100.21" in output
    assert "Credentials OK" in output
    assert "Test-Pass-123" not in output
    assert "labuser" not in output


@mock.patch("src.config_backup.connect")
def test_dry_run_with_default_paths_writes_nothing(mock_connect, lab_credentials):
    # Uses the real inventory and the real data/backups path; the conftest safeguard
    # fails this test if anything is written to data/backups/ or data/reports/.
    assert main(["--dry-run"]) == 0
    mock_connect.assert_not_called()


@mock.patch("src.config_backup.connect")
def test_dry_run_missing_credentials(mock_connect, inventory_file, backup_dir, tmp_path, no_credentials, capsys):
    exit_code = main(["--dry-run"] + cli_args(inventory_file, backup_dir, tmp_path / "r.csv"))

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Inventory OK" in captured.out
    assert "Missing environment variable(s): NETWORK_USERNAME, NETWORK_PASSWORD" in captured.err
    mock_connect.assert_not_called()
    assert not backup_dir.exists()


def test_dry_run_invalid_inventory(tmp_path, lab_credentials, capsys):
    path = tmp_path / "devices.yaml"
    path.write_text("devices:\n  - hostname: R1\n", encoding="utf-8")

    exit_code = main(["--dry-run", "--inventory", str(path)])

    assert exit_code == 2
    assert "missing required field" in capsys.readouterr().err
