"""Validate Cisco running configurations against expected lab settings.

Expected settings are read from config/expected_config.yaml. For each device
the running configuration is collected over SSH with the read-only command
    show running-config
(reusing the Phase 4 backup retrieval and its output checks) and compared
line by line. Nothing is ever changed on the device.

Check types:
    hostname           the "hostname" line has the expected value
    required_config    a line must be present anywhere in the config
    prohibited_config  a line must not be present
    interface          an "interface <name>" section must exist
    interface_config   a line must be present inside that interface section
    collection         the running-config could not be collected (ERROR)
    rules              no expected rules exist for the device (ERROR)

Run from the project root once the GNS3 lab is reachable:
    python -m src.config_validation

Validate the inventory, expected rules and credentials without SSH:
    python -m src.config_validation --dry-run

Exit codes:
    0 = every device passed
    1 = one or more devices failed or could not be checked
    2 = inventory, expected config, credentials or report error
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from src.config_backup import BackupError, get_running_config
from src.inventory import DEFAULT_INVENTORY_PATH, PROJECT_ROOT, InventoryError, load_inventory
from src.reports import REPORTS_DIR, format_table, write_csv_report
from src.ssh_client import (
    DEFAULT_SSH_TIMEOUT,
    PASSWORD_ENV,
    USERNAME_ENV,
    CredentialsError,
    SSHConnectionError,
    connect,
    disconnect,
    get_credentials,
)

DEFAULT_EXPECTED_PATH = PROJECT_ROOT / "config" / "expected_config.yaml"
DEFAULT_REPORT_PATH = REPORTS_DIR / "config_validation_report.csv"
DEFAULT_SUMMARY_PATH = REPORTS_DIR / "config_validation_summary.csv"

PASS = "PASS"
FAIL = "FAIL"
ERROR = "ERROR"

PRESENT = "present"
MISSING = "missing"
ABSENT = "absent"

RULE_KEYS = {"hostname", "required_config_lines", "prohibited_config_lines", "interfaces"}

# One row per check. Credentials are never part of a result.
REPORT_FIELDS = [
    "hostname", "host", "role", "check_type", "check_name",
    "expected", "actual", "status", "message", "checked_at",
]

# One row per device.
SUMMARY_FIELDS = [
    "hostname", "host", "role", "total_checks", "passed_checks",
    "failed_checks", "error_checks", "overall_status", "checked_at",
]


class ExpectedConfigError(Exception):
    """Raised when expected_config.yaml is missing or contains invalid rules."""


# --- loading and checking the expected rules --------------------------------

def load_expected_config(path=DEFAULT_EXPECTED_PATH):
    """Load expected_config.yaml and return {inventory hostname: rules}.

    Raises ExpectedConfigError with a clear message for any problem.
    """
    path = Path(path)
    if not path.is_file():
        raise ExpectedConfigError(f"Expected config file not found: {path}")

    try:
        with path.open(encoding="utf-8") as expected_file:
            data = yaml.safe_load(expected_file)
    except yaml.YAMLError as exc:
        raise ExpectedConfigError(f"Expected config file is not valid YAML: {path}\n{exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("devices"), dict) or not data["devices"]:
        raise ExpectedConfigError(f"Expected config must contain a non-empty 'devices' mapping: {path}")

    for name, rules in data["devices"].items():
        validate_rules(str(name), rules)
    return {str(name): rules for name, rules in data["devices"].items()}


def validate_rules(name, rules):
    """Check the structure of one device's rules. Raises ExpectedConfigError."""
    if not isinstance(rules, dict):
        raise ExpectedConfigError(f"Rules for {name} must be a mapping of rule types")

    unknown = sorted(set(rules) - RULE_KEYS)
    if unknown:
        raise ExpectedConfigError(
            f"Unknown rule type(s) for {name}: {', '.join(unknown)}. Allowed: {', '.join(sorted(RULE_KEYS))}"
        )

    if "hostname" in rules and not _is_text(rules["hostname"]):
        raise ExpectedConfigError(f"'hostname' for {name} must be non-empty text")

    required = _check_line_list(name, "required_config_lines", rules.get("required_config_lines"))
    prohibited = _check_line_list(name, "prohibited_config_lines", rules.get("prohibited_config_lines"))
    conflicts = sorted(set(required) & set(prohibited))
    if conflicts:
        raise ExpectedConfigError(f"Line(s) for {name} are both required and prohibited: {', '.join(conflicts)}")

    interfaces = rules.get("interfaces")
    if interfaces is not None and not isinstance(interfaces, dict):
        raise ExpectedConfigError(f"'interfaces' for {name} must map interface names to lists of lines")
    for interface, lines in (interfaces or {}).items():
        if not _is_text(interface):
            raise ExpectedConfigError(f"Interface names for {name} must be text, got: {interface!r}")
        _check_line_list(name, f"interfaces/{interface}", lines)

    if count_checks(rules) == 0:
        raise ExpectedConfigError(f"No validation rules defined for {name}")


def _check_line_list(name, key, lines):
    """Make sure `lines` is a list of unique, non-empty text lines and return them normalized.

    An empty list (or a key left empty in YAML) is allowed.
    """
    if lines is None:
        return []
    if not isinstance(lines, list):
        raise ExpectedConfigError(f"'{key}' for {name} must be a list of configuration lines")
    for line in lines:
        if not _is_text(line):
            raise ExpectedConfigError(f"'{key}' for {name} contains an invalid line: {line!r} (lines must be text)")
    normalized = [normalize_line(line) for line in lines]
    duplicates = sorted({line for line in normalized if normalized.count(line) > 1})
    if duplicates:
        raise ExpectedConfigError(f"'{key}' for {name} lists the same line more than once: {', '.join(duplicates)}")
    return normalized


def _is_text(value):
    return isinstance(value, str) and value.strip() != ""


def count_checks(rules):
    """Number of checks a device's rules will produce."""
    interfaces = rules.get("interfaces") or {}
    return (
        (1 if rules.get("hostname") else 0)
        + len(rules.get("required_config_lines") or [])
        + len(rules.get("prohibited_config_lines") or [])
        + sum(1 + len(lines or []) for lines in interfaces.values())
    )


# --- normalizing the running configuration ----------------------------------

def normalize_line(line):
    """Strip leading/trailing whitespace and collapse repeated spaces/tabs to one space.

    Case is preserved: Cisco configuration is compared exactly.
    """
    return " ".join(str(line).split())


def config_lines(config_text):
    """Return the running-config as a list of normalized lines.

    Blank lines and "!" separator lines are dropped. Works with Windows (\\r\\n)
    and Linux (\\n) line endings.
    """
    lines = (normalize_line(line) for line in config_text.splitlines())
    return [line for line in lines if line and line != "!"]


def get_interface_block(config_text, interface):
    """Return the normalized lines under 'interface <name>', or None if the interface does not exist.

    In IOS the lines belonging to an interface are the indented lines directly
    after the 'interface ...' line. The section ends at the first line that is
    not indented (usually '!').
    """
    header = f"interface {interface}"
    block = None
    for line in config_text.splitlines():
        if block is None:
            if not line[:1].isspace() and normalize_line(line) == header:
                block = []
        elif line[:1].isspace() and line.strip():
            block.append(normalize_line(line))
        else:
            break
    return block


# --- individual checks ------------------------------------------------------

def make_check(check_type, check_name, expected, actual, status, message):
    return {
        "check_type": check_type,
        "check_name": check_name,
        "expected": expected,
        "actual": actual,
        "status": status,
        "message": message,
    }


def check_hostname(lines, expected_hostname):
    """Check the 'hostname <name>' line."""
    expected_hostname = normalize_line(expected_hostname)
    found = next((line.split(" ", 1)[1] for line in lines if line.startswith("hostname ")), None)
    if found == expected_hostname:
        return make_check("hostname", f"hostname {expected_hostname}", expected_hostname, found, PASS, "hostname matches")
    message = "no hostname line found" if found is None else f"hostname is {found}, expected {expected_hostname}"
    return make_check("hostname", f"hostname {expected_hostname}", expected_hostname, found or MISSING, FAIL, message)


def check_required_lines(lines, required_lines):
    """Every required line must be present (exact whole-line match)."""
    present = set(lines)
    checks = []
    for line in required_lines:
        line = normalize_line(line)
        if line in present:
            checks.append(make_check("required_config", line, PRESENT, PRESENT, PASS, "required line found"))
        else:
            checks.append(make_check("required_config", line, PRESENT, MISSING, FAIL, "required line not found"))
    return checks


def check_prohibited_lines(lines, prohibited_lines):
    """No prohibited line may be present (exact whole-line match)."""
    present = set(lines)
    checks = []
    for line in prohibited_lines:
        line = normalize_line(line)
        if line in present:
            checks.append(make_check("prohibited_config", line, ABSENT, PRESENT, FAIL, "prohibited configuration line found"))
        else:
            checks.append(make_check("prohibited_config", line, ABSENT, ABSENT, PASS, "prohibited line not present"))
    return checks


def check_interfaces(config_text, interfaces):
    """Each interface must exist, and its expected lines must be inside that interface section."""
    all_lines = set(config_lines(config_text))
    checks = []
    for interface, expected_lines in interfaces.items():
        block = get_interface_block(config_text, interface)
        if block is None:
            checks.append(make_check("interface", f"interface {interface}", PRESENT, MISSING, FAIL, "interface not found"))
        else:
            checks.append(make_check("interface", f"interface {interface}", PRESENT, PRESENT, PASS, "interface found"))

        for line in expected_lines or []:
            line = normalize_line(line)
            name = f"{interface}: {line}"
            if block is not None and line in block:
                checks.append(make_check("interface_config", name, PRESENT, PRESENT, PASS, f"line found under interface {interface}"))
            elif block is None:
                checks.append(make_check("interface_config", name, PRESENT, MISSING, FAIL, f"interface {interface} not found"))
            elif line in all_lines:
                checks.append(make_check("interface_config", name, PRESENT, MISSING, FAIL, f"line found, but not under interface {interface}"))
            else:
                checks.append(make_check("interface_config", name, PRESENT, MISSING, FAIL, f"line not found under interface {interface}"))
    return checks


def validate_config_text(rules, config_text):
    """Run every check in `rules` against a running-config text. No SSH involved."""
    lines = config_lines(config_text)
    checks = []
    if rules.get("hostname"):
        checks.append(check_hostname(lines, rules["hostname"]))
    checks += check_required_lines(lines, rules.get("required_config_lines") or [])
    checks += check_prohibited_lines(lines, rules.get("prohibited_config_lines") or [])
    checks += check_interfaces(config_text, rules.get("interfaces") or {})
    return checks


# --- one device -------------------------------------------------------------

def validate_device(device, rules, credentials, timeout=DEFAULT_SSH_TIMEOUT):
    """Collect one device's running-config and validate it. Returns a list of check results.

    Never raises: a device that has no rules, cannot be reached or returns bad
    output gets a single ERROR check, so the rest of the run continues.
    """
    if rules is None:
        checks = [make_check("rules", "expected rules", "rules defined", MISSING, ERROR,
                             "no validation rules defined for this device in expected_config.yaml")]
    else:
        connection = None
        try:
            connection = connect(device, credentials, timeout)
            config_text = get_running_config(connection)
            checks = validate_config_text(rules, config_text)
        except (SSHConnectionError, BackupError) as exc:
            checks = [_collection_error(str(exc))]
        except Exception as exc:  # session dropped, unexpected device output, ...
            checks = [_collection_error(f"collection failed: {exc}")]
        finally:
            disconnect(connection)

    checked_at = datetime.now().isoformat(timespec="seconds")
    device_details = {"hostname": device.get("hostname"), "host": device.get("host"), "role": device.get("role")}
    return [{**device_details, **check, "checked_at": checked_at} for check in checks]


def _collection_error(message):
    return make_check("collection", "show running-config", "running-config collected", None, ERROR, message)


# --- all devices ------------------------------------------------------------

def run_validation(inventory_path=DEFAULT_INVENTORY_PATH, expected_path=DEFAULT_EXPECTED_PATH, timeout=DEFAULT_SSH_TIMEOUT):
    """Validate every inventory device, one at a time. Returns a flat list of check results.

    Raises InventoryError, ExpectedConfigError or CredentialsError before any
    device is contacted if the inputs are not usable.
    """
    devices = load_inventory(inventory_path)
    expected = load_expected_config(expected_path)
    credentials = get_credentials()

    checks = []
    for device in devices:
        checks += validate_device(device, expected.get(device["hostname"]), credentials, timeout)
    return checks


def find_unknown_devices(devices, expected):
    """Hostnames that have rules in expected_config.yaml but are not in the inventory."""
    inventory_names = {device["hostname"] for device in devices}
    return [name for name in expected if name not in inventory_names]


# --- summaries --------------------------------------------------------------

def overall_status(checks):
    """ERROR if any check could not run, else FAIL if any check failed, else PASS."""
    statuses = {check["status"] for check in checks}
    if ERROR in statuses:
        return ERROR
    if FAIL in statuses:
        return FAIL
    return PASS


def summarize_devices(checks):
    """Group check results by device and count them. Returns one summary dict per device."""
    by_device = {}
    for check in checks:
        by_device.setdefault(check["hostname"], []).append(check)

    summaries = []
    for hostname, device_checks in by_device.items():
        first = device_checks[0]
        summaries.append({
            "hostname": hostname,
            "host": first["host"],
            "role": first["role"],
            "total_checks": len(device_checks),
            "passed_checks": sum(1 for c in device_checks if c["status"] == PASS),
            "failed_checks": sum(1 for c in device_checks if c["status"] == FAIL),
            "error_checks": sum(1 for c in device_checks if c["status"] == ERROR),
            "overall_status": overall_status(device_checks),
            "checked_at": first["checked_at"],
        })
    return summaries


# --- terminal output --------------------------------------------------------

def check_label(check):
    """Short description of a check for the terminal table."""
    if check["check_type"] == "prohibited_config":
        return f"prohibited: {check['check_name']}"
    return check["check_name"]


def format_checks_table(checks):
    rows = [
        (c["hostname"], check_label(c), c["status"], "" if c["status"] == PASS else c["message"])
        for c in checks
    ]
    return format_table(("DEVICE", "CHECK", "STATUS", "MESSAGE"), rows)


def format_summary_table(summaries):
    rows = [(s["hostname"], s["overall_status"], f"{s['passed_checks']}/{s['total_checks']}") for s in summaries]
    return format_table(("DEVICE", "RESULT", "PASSED"), rows)


# --- command line -----------------------------------------------------------

def dry_run(inventory_path, expected_path):
    """Validate inventory, expected rules and credentials. Opens no SSH connection and writes no files."""
    print("Dry run: checking inventory, expected rules and credentials. "
          "No SSH connections will be opened and no reports will be written.\n")

    try:
        devices = load_inventory(inventory_path)
        expected = load_expected_config(expected_path)
    except (InventoryError, ExpectedConfigError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Inventory OK: {len(devices)} device(s) in {inventory_path}")
    print(f"Expected rules OK: {len(expected)} device(s) in {expected_path}\n")

    rows = []
    for device in devices:
        rules = expected.get(device["hostname"])
        rows.append((device["hostname"], device["host"], device["role"], count_checks(rules) if rules else "no rules"))
    print(format_table(("HOSTNAME", "HOST", "ROLE", "CHECKS"), rows))

    for device in devices:
        if device["hostname"] not in expected:
            print(f"\nWARNING: no expected rules for {device['hostname']}; it will be reported as ERROR.")
    for name in find_unknown_devices(devices, expected):
        print(f"\nWARNING: expected rules for {name}, but {name} is not in the inventory; these rules will be ignored.")

    try:
        get_credentials()
    except CredentialsError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2

    print(f"\nCredentials OK: {USERNAME_ENV} and {PASSWORD_ENV} are set (values not shown).")
    return 0


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate Cisco running configurations against config/expected_config.yaml."
    )
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY_PATH, help="path to the YAML inventory file")
    parser.add_argument("--expected", default=DEFAULT_EXPECTED_PATH, help="path to the expected configuration rules")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="path of the detailed CSV report (one row per check)")
    parser.add_argument("--summary-report", default=DEFAULT_SUMMARY_PATH, help="path of the summary CSV report (one row per device)")
    parser.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_SSH_TIMEOUT,
        help=f"SSH connection timeout in seconds (default: {DEFAULT_SSH_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="only validate the inventory, expected rules and credentials; do not connect or write reports",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args.inventory, args.expected)

    print(f"Validating running configurations using rules: {args.expected}\n")
    try:
        checks = run_validation(args.inventory, args.expected, timeout=args.timeout)
    except (InventoryError, ExpectedConfigError, CredentialsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summaries = summarize_devices(checks)
    print(format_checks_table(checks))
    print("\nSummary:")
    print(format_summary_table(summaries))

    try:
        report_path = write_csv_report(checks, args.report, REPORT_FIELDS)
        summary_path = write_csv_report(summaries, args.summary_report, SUMMARY_FIELDS)
    except OSError as exc:
        print(f"\nERROR: could not write report: {exc}", file=sys.stderr)
        return 2

    print(f"\nDetailed report saved to: {report_path}")
    print(f"Summary report saved to:  {summary_path}")
    return 0 if all(s["overall_status"] == PASS for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
