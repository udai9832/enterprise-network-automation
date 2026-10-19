"""Test ICMP reachability for every device in the inventory.

Run from the project root:
    python -m src.network_check

Exit codes:
    0 = all devices reachable
    1 = one or more devices unreachable
    2 = inventory or report error (check could not complete)
"""

import argparse
import sys
from datetime import datetime

from src.connectivity import PingResult, ping_host
from src.inventory import DEFAULT_INVENTORY_PATH, InventoryError, load_inventory
from src.reports import REPORTS_DIR, format_table, write_csv_report

DEFAULT_REPORT_PATH = REPORTS_DIR / "connectivity_report.csv"

# Columns of the CSV report, in order.
REPORT_FIELDS = [
    "hostname",
    "host",
    "role",
    "reachable",
    "packets_sent",
    "packets_received",
    "packet_loss_percent",
    "average_rtt_ms",
    "error",
    "checked_at",
]

TABLE_HEADERS = ("HOSTNAME", "HOST", "ROLE", "STATUS", "LOSS", "AVG RTT", "ERROR")


def check_device(device, count=2, timeout=2):
    """Ping one inventory device and return the inventory details plus the ping result."""
    host = device.get("host")
    try:
        ping = ping_host(host, count=count, timeout=timeout)
    except Exception as exc:  # one bad device must not stop the whole run
        ping = PingResult(
            host=str(host),
            reachable=False,
            packets_sent=0,
            packets_received=0,
            packet_loss=100.0,
            error=f"unexpected error: {exc}",
        )

    return {
        "hostname": device.get("hostname"),
        "host": host,
        "role": device.get("role"),
        "reachable": ping.reachable,
        "packets_sent": ping.packets_sent,
        "packets_received": ping.packets_received,
        "packet_loss_percent": ping.packet_loss,
        "average_rtt_ms": ping.avg_rtt_ms,
        "error": ping.error,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def run_connectivity_check(inventory_path=DEFAULT_INVENTORY_PATH, count=2, timeout=2):
    """Load the inventory and test every device. Returns a list of result dictionaries.

    Raises InventoryError if the inventory cannot be loaded.
    """
    devices = load_inventory(inventory_path)
    return [check_device(device, count=count, timeout=timeout) for device in devices]


def summarize_results(results):
    """Count total, reachable and unreachable devices."""
    reachable = sum(1 for result in results if result["reachable"])
    return {
        "total": len(results),
        "reachable": reachable,
        "unreachable": len(results) - reachable,
    }


def format_results_table(results):
    """Return the results as an aligned text table."""
    return format_table(TABLE_HEADERS, [_table_row(result) for result in results])


def _table_row(result):
    rtt = result["average_rtt_ms"]
    return (
        str(result["hostname"]),
        str(result["host"]),
        str(result["role"]),
        "UP" if result["reachable"] else "DOWN",
        f"{result['packet_loss_percent']:.0f}%",
        f"{rtt} ms" if rtt is not None else "-",
        result["error"] or "",
    )


def format_summary(summary):
    return (
        f"Total devices: {summary['total']}\n"
        f"Reachable:     {summary['reachable']}\n"
        f"Unreachable:   {summary['unreachable']}"
    )


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Ping every device in the inventory and save a CSV report.")
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY_PATH, help="path to the YAML inventory file")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="path of the CSV report to write")
    parser.add_argument("--count", type=positive_int, default=2, help="echo requests per device (default: 2)")
    parser.add_argument("--timeout", type=positive_int, default=2, help="seconds to wait per reply (default: 2)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print(f"Running connectivity check using inventory: {args.inventory}\n")

    try:
        results = run_connectivity_check(args.inventory, count=args.count, timeout=args.timeout)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = summarize_results(results)
    print(format_results_table(results))
    print()
    print(format_summary(summary))

    try:
        report_path = write_csv_report(results, args.report, REPORT_FIELDS)
    except OSError as exc:
        print(f"\nERROR: could not write report {args.report}: {exc}", file=sys.stderr)
        return 2

    print(f"\nReport saved to: {report_path}")
    return 0 if summary["unreachable"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
