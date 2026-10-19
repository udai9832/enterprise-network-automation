"""Back up the running configuration of Cisco IOS lab devices over SSH.

Only one read-only command is sent to each device:
    show running-config
The device is never put into configuration mode and nothing is saved or
changed on it. (Netmiko itself sets "terminal length 0" / "terminal width"
for the SSH session only; these do not change the device configuration.)

Backups are written to data/backups/ as <HOSTNAME>_<YYYYMMDD>_<HHMMSS>.cfg

Run from the project root once the GNS3 lab is reachable:
    python -m src.config_backup

Validate inventory and credentials without connecting or writing files:
    python -m src.config_backup --dry-run

Exit codes:
    0 = all devices backed up
    1 = one or more backups failed
    2 = inventory, credentials or report error (backup could not run)
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

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

BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
DEFAULT_REPORT_PATH = REPORTS_DIR / "config_backup_report.csv"

SHOW_RUNNING_CONFIG = "show running-config"
COMMAND_READ_TIMEOUT = 60  # seconds; large configurations can take a while to print
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

SUCCESS = "SUCCESS"
FAILED = "FAILED"

# Columns of the CSV report, in order. Credentials are never part of a result.
REPORT_FIELDS = ["hostname", "host", "role", "status", "backup_path", "bytes_written", "error", "backed_up_at"]

TABLE_HEADERS = ("HOSTNAME", "HOST", "ROLE", "STATUS", "BACKUP", "ERROR")

# Anything other than letters, digits, dot, dash and underscore is replaced in filenames.
UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class BackupError(Exception):
    """Raised when a usable configuration could not be retrieved or saved."""


# --- filenames --------------------------------------------------------------

def sanitize_hostname(hostname):
    """Make a hostname safe to use in a filename on Windows and Linux.

    'R1' stays 'R1'; 'core/rtr:1' becomes 'core_rtr_1'. Leading and trailing
    dots/underscores are removed so the name can never be '..' or a hidden file.
    """
    safe = UNSAFE_FILENAME_CHARS.sub("_", str(hostname)).strip("._")
    return safe or "device"


def build_backup_filename(hostname, timestamp, counter=0):
    """Return e.g. 'R1_20261019_125500.cfg', or 'R1_20261019_125500_1.cfg' when counter is 1."""
    name = f"{sanitize_hostname(hostname)}_{timestamp.strftime(TIMESTAMP_FORMAT)}"
    if counter:
        name += f"_{counter}"
    return f"{name}.cfg"


# --- retrieving the configuration -------------------------------------------

def check_running_config(output):
    """Raise BackupError if the device output is not a usable running configuration.

    Rejects empty output, Cisco CLI errors such as '% Invalid input detected'
    or '% Authorization failed', and output that does not end with 'end'
    (a sign that the configuration was cut off).
    """
    lines = output.strip().splitlines() if output else []
    if not lines:
        raise BackupError(f"device returned empty output for '{SHOW_RUNNING_CONFIG}'")

    # CLI errors appear at the top of the output. Only the first lines are checked
    # so that text inside the config (for example a login banner) is not mistaken for an error.
    for line in lines[:3]:
        line = line.strip()
        if line.startswith("%") or "authorization failed" in line.lower():
            raise BackupError(f"device rejected '{SHOW_RUNNING_CONFIG}': {line}")

    if lines[-1].strip() != "end":
        raise BackupError("running-config output looks incomplete (missing final 'end' line)")


def get_running_config(connection):
    """Send 'show running-config' and return the validated output."""
    output = connection.send_command(SHOW_RUNNING_CONFIG, read_timeout=COMMAND_READ_TIMEOUT)
    check_running_config(output)
    return output


# --- saving the configuration -----------------------------------------------

def save_backup(config_text, backup_dir, hostname, timestamp):
    """Write the configuration to a new backup file and return (path, bytes_written).

    An existing backup is never overwritten: if a file with the same name exists
    (two backups of one device in the same second), _1, _2, ... is appended.
    The text is saved exactly as returned by the device.
    """
    backup_dir = Path(backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        counter = 0
        while True:
            path = backup_dir / build_backup_filename(hostname, timestamp, counter)
            try:
                return path, _write_new_file(path, config_text)
            except FileExistsError:
                counter += 1
    except OSError as exc:
        raise BackupError(f"could not write backup file in {backup_dir}: {exc}") from exc


def _write_new_file(path, text):
    """Create a new file (fails if it already exists) and return the number of bytes written."""
    try:
        # "x" mode = create only, never overwrite. newline="" keeps the device's line endings.
        with path.open("x", encoding="utf-8", newline="") as backup_file:
            backup_file.write(text)
    except FileExistsError:
        raise
    except OSError:
        path.unlink(missing_ok=True)  # never leave a half-written backup behind
        raise
    return path.stat().st_size


# --- one device -------------------------------------------------------------

def backup_device(device, credentials, backup_dir=BACKUP_DIR, timeout=DEFAULT_SSH_TIMEOUT):
    """Back up one device's running configuration. Always disconnects.

    Never raises: failures are recorded in `status` and `error` so that one bad
    device does not stop a multi-device backup run.
    """
    started = datetime.now()
    result = {
        "hostname": device.get("hostname"),
        "host": device.get("host"),
        "role": device.get("role"),
        "status": FAILED,
        "backup_path": None,
        "bytes_written": None,
        "error": None,
        "backed_up_at": started.isoformat(timespec="seconds"),
    }

    connection = None
    try:
        connection = connect(device, credentials, timeout)
        config_text = get_running_config(connection)
        path, size = save_backup(config_text, backup_dir, device["hostname"], started)
        result.update(status=SUCCESS, backup_path=str(path), bytes_written=size)
    except (SSHConnectionError, BackupError) as exc:
        result["error"] = str(exc)
    except Exception as exc:  # e.g. session dropped while reading output
        result["error"] = f"backup failed: {exc}"
    finally:
        disconnect(connection)

    return result


# --- all devices ------------------------------------------------------------

def run_backup(inventory_path=DEFAULT_INVENTORY_PATH, backup_dir=BACKUP_DIR, timeout=DEFAULT_SSH_TIMEOUT):
    """Back up every inventory device, one device at a time.

    Raises InventoryError or CredentialsError before any device is contacted
    if the inventory or credentials are not usable.
    """
    devices = load_inventory(inventory_path)
    credentials = get_credentials()
    return [backup_device(device, credentials, backup_dir, timeout) for device in devices]


def summarize_backups(results):
    """Count total, successful and failed backups."""
    succeeded = sum(1 for result in results if result["status"] == SUCCESS)
    return {"total": len(results), "succeeded": succeeded, "failed": len(results) - succeeded}


def format_results_table(results):
    rows = [
        (
            result["hostname"],
            result["host"],
            result["role"],
            result["status"],
            Path(result["backup_path"]).name if result["backup_path"] else "-",
            result["error"] or "",
        )
        for result in results
    ]
    return format_table(TABLE_HEADERS, rows)


def format_summary(summary):
    return (
        f"Total devices: {summary['total']}\n"
        f"Successful backups: {summary['succeeded']}\n"
        f"Failed backups: {summary['failed']}"
    )


# --- command line -----------------------------------------------------------

def dry_run(inventory_path, backup_dir):
    """Validate inventory and credentials. Opens no SSH connection and writes no files."""
    print("Dry run: checking inventory and credentials. No SSH connections will be opened and no files will be written.\n")

    try:
        devices = load_inventory(inventory_path)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Inventory OK: {len(devices)} device(s) in {inventory_path}")
    print(f"Backup directory: {backup_dir}\n")
    rows = [
        (d["hostname"], d["host"], d["role"], f"{sanitize_hostname(d['hostname'])}_YYYYMMDD_HHMMSS.cfg")
        for d in devices
    ]
    print(format_table(("HOSTNAME", "HOST", "ROLE", "BACKUP FILE"), rows))

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
        description="Back up the running configuration of Cisco IOS lab devices over SSH."
    )
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY_PATH, help="path to the YAML inventory file")
    parser.add_argument("--backup-dir", default=BACKUP_DIR, help="folder where .cfg backups are saved")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="path of the CSV summary report")
    parser.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_SSH_TIMEOUT,
        help=f"SSH connection timeout in seconds (default: {DEFAULT_SSH_TIMEOUT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="only validate the inventory and credentials; do not connect or write any file",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args.inventory, args.backup_dir)

    print(f"Backing up running configurations using inventory: {args.inventory}\n")
    try:
        results = run_backup(args.inventory, args.backup_dir, timeout=args.timeout)
    except (InventoryError, CredentialsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = summarize_backups(results)
    print(format_results_table(results))
    print()
    print(format_summary(summary))
    print(f"\nBackup directory: {args.backup_dir}")

    try:
        report_path = write_csv_report(results, args.report, REPORT_FIELDS)
    except OSError as exc:
        print(f"\nERROR: could not write report {args.report}: {exc}", file=sys.stderr)
        return 2

    print(f"Report saved to: {report_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
