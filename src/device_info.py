"""Collect read-only information from Cisco IOS lab devices over SSH.

Commands used (all read-only "show" commands, no configuration changes):
    show version              -> device hostname, IOS version, uptime
    show ip interface brief   -> interface count and up/down status
    show interfaces status    -> switch port summary (optional: not supported on routers)

Run from the project root once the GNS3 lab is reachable:
    python -m src.device_info

Validate inventory and credentials without opening any SSH connection:
    python -m src.device_info --dry-run

Exit codes:
    0 = information collected from all devices
    1 = one or more devices failed
    2 = inventory, credentials or report error (collection could not run)
"""

import argparse
import re
import sys
from collections import Counter
from datetime import datetime

from src.inventory import DEFAULT_INVENTORY_PATH, InventoryError, load_inventory
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

DEFAULT_REPORT_PATH = REPORTS_DIR / "device_info_report.csv"

SHOW_VERSION = "show version"
SHOW_IP_INTERFACE_BRIEF = "show ip interface brief"
SHOW_INTERFACES_STATUS = "show interfaces status"

# Columns of the CSV report, in order. Credentials are never part of a result.
REPORT_FIELDS = [
    "inventory_hostname",
    "host",
    "role",
    "ssh_status",
    "device_hostname",
    "ios_version",
    "uptime",
    "interface_count",
    "interfaces_up",
    "interfaces_down",
    "ip_interface_summary",
    "switchport_summary",
    "unsupported_commands",
    "error",
    "checked_at",
]

TABLE_HEADERS = ("HOSTNAME", "HOST", "SSH STATUS", "DEVICE NAME", "IOS VERSION", "UPTIME", "INTF UP/TOTAL", "ERROR")

# IOS answers an unknown or incomplete command with a line starting with "%".
INVALID_COMMAND_PATTERN = re.compile(r"^\s*% (Invalid input|Incomplete command|Ambiguous command)", re.MULTILINE)

# "Cisco IOS Software, IOSv Software (VIOS-ADVENTERPRISEK9-M), Version 15.9(3)M6, RELEASE SOFTWARE (fc1)"
# The first line mentioning IOS and "Version" holds the software version.
VERSION_PATTERN = re.compile(r"^.*\bIOS\b.*?\bVersion\s+([^\s,]+)", re.MULTILINE)

# "R1 uptime is 2 hours, 14 minutes"
UPTIME_PATTERN = re.compile(r"^(\S+) uptime is (.+)$", re.MULTILINE)

# Port states shown in the Status column of "show interfaces status".
SWITCHPORT_STATES = {"connected", "notconnect", "disabled", "err-disabled", "inactive", "monitoring", "sfpAbsent"}


class UnsupportedCommandError(Exception):
    """Raised when the device rejects a command (e.g. '% Invalid input detected')."""


# --- parsing ----------------------------------------------------------------

def parse_show_version(output):
    """Extract hostname, IOS version and uptime. Values not found are None."""
    version_match = VERSION_PATTERN.search(output)
    uptime_match = UPTIME_PATTERN.search(output)
    return {
        "device_hostname": uptime_match.group(1) if uptime_match else None,
        "ios_version": version_match.group(1) if version_match else None,
        "uptime": uptime_match.group(2).strip() if uptime_match else None,
    }


def parse_ip_interface_brief(output):
    """Parse 'show ip interface brief' into a list of interface dictionaries.

    Example line:
        GigabitEthernet0/2     unassigned      YES NVRAM  administratively down down
    The Status column can contain a space ("administratively down"), so it is
    everything between the Method column and the last (Protocol) column.
    """
    interfaces = []
    for line in output.splitlines():
        parts = line.split()
        # Data lines have at least 6 columns and YES/NO in the "OK?" column.
        # This skips the header line, blank lines and any stray prompt text.
        if len(parts) < 6 or parts[2] not in ("YES", "NO"):
            continue
        interfaces.append({
            "interface": parts[0],
            "ip_address": parts[1],
            "status": " ".join(parts[4:-1]),
            "protocol": parts[-1],
        })
    return interfaces


def summarize_interfaces(interfaces):
    """Count interfaces and build a short one-line summary for the CSV report.

    An interface counts as up only when both Status and Protocol are "up".
    Everything else (down, administratively down, up/down) counts as down.
    """
    up = sum(1 for intf in interfaces if intf["status"] == "up" and intf["protocol"] == "up")
    summary = "; ".join(
        f"{intf['interface']} {intf['ip_address']} {intf['status']}/{intf['protocol']}" for intf in interfaces
    )
    return {
        "interface_count": len(interfaces),
        "interfaces_up": up,
        "interfaces_down": len(interfaces) - up,
        "ip_interface_summary": summary or None,
    }


def parse_interfaces_status(output):
    """Parse 'show interfaces status' (switches) into a list of {port, status}.

    The Name column is free text and may be empty, so the status is found by
    looking for the first known port state on each line.
    """
    ports = []
    for line in output.splitlines():
        parts = line.split()
        if not parts or parts[0] == "Port":
            continue
        status = next((part for part in parts[1:] if part in SWITCHPORT_STATES), None)
        if status:
            ports.append({"port": parts[0], "status": status})
    return ports


def summarize_switchports(ports):
    """Return e.g. '4 ports: 2 connected, 1 notconnect, 1 disabled', or None if no ports."""
    if not ports:
        return None
    counts = Counter(port["status"] for port in ports)
    details = ", ".join(f"{count} {status}" for status, count in counts.items())
    return f"{len(ports)} ports: {details}"


# --- collection from one device ---------------------------------------------

def run_show_command(connection, command):
    """Send a read-only show command and return its output.

    Raises UnsupportedCommandError if IOS rejects the command.
    """
    output = connection.send_command(command)
    if INVALID_COMMAND_PATTERN.search(output):
        raise UnsupportedCommandError(f"'{command}' is not supported on this device")
    return output


def collect_from_connection(connection, result):
    """Run the show commands on an open connection and fill in `result`.

    `result` is updated step by step, so data already collected is kept
    if a later command fails.
    """
    result.update(parse_show_version(run_show_command(connection, SHOW_VERSION)))

    interfaces = parse_ip_interface_brief(run_show_command(connection, SHOW_IP_INTERFACE_BRIEF))
    result.update(summarize_interfaces(interfaces))

    # Optional: switch-only command. Routers reject it, which is not a failure.
    try:
        ports = parse_interfaces_status(run_show_command(connection, SHOW_INTERFACES_STATUS))
        result["switchport_summary"] = summarize_switchports(ports)
    except UnsupportedCommandError:
        result["unsupported_commands"] = SHOW_INTERFACES_STATUS


def new_result(device):
    """Return an empty result for a device: every collected field starts as None."""
    result = dict.fromkeys(REPORT_FIELDS)
    result.update({
        "inventory_hostname": device.get("hostname"),
        "host": device.get("host"),
        "role": device.get("role"),
    })
    return result


def collect_device_info(device, credentials, timeout=DEFAULT_SSH_TIMEOUT):
    """Connect to one device, collect its information and always disconnect.

    Never raises: failures are recorded in `ssh_status` and `error` so that
    one bad device does not stop a multi-device run.

    ssh_status values: success, auth_failed, timeout, connection_failed, failed
    """
    result = new_result(device)
    connection = None
    try:
        connection = connect(device, credentials, timeout)
        collect_from_connection(connection, result)
        result["ssh_status"] = "success"
    except SSHConnectionError as exc:
        result["ssh_status"] = exc.status
        result["error"] = str(exc)
    except Exception as exc:  # command failure, read timeout, unexpected device output
        result["ssh_status"] = "failed"
        result["error"] = f"collection failed: {exc}"
    finally:
        disconnect(connection)

    result["checked_at"] = datetime.now().isoformat(timespec="seconds")
    return result


# --- multi-device workflow --------------------------------------------------

def run_device_collection(inventory_path=DEFAULT_INVENTORY_PATH, timeout=DEFAULT_SSH_TIMEOUT):
    """Collect information from every inventory device, one device at a time.

    Raises InventoryError or CredentialsError before any device is contacted
    if the inventory or credentials are not usable.
    """
    devices = load_inventory(inventory_path)
    credentials = get_credentials()
    return [collect_device_info(device, credentials, timeout) for device in devices]


def summarize_collection(results):
    """Count total, successful and failed devices."""
    succeeded = sum(1 for result in results if result["ssh_status"] == "success")
    return {"total": len(results), "succeeded": succeeded, "failed": len(results) - succeeded}


def format_results_table(results):
    return format_table(TABLE_HEADERS, [_table_row(result) for result in results])


def _table_row(result):
    interfaces = "-"
    if result["interface_count"] is not None:
        interfaces = f"{result['interfaces_up']}/{result['interface_count']}"
    return (
        result["inventory_hostname"],
        result["host"],
        result["ssh_status"],
        result["device_hostname"] or "-",
        result["ios_version"] or "-",
        result["uptime"] or "-",
        interfaces,
        result["error"] or "",
    )


def format_summary(summary):
    return (
        f"Total devices: {summary['total']}\n"
        f"Collected:     {summary['succeeded']}\n"
        f"Failed:        {summary['failed']}"
    )


# --- command line -----------------------------------------------------------

def dry_run(inventory_path):
    """Validate the inventory and credentials without opening any SSH connection."""
    print("Dry run: checking inventory and credentials. No SSH connections will be opened.\n")

    try:
        devices = load_inventory(inventory_path)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Inventory OK: {len(devices)} device(s) in {inventory_path}\n")
    rows = [(d["hostname"], d["host"], d["role"], d["device_type"]) for d in devices]
    print(format_table(("HOSTNAME", "HOST", "ROLE", "DEVICE TYPE"), rows))

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
        description="Collect show version and interface information from Cisco IOS lab devices over SSH."
    )
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY_PATH, help="path to the YAML inventory file")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="path of the CSV report to write")
    parser.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_SSH_TIMEOUT,
        help=f"SSH connection timeout in seconds (default: {DEFAULT_SSH_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="only validate the inventory and credentials; do not connect to any device",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args.inventory)

    print(f"Collecting device information using inventory: {args.inventory}\n")
    try:
        results = run_device_collection(args.inventory, timeout=args.timeout)
    except (InventoryError, CredentialsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = summarize_collection(results)
    print(format_results_table(results))
    print()
    print(format_summary(summary))

    try:
        report_path = write_csv_report(results, args.report, REPORT_FIELDS)
    except OSError as exc:
        print(f"\nERROR: could not write report {args.report}: {exc}", file=sys.stderr)
        return 2

    print(f"\nReport saved to: {report_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
