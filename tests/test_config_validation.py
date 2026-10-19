"""Tests for src/config_validation.py.

The SSH layer is mocked in every test: src.config_validation.connect is
replaced with fake Netmiko connections (autospec of CiscoIosSSH) that return
saved running-configs from tests/fixtures/. Reports go to pytest's tmp_path.
No SSH session, network access, Cisco device or GNS3 lab is used.
"""

import csv
from unittest import mock

import pytest

from src.config_backup import COMMAND_READ_TIMEOUT
from src.config_validation import (
    DEFAULT_EXPECTED_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_SUMMARY_PATH,
    ERROR,
    FAIL,
    PASS,
    REPORT_FIELDS,
    SUMMARY_FIELDS,
    ExpectedConfigError,
    check_hostname,
    check_interfaces,
    check_label,
    check_prohibited_lines,
    check_required_lines,
    config_lines,
    count_checks,
    find_unknown_devices,
    format_checks_table,
    format_summary_table,
    get_interface_block,
    load_expected_config,
    main,
    normalize_line,
    overall_status,
    run_validation,
    summarize_devices,
    validate_config_text,
    validate_device,
)
from src.inventory import InventoryError
from src.ssh_client import CredentialsError, SSHAuthenticationError, SSHConnectionError, SSHTimeoutError
from tests.conftest import assert_no_config_changes, fake_ios_connection, load_fixture

R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}
CREDENTIALS = {"username": "labuser", "password": "Test-Pass-123"}

R1_CONFIG = load_fixture("show_running_config_r1.txt")
R2_CONFIG = load_fixture("show_running_config_r2.txt")
SW1_CONFIG = load_fixture("show_running_config_sw1.txt")
LAB_CONFIGS = {"192.168.100.11": R1_CONFIG, "192.168.100.12": R2_CONFIG, "192.168.100.21": SW1_CONFIG}

EXPECTED = load_expected_config(DEFAULT_EXPECTED_PATH)   # the real project rules
R1_RULES = EXPECTED["R1"]

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


def fake_connect(failures=None, configs=None, connections=None):
    """Return a fake connect(). Hosts in `failures` raise; `configs` overrides a host's running-config."""
    failures = failures or {}
    configs = {**LAB_CONFIGS, **(configs or {})}

    def _connect(device, credentials, timeout=10):
        if device["host"] in failures:
            raise failures[device["host"]]
        connection = fake_ios_connection(configs[device["host"]])
        if connections is not None:
            connections.append(connection)
        return connection
    return _connect


def write_file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def inventory_file(tmp_path):
    return write_file(tmp_path, "devices.yaml", INVENTORY)


def statuses(checks):
    return [check["status"] for check in checks]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as report_file:
        return list(csv.DictReader(report_file))


# --- loading expected rules -------------------------------------------------

def test_project_expected_config_is_valid():
    assert list(EXPECTED) == ["R1", "R2", "SW1"]
    assert [count_checks(rules) for rules in EXPECTED.values()] == [11, 11, 11]


def test_load_valid_expected_config(tmp_path):
    path = write_file(tmp_path, "expected.yaml", """
devices:
  R1:
    hostname: R1
    required_config_lines:
      - "ip ssh version 2"
    prohibited_config_lines:
      - "ip http server"
    interfaces:
      GigabitEthernet0/0:
        - "ip address 192.168.100.11 255.255.255.0"
""")
    rules = load_expected_config(path)

    assert rules["R1"]["hostname"] == "R1"
    assert count_checks(rules["R1"]) == 5


def test_empty_rule_lists_are_allowed(tmp_path):
    path = write_file(tmp_path, "expected.yaml", """
devices:
  R1:
    hostname: R1
    required_config_lines: []
    prohibited_config_lines:
    interfaces:
      GigabitEthernet0/0: []
""")
    rules = load_expected_config(path)

    assert count_checks(rules["R1"]) == 2   # hostname + interface exists


def test_missing_expected_config_file(tmp_path):
    with pytest.raises(ExpectedConfigError, match="not found"):
        load_expected_config(tmp_path / "missing.yaml")


def test_malformed_expected_config_yaml(tmp_path):
    path = write_file(tmp_path, "expected.yaml", "devices:\n  R1: [hostname: R1\n  bad: : :")

    with pytest.raises(ExpectedConfigError, match="not valid YAML"):
        load_expected_config(path)


@pytest.mark.parametrize("content", ["", "devices:\n", "devices: []\n", "devices: {}\n", "routers:\n  R1: {}\n", "- R1\n"])
def test_missing_devices_mapping(tmp_path, content):
    with pytest.raises(ExpectedConfigError, match="non-empty 'devices' mapping"):
        load_expected_config(write_file(tmp_path, "expected.yaml", content))


@pytest.mark.parametrize(
    ("device_rules", "message"),
    [
        ("  R1: [hostname R1]\n", "must be a mapping"),
        ("  R1:\n    required_lines: [ip ssh version 2]\n", "Unknown rule type.*required_lines"),
        ("  R1:\n    hostname: 123\n", "'hostname' for R1 must be non-empty text"),
        ("  R1:\n    hostname: ''\n", "'hostname' for R1 must be non-empty text"),
        ("  R1:\n    required_config_lines: ip ssh version 2\n", "must be a list"),
        ("  R1:\n    required_config_lines: [10]\n", "invalid line: 10"),
        ("  R1:\n    prohibited_config_lines: ['']\n", "invalid line"),
        ("  R1:\n    required_config_lines: [null]\n", "invalid line: None"),
        ("  R1:\n    interfaces: [GigabitEthernet0/0]\n", "must map interface names"),
        ("  R1:\n    interfaces:\n      GigabitEthernet0/0: ip address 1.1.1.1 255.0.0.0\n", "interfaces/GigabitEthernet0/0.*must be a list"),
        ("  R1:\n    required_config_lines: [ip ssh version 2, ' ip ssh  version 2 ']\n", "same line more than once"),
        ("  R1:\n    required_config_lines: [ip http server]\n    prohibited_config_lines: [ip http server]\n", "both required and prohibited"),
        ("  R1: {}\n", "No validation rules defined for R1"),
        ("  R1:\n    required_config_lines: []\n", "No validation rules defined for R1"),
    ],
)
def test_invalid_device_rules(tmp_path, device_rules, message):
    path = write_file(tmp_path, "expected.yaml", "devices:\n" + device_rules)

    with pytest.raises(ExpectedConfigError, match=message):
        load_expected_config(path)


def test_find_unknown_devices():
    devices = [R1]
    expected = {"R1": R1_RULES, "R9": R1_RULES}

    assert find_unknown_devices(devices, expected) == ["R9"]


# --- normalization ----------------------------------------------------------

@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("  ip ssh version 2  ", "ip ssh version 2"),
        ("\tip  ssh\tversion   2", "ip ssh version 2"),
        (" ip address 192.168.100.11 255.255.255.0\r", "ip address 192.168.100.11 255.255.255.0"),
        ("Hostname R1", "Hostname R1"),   # case is preserved
    ],
)
def test_normalize_line(line, expected):
    assert normalize_line(line) == expected


def test_config_lines_drops_blank_and_separator_lines_and_indentation():
    text = "!\nhostname R1\n\n   \ninterface Loopback0\n ip address 1.1.1.1 255.255.255.255\n!\nend\n"

    assert config_lines(text) == ["hostname R1", "interface Loopback0", "ip address 1.1.1.1 255.255.255.255", "end"]


def test_config_lines_windows_and_linux_line_endings_match():
    assert config_lines(R1_CONFIG.replace("\n", "\r\n")) == config_lines(R1_CONFIG)


def test_get_interface_block():
    block = get_interface_block(R1_CONFIG, "GigabitEthernet0/0")

    assert block == [
        "description MGMT - GNS3 lab management network",
        "ip address 192.168.100.11 255.255.255.0",
        "duplex auto",
        "speed auto",
        "media-type rj45",
    ]


def test_get_interface_block_missing_interface():
    assert get_interface_block(R1_CONFIG, "GigabitEthernet0/9") is None


def test_get_interface_block_exact_name_only():
    text = "interface GigabitEthernet0/10\n ip address 10.0.0.1 255.255.255.0\n!\n"

    assert get_interface_block(text, "GigabitEthernet0/1") is None


def test_get_interface_block_stops_at_next_section():
    text = "interface Vlan1\n ip address 192.168.100.21 255.255.255.0\nip default-gateway 192.168.100.1\n"

    assert get_interface_block(text, "Vlan1") == ["ip address 192.168.100.21 255.255.255.0"]


def test_get_interface_block_without_sub_lines():
    assert get_interface_block("interface Loopback1\n!\nend\n", "Loopback1") == []


def test_get_interface_block_windows_line_endings():
    block = get_interface_block(R1_CONFIG.replace("\n", "\r\n"), "GigabitEthernet0/1")

    assert "ip address 10.0.12.1 255.255.255.252" in block


# --- hostname ---------------------------------------------------------------

def test_hostname_correct():
    check = check_hostname(config_lines(R1_CONFIG), "R1")

    assert check["status"] == PASS
    assert (check["expected"], check["actual"]) == ("R1", "R1")


def test_hostname_incorrect():
    check = check_hostname(config_lines(R1_CONFIG.replace("hostname R1", "hostname R1-OLD")), "R1")

    assert check["status"] == FAIL
    assert check["actual"] == "R1-OLD"
    assert check["message"] == "hostname is R1-OLD, expected R1"


def test_hostname_missing():
    check = check_hostname(config_lines(R1_CONFIG.replace("hostname R1\n", "")), "R1")

    assert check["status"] == FAIL
    assert check["actual"] == "missing"
    assert check["message"] == "no hostname line found"


# --- required lines ---------------------------------------------------------

def test_required_lines_present():
    checks = check_required_lines(config_lines(R1_CONFIG), ["ip ssh version 2", "router ospf 1"])

    assert statuses(checks) == [PASS, PASS]
    assert checks[0]["check_type"] == "required_config"
    assert checks[0]["check_name"] == "ip ssh version 2"


def test_required_line_missing():
    checks = check_required_lines(config_lines(R1_CONFIG), ["ip domain lookup"])

    assert checks[0]["status"] == FAIL
    assert (checks[0]["expected"], checks[0]["actual"]) == ("present", "missing")
    assert checks[0]["message"] == "required line not found"


def test_required_line_duplicated_in_config_still_passes():
    lines = config_lines("logging buffered 4096\nlogging buffered 4096\n")

    assert statuses(check_required_lines(lines, ["logging buffered 4096"])) == [PASS]


def test_required_line_whitespace_variations():
    lines = config_lines("   ip  ssh   version 2   \r\n")

    assert statuses(check_required_lines(lines, ["  ip ssh version 2 "])) == [PASS]


def test_required_line_is_case_sensitive():
    assert statuses(check_required_lines(config_lines(R1_CONFIG), ["IP SSH VERSION 2"])) == [FAIL]


def test_required_line_is_whole_line_not_substring():
    # "network 10.0.12.0" alone is not a full line in the config.
    assert statuses(check_required_lines(config_lines(R1_CONFIG), ["network 10.0.12.0"])) == [FAIL]


# --- prohibited lines -------------------------------------------------------

def test_prohibited_line_absent():
    checks = check_prohibited_lines(config_lines(R1_CONFIG), ["transport input telnet"])

    assert checks[0]["status"] == PASS
    assert checks[0]["check_type"] == "prohibited_config"
    assert (checks[0]["expected"], checks[0]["actual"]) == ("absent", "absent")


def test_prohibited_line_present():
    config = R1_CONFIG.replace("no ip http server", "ip http server")

    checks = check_prohibited_lines(config_lines(config), ["ip http server"])

    assert checks[0]["status"] == FAIL
    assert checks[0]["actual"] == "present"
    assert checks[0]["message"] == "prohibited configuration line found"


def test_negated_line_does_not_match_prohibited_line():
    # The fixture contains "no ip http server", which is the safe setting.
    assert "no ip http server" in R1_CONFIG

    assert statuses(check_prohibited_lines(config_lines(R1_CONFIG), ["ip http server"])) == [PASS]


def test_prohibited_line_inside_section_is_found():
    config = R1_CONFIG.replace(" transport input ssh", " transport input telnet")

    assert statuses(check_prohibited_lines(config_lines(config), ["transport input telnet"])) == [FAIL]


# --- interface / IP checks --------------------------------------------------

def test_interface_and_ip_present():
    checks = check_interfaces(R1_CONFIG, {"GigabitEthernet0/0": ["ip address 192.168.100.11 255.255.255.0"]})

    assert [(c["check_type"], c["check_name"], c["status"]) for c in checks] == [
        ("interface", "interface GigabitEthernet0/0", PASS),
        ("interface_config", "GigabitEthernet0/0: ip address 192.168.100.11 255.255.255.0", PASS),
    ]


def test_interface_ip_missing():
    checks = check_interfaces(R1_CONFIG, {"GigabitEthernet0/0": ["ip address 192.168.100.99 255.255.255.0"]})

    assert statuses(checks) == [PASS, FAIL]
    assert checks[1]["message"] == "line not found under interface GigabitEthernet0/0"


def test_interface_missing_fails_its_lines_too():
    checks = check_interfaces(R1_CONFIG, {"GigabitEthernet0/9": ["ip address 10.9.9.9 255.255.255.0", "no shutdown"]})

    assert statuses(checks) == [FAIL, FAIL, FAIL]
    assert checks[0]["message"] == "interface not found"
    assert checks[1]["message"] == "interface GigabitEthernet0/9 not found"


def test_ip_on_wrong_interface_fails():
    # 10.0.12.1 is configured on Gi0/1, not Gi0/0.
    checks = check_interfaces(R1_CONFIG, {"GigabitEthernet0/0": ["ip address 10.0.12.1 255.255.255.252"]})

    assert checks[1]["status"] == FAIL
    assert checks[1]["message"] == "line found, but not under interface GigabitEthernet0/0"


def test_interface_shutdown_check():
    checks = check_interfaces(R1_CONFIG, {"GigabitEthernet0/2": ["shutdown"]})

    assert statuses(checks) == [PASS, PASS]


# --- whole config -----------------------------------------------------------

@pytest.mark.parametrize(("hostname", "config"), [("R1", R1_CONFIG), ("R2", R2_CONFIG), ("SW1", SW1_CONFIG)])
def test_lab_configs_pass_project_rules(hostname, config):
    checks = validate_config_text(EXPECTED[hostname], config)

    assert len(checks) == 11
    assert statuses(checks) == [PASS] * 11


def test_validate_config_text_one_failure():
    config = R1_CONFIG.replace("ip ssh version 2", "ip ssh version 1")

    checks = validate_config_text(R1_RULES, config)

    failed = [c["check_name"] for c in checks if c["status"] == FAIL]
    assert failed == ["ip ssh version 2"]
    assert len(checks) == 11


def test_validate_config_text_multiple_failures():
    config = (
        R1_CONFIG.replace("hostname R1", "hostname Router")
        .replace("no ip http server", "ip http server")
        .replace(" ip address 192.168.100.11 255.255.255.0", " ip address 192.168.100.50 255.255.255.0")
    )

    checks = validate_config_text(R1_RULES, config)

    failed = {c["check_type"] for c in checks if c["status"] == FAIL}
    assert failed == {"hostname", "prohibited_config", "interface_config"}
    assert sum(1 for c in checks if c["status"] == FAIL) == 3
    assert len(checks) == 11   # the number of checks does not depend on the config


# --- one device -------------------------------------------------------------

@mock.patch("src.config_validation.connect")
def test_validate_device_all_pass(mock_connect):
    connection = fake_ios_connection(R1_CONFIG)
    mock_connect.return_value = connection

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert len(checks) == 11
    assert statuses(checks) == [PASS] * 11
    assert list(checks[0]) == REPORT_FIELDS
    assert {c["hostname"] for c in checks} == {"R1"}
    assert {c["host"] for c in checks} == {"192.168.100.11"}
    assert {c["role"] for c in checks} == {"core-router"}
    assert all(c["checked_at"] for c in checks)
    connection.send_command.assert_called_once_with("show running-config", read_timeout=COMMAND_READ_TIMEOUT)
    connection.disconnect.assert_called_once()
    mock_connect.assert_called_once_with(R1, CREDENTIALS, 10)


@mock.patch("src.config_validation.connect")
def test_validate_device_one_fail(mock_connect):
    mock_connect.return_value = fake_ios_connection(R1_CONFIG.replace(" transport input ssh", " transport input telnet"))

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    failed = [(c["check_type"], c["check_name"]) for c in checks if c["status"] == FAIL]
    assert failed == [("required_config", "transport input ssh"), ("prohibited_config", "transport input telnet")]


@mock.patch("src.config_validation.connect")
def test_validate_device_multiple_fails(mock_connect):
    config = R1_CONFIG.replace("router ospf 1", "router eigrp 100").replace("hostname R1", "hostname X")
    mock_connect.return_value = fake_ios_connection(config)

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert sum(1 for c in checks if c["status"] == FAIL) == 2
    assert overall_status(checks) == FAIL


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (SSHAuthenticationError("SSH authentication failed for 192.168.100.11"), "authentication failed"),
        (SSHTimeoutError("SSH connection to 192.168.100.11 timed out"), "timed out"),
        (SSHConnectionError("SSH connection to 192.168.100.11 failed: connection refused"), "connection refused"),
    ],
)
@mock.patch("src.config_validation.connect")
def test_validate_device_connection_failures(mock_connect, error, message):
    mock_connect.side_effect = error

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert len(checks) == 1
    assert checks[0]["status"] == ERROR
    assert checks[0]["check_type"] == "collection"
    assert checks[0]["check_name"] == "show running-config"
    assert message in checks[0]["message"]
    assert overall_status(checks) == ERROR


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "empty output"),
        (load_fixture("invalid_input.txt"), "% Invalid input detected"),
        ("% Authorization failed.\n", "% Authorization failed"),
        (R1_CONFIG[:300], "incomplete"),
    ],
)
@mock.patch("src.config_validation.connect")
def test_validate_device_invalid_output(mock_connect, output, message):
    connection = fake_ios_connection(output)
    mock_connect.return_value = connection

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert statuses(checks) == [ERROR]
    assert message in checks[0]["message"]
    connection.disconnect.assert_called_once()


@mock.patch("src.config_validation.connect")
def test_validate_device_retrieval_failure(mock_connect):
    connection = fake_ios_connection(OSError("Socket is closed"))
    mock_connect.return_value = connection

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert statuses(checks) == [ERROR]
    assert checks[0]["message"] == "collection failed: Socket is closed"
    connection.disconnect.assert_called_once()


@mock.patch("src.config_validation.connect")
def test_validate_device_without_rules_does_not_connect(mock_connect):
    checks = validate_device(R1, None, CREDENTIALS)

    assert statuses(checks) == [ERROR]
    assert checks[0]["check_type"] == "rules"
    assert "no validation rules defined" in checks[0]["message"]
    mock_connect.assert_not_called()


@mock.patch("src.config_validation.connect")
def test_validate_device_only_reads_the_device(mock_connect):
    connection = fake_ios_connection(R1_CONFIG)
    mock_connect.return_value = connection

    validate_device(R1, R1_RULES, CREDENTIALS)

    assert_no_config_changes(connection)
    assert [call.args[0] for call in connection.send_command.call_args_list] == ["show running-config"]


@mock.patch("src.config_validation.connect")
def test_results_never_contain_credentials(mock_connect):
    mock_connect.return_value = fake_ios_connection(R1_CONFIG)

    checks = validate_device(R1, R1_RULES, CREDENTIALS)

    assert "Test-Pass-123" not in str(checks)
    assert "labuser" not in str(checks)


def test_validate_device_cannot_reach_real_netmiko():
    # Without mocking connect(), the conftest.py safeguard must stop the real ConnectHandler.
    with pytest.raises(pytest.fail.Exception, match="Real SSH connection attempted"):
        validate_device(R1, R1_RULES, CREDENTIALS)


# --- all devices ------------------------------------------------------------

def test_run_validation_all_pass(inventory_file, lab_credentials):
    connections = []
    with mock.patch("src.config_validation.connect", side_effect=fake_connect(connections=connections)) as mock_connect:
        checks = run_validation(inventory_file, DEFAULT_EXPECTED_PATH)

    summaries = summarize_devices(checks)
    assert [(s["hostname"], s["overall_status"], s["passed_checks"], s["total_checks"]) for s in summaries] == [
        ("R1", PASS, 11, 11),
        ("R2", PASS, 11, 11),
        ("SW1", PASS, 11, 11),
    ]
    assert mock_connect.call_args.args[1] == lab_credentials
    for connection in connections:
        assert_no_config_changes(connection)
        connection.disconnect.assert_called_once()


@mock.patch(
    "src.config_validation.connect",
    side_effect=fake_connect(
        failures={"192.168.100.12": SSHAuthenticationError("SSH authentication failed for 192.168.100.12")},
        configs={"192.168.100.21": SW1_CONFIG.replace("vlan 10\n", "vlan 20\n")},
    ),
)
def test_run_validation_mixed_pass_fail_error(mock_connect, inventory_file, lab_credentials):
    checks = run_validation(inventory_file, DEFAULT_EXPECTED_PATH)

    summaries = {s["hostname"]: s for s in summarize_devices(checks)}
    assert summaries["R1"]["overall_status"] == PASS
    assert summaries["R2"]["overall_status"] == ERROR
    assert summaries["SW1"]["overall_status"] == FAIL
    assert (summaries["SW1"]["passed_checks"], summaries["SW1"]["failed_checks"]) == (10, 1)
    assert (summaries["R2"]["total_checks"], summaries["R2"]["error_checks"]) == (1, 1)


@mock.patch("src.config_validation.connect", side_effect=fake_connect({"192.168.100.11": RuntimeError("unexpected")}))
def test_one_device_failure_does_not_stop_others(mock_connect, inventory_file, lab_credentials):
    checks = run_validation(inventory_file, DEFAULT_EXPECTED_PATH)

    summaries = summarize_devices(checks)
    assert [s["overall_status"] for s in summaries] == [ERROR, PASS, PASS]
    assert checks[0]["message"] == "collection failed: unexpected"
    assert mock_connect.call_count == 3


@mock.patch("src.config_validation.connect", side_effect=fake_connect())
def test_run_validation_device_without_rules(mock_connect, inventory_file, tmp_path, lab_credentials):
    expected = write_file(tmp_path, "expected.yaml", "devices:\n  R1:\n    hostname: R1\n  R2:\n    hostname: R2\n")

    checks = run_validation(inventory_file, expected)

    summaries = {s["hostname"]: s["overall_status"] for s in summarize_devices(checks)}
    assert summaries == {"R1": PASS, "R2": PASS, "SW1": ERROR}
    assert mock_connect.call_count == 2   # SW1 is not contacted


@mock.patch("src.config_validation.connect")
def test_run_validation_missing_credentials_contacts_no_devices(mock_connect, inventory_file, no_credentials):
    with pytest.raises(CredentialsError):
        run_validation(inventory_file, DEFAULT_EXPECTED_PATH)
    mock_connect.assert_not_called()


@mock.patch("src.config_validation.connect")
def test_run_validation_invalid_rules_contacts_no_devices(mock_connect, inventory_file, tmp_path, lab_credentials):
    expected = write_file(tmp_path, "expected.yaml", "devices:\n  R1:\n    hostnme: R1\n")

    with pytest.raises(ExpectedConfigError, match="Unknown rule type"):
        run_validation(inventory_file, expected)
    mock_connect.assert_not_called()


@mock.patch("src.config_validation.connect")
def test_run_validation_missing_inventory(mock_connect, tmp_path, lab_credentials):
    with pytest.raises(InventoryError):
        run_validation(tmp_path / "missing.yaml", DEFAULT_EXPECTED_PATH)
    mock_connect.assert_not_called()


# --- summaries --------------------------------------------------------------

def make_result(hostname, status):
    return {"hostname": hostname, "host": "192.168.100.11", "role": "core-router", "status": status, "checked_at": "2026-10-19T13:00:00"}


def test_summarize_devices_counts():
    checks = [make_result("R1", s) for s in (PASS, PASS, FAIL, PASS)] + [make_result("R2", ERROR)]

    summaries = summarize_devices(checks)

    assert summaries[0] == {
        "hostname": "R1",
        "host": "192.168.100.11",
        "role": "core-router",
        "total_checks": 4,
        "passed_checks": 3,
        "failed_checks": 1,
        "error_checks": 0,
        "overall_status": FAIL,
        "checked_at": "2026-10-19T13:00:00",
    }
    assert (summaries[1]["total_checks"], summaries[1]["error_checks"], summaries[1]["overall_status"]) == (1, 1, ERROR)
    assert list(summaries[0]) == SUMMARY_FIELDS


@pytest.mark.parametrize(
    ("check_statuses", "expected"),
    [
        ([PASS, PASS], PASS),
        ([PASS, FAIL], FAIL),
        ([FAIL, FAIL], FAIL),
        ([ERROR], ERROR),
        ([PASS, FAIL, ERROR], ERROR),
    ],
)
def test_overall_status(check_statuses, expected):
    assert overall_status([{"status": s} for s in check_statuses]) == expected


def test_summarize_devices_empty():
    assert summarize_devices([]) == []


# --- terminal output --------------------------------------------------------

@mock.patch("src.config_validation.connect")
def test_format_checks_table(mock_connect):
    mock_connect.return_value = fake_ios_connection(R1_CONFIG.replace("no ip http server", "ip http server"))

    lines = format_checks_table(validate_device(R1, R1_RULES, CREDENTIALS)).splitlines()

    assert lines[0].split() == ["DEVICE", "CHECK", "STATUS", "MESSAGE"]
    assert lines[2].split() == ["R1", "hostname", "R1", "PASS"]
    assert any("prohibited: ip http server" in line and "FAIL" in line and "prohibited configuration line found" in line for line in lines)
    assert any("GigabitEthernet0/0: ip address 192.168.100.11 255.255.255.0" in line for line in lines)


def test_check_label():
    assert check_label({"check_type": "prohibited_config", "check_name": "ip http server"}) == "prohibited: ip http server"
    assert check_label({"check_type": "required_config", "check_name": "router ospf 1"}) == "router ospf 1"


def test_format_summary_table():
    summaries = [
        {"hostname": "R1", "overall_status": PASS, "passed_checks": 11, "total_checks": 11},
        {"hostname": "SW1", "overall_status": FAIL, "passed_checks": 10, "total_checks": 11},
    ]

    assert format_summary_table(summaries).splitlines() == [
        "DEVICE  RESULT  PASSED",
        "------  ------  ------",
        "R1      PASS    11/11",
        "SW1     FAIL    10/11",
    ]


# --- CLI and CSV reports ----------------------------------------------------

def cli_args(inventory_file, tmp_path, expected=DEFAULT_EXPECTED_PATH):
    return [
        "--inventory", str(inventory_file),
        "--expected", str(expected),
        "--report", str(tmp_path / "reports" / "config_validation_report.csv"),
        "--summary-report", str(tmp_path / "reports" / "config_validation_summary.csv"),
    ]


@mock.patch("src.config_validation.connect", side_effect=fake_connect())
def test_main_all_pass_writes_both_reports(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    exit_code = main(cli_args(inventory_file, tmp_path))

    output = capsys.readouterr().out
    detail = read_csv(tmp_path / "reports" / "config_validation_report.csv")
    summary = read_csv(tmp_path / "reports" / "config_validation_summary.csv")
    assert exit_code == 0
    assert "Summary:" in output
    assert "R1      PASS    11/11" in output
    assert "Detailed report saved to:" in output and "Summary report saved to:" in output
    assert list(detail[0]) == REPORT_FIELDS
    assert len(detail) == 33
    assert {row["status"] for row in detail} == {PASS}
    assert list(summary[0]) == SUMMARY_FIELDS
    assert [(row["hostname"], row["overall_status"], row["total_checks"]) for row in summary] == [
        ("R1", PASS, "11"), ("R2", PASS, "11"), ("SW1", PASS, "11"),
    ]


@mock.patch(
    "src.config_validation.connect",
    side_effect=fake_connect(
        failures={"192.168.100.12": SSHTimeoutError("SSH connection to 192.168.100.12 timed out")},
        configs={"192.168.100.21": SW1_CONFIG.replace("no ip http server", "ip http server")},
    ),
)
def test_main_mixed_results(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    exit_code = main(cli_args(inventory_file, tmp_path))

    output = capsys.readouterr().out
    detail = read_csv(tmp_path / "reports" / "config_validation_report.csv")
    summary = read_csv(tmp_path / "reports" / "config_validation_summary.csv")
    assert exit_code == 1
    assert [row["overall_status"] for row in summary] == [PASS, ERROR, FAIL]
    failed = [row for row in detail if row["status"] == FAIL]
    assert [(row["hostname"], row["check_type"], row["check_name"], row["actual"]) for row in failed] == [
        ("SW1", "prohibited_config", "ip http server", "present"),
    ]
    error = [row for row in detail if row["status"] == ERROR][0]
    assert (error["hostname"], error["check_name"], error["actual"]) == ("R2", "show running-config", "")
    assert "timed out" in error["message"]
    assert "R2      ERROR   0/1" in output
    assert "SW1     FAIL    10/11" in output


@mock.patch("src.config_validation.connect", side_effect=fake_connect())
def test_credentials_never_appear_in_output_or_reports(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    main(cli_args(inventory_file, tmp_path))

    output = capsys.readouterr()
    reports = [(tmp_path / "reports" / name).read_text(encoding="utf-8")
               for name in ("config_validation_report.csv", "config_validation_summary.csv")]
    for secret in ("Test-Pass-123", "labuser"):
        assert secret not in output.out and secret not in output.err
        assert all(secret not in text for text in reports)


@mock.patch("src.config_validation.connect")
def test_main_missing_credentials(mock_connect, inventory_file, tmp_path, no_credentials, capsys):
    exit_code = main(cli_args(inventory_file, tmp_path))

    assert exit_code == 2
    assert "Missing environment variable(s): NETWORK_USERNAME, NETWORK_PASSWORD" in capsys.readouterr().err
    mock_connect.assert_not_called()
    assert not (tmp_path / "reports").exists()


@mock.patch("src.config_validation.connect")
def test_main_invalid_expected_config(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    expected = write_file(tmp_path, "expected.yaml", "devices:\n  R1:\n    required_config_lines: ip ssh version 2\n")

    exit_code = main(cli_args(inventory_file, tmp_path, expected))

    assert exit_code == 2
    assert "must be a list" in capsys.readouterr().err
    mock_connect.assert_not_called()


@mock.patch("src.config_validation.write_csv_report", side_effect=PermissionError("file is open in another program"))
@mock.patch("src.config_validation.connect", side_effect=fake_connect())
def test_main_report_write_error(mock_connect, mock_write, inventory_file, tmp_path, lab_credentials, capsys):
    exit_code = main(cli_args(inventory_file, tmp_path))

    assert exit_code == 2
    assert "could not write report" in capsys.readouterr().err


@mock.patch("src.config_validation.connect", side_effect=fake_connect())
def test_main_passes_timeout(mock_connect, inventory_file, tmp_path, lab_credentials):
    main(cli_args(inventory_file, tmp_path) + ["--timeout", "30"])

    assert mock_connect.call_args.args[2] == 30


def test_main_rejects_invalid_timeout():
    with pytest.raises(SystemExit) as exc_info:
        main(["--timeout", "0"])
    assert exc_info.value.code == 2


def test_default_locations():
    assert DEFAULT_EXPECTED_PATH.parts[-2:] == ("config", "expected_config.yaml")
    assert DEFAULT_REPORT_PATH.parts[-3:] == ("data", "reports", "config_validation_report.csv")
    assert DEFAULT_SUMMARY_PATH.parts[-3:] == ("data", "reports", "config_validation_summary.csv")


# --- dry run (no SSH, no reports) -------------------------------------------

@mock.patch("src.config_validation.connect")
def test_dry_run_opens_no_ssh_and_writes_no_reports(mock_connect, inventory_file, tmp_path, lab_credentials, capsys):
    exit_code = main(["--dry-run"] + cli_args(inventory_file, tmp_path))

    output = capsys.readouterr().out
    assert exit_code == 0
    mock_connect.assert_not_called()
    assert not (tmp_path / "reports").exists()
    assert "No SSH connections will be opened and no reports will be written" in output
    assert "Inventory OK: 3 device(s)" in output
    assert "Expected rules OK: 3 device(s)" in output
    assert "R1        192.168.100.11  core-router    11" in output
    assert "Credentials OK" in output
    assert "Test-Pass-123" not in output and "labuser" not in output


@mock.patch("src.config_validation.connect")
def test_dry_run_with_default_paths_writes_nothing(mock_connect, lab_credentials):
    # The conftest safeguard fails this test if anything is written to data/reports/.
    assert main(["--dry-run"]) == 0
    mock_connect.assert_not_called()


@mock.patch("src.config_validation.connect")
def test_dry_run_missing_credentials(mock_connect, inventory_file, tmp_path, no_credentials, capsys):
    exit_code = main(["--dry-run"] + cli_args(inventory_file, tmp_path))

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Expected rules OK" in captured.out
    assert "Missing environment variable(s)" in captured.err
    mock_connect.assert_not_called()


def test_dry_run_invalid_expected_config(inventory_file, tmp_path, lab_credentials, capsys):
    expected = write_file(tmp_path, "expected.yaml", "devices: [R1]\n")

    exit_code = main(["--dry-run"] + cli_args(inventory_file, tmp_path, expected))

    assert exit_code == 2
    assert "non-empty 'devices' mapping" in capsys.readouterr().err


def test_dry_run_warns_about_missing_and_unknown_devices(inventory_file, tmp_path, lab_credentials, capsys):
    expected = write_file(tmp_path, "expected.yaml", "devices:\n  R1:\n    hostname: R1\n  R9:\n    hostname: R9\n")

    exit_code = main(["--dry-run"] + cli_args(inventory_file, tmp_path, expected))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "no rules" in output
    assert "WARNING: no expected rules for R2" in output
    assert "WARNING: no expected rules for SW1" in output
    assert "WARNING: expected rules for R9, but R9 is not in the inventory" in output
