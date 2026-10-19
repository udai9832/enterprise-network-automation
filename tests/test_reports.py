"""Tests for src/reports.py."""

import csv

from src.reports import REPORTS_DIR, write_csv_report


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as report_file:
        return list(csv.DictReader(report_file))


def test_write_csv_report(tmp_path):
    rows = [
        {"hostname": "R1", "reachable": True},
        {"hostname": "R2", "reachable": False},
    ]
    path = write_csv_report(rows, tmp_path / "report.csv", ["hostname", "reachable"])

    assert path.is_file()
    assert read_csv(path) == [
        {"hostname": "R1", "reachable": "True"},
        {"hostname": "R2", "reachable": "False"},
    ]


def test_write_csv_report_creates_missing_folder(tmp_path):
    path = write_csv_report([{"hostname": "R1"}], tmp_path / "new" / "folder" / "report.csv", ["hostname"])

    assert path.is_file()


def test_write_csv_report_uses_given_column_order_and_ignores_extra_keys(tmp_path):
    rows = [{"b": 2, "a": 1, "extra": "ignored"}]
    path = write_csv_report(rows, tmp_path / "report.csv", ["a", "b"])

    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "a,b"


def test_write_csv_report_blank_for_missing_values(tmp_path):
    path = write_csv_report([{"hostname": "R1", "error": None}], tmp_path / "report.csv", ["hostname", "error"])

    assert read_csv(path) == [{"hostname": "R1", "error": ""}]


def test_reports_dir_is_data_reports():
    assert REPORTS_DIR.parts[-2:] == ("data", "reports")
