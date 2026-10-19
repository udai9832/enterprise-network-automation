"""Write automation results to report files in data/reports/."""

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
