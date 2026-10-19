"""Write automation results to report files in data/reports/ and format them for the terminal."""

import csv
from pathlib import Path

from src.inventory import PROJECT_ROOT

REPORTS_DIR = PROJECT_ROOT / "data" / "reports"


def write_csv_report(rows, path, fieldnames):
    """Write a list of dictionaries to a CSV file and return the file path.

    Only the columns listed in `fieldnames` are written, in that order.
    The parent folder is created if it does not exist yet.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as report_file:
        writer = csv.DictWriter(report_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return path


def format_table(headers, rows):
    """Return rows of values as an aligned plain-text table with a dashed line under the headers."""
    rows = [tuple(headers)] + [tuple(str(value) for value in row) for row in rows]
    widths = [max(len(row[column]) for row in rows) for column in range(len(headers))]

    lines = ["  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)
