"""Tests for scripts/generate_demo_output.py and the committed demo files in docs/demo_output/.

The generator runs the real CLI tools against a simulated lab. These tests make
sure it stays isolated: no real SSH, no real ping, nothing written to the
project's data/ folders, and the committed demo files match what the code produces.
"""

import subprocess

import pytest

from scripts import generate_demo_output as demo
from src import connectivity, ssh_client
from src.inventory import PROJECT_ROOT
from tests.conftest import _snapshot_output_dirs

DEMO_FILES = list(demo.SIMULATED_DEMOS)
DISCLOSURE = "DEMONSTRATION OUTPUT - generated from simulated/mock lab data."
R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Generate the four simulated demos once into a temporary folder."""
    folder = tmp_path_factory.mktemp("demo_output")
    demo.generate_simulated_demos(folder)
    return {name: (folder / name).read_text(encoding="utf-8") for name in DEMO_FILES}


def normalize(text):
    """Make output comparable across Windows and Linux (path separators, line endings)."""
    return text.replace("\r\n", "\n").replace("\\", "/")


# --- generated content ------------------------------------------------------

def test_all_four_demos_are_generated(generated):
    assert sorted(generated) == sorted([
        "connectivity_demo.txt", "device_info_demo.txt", "config_backup_demo.txt", "config_validation_demo.txt",
    ])


@pytest.mark.parametrize("name", DEMO_FILES)
def test_demo_starts_with_simulated_disclosure(generated, name):
    text = generated[name]

    assert text.startswith(DISCLOSURE)
    assert "No real device, SSH" in text
    assert "This is not output from a GNS3 lab." in text


@pytest.mark.parametrize("name", DEMO_FILES)
def test_demo_contains_no_credentials_or_personal_paths(generated, name):
    text = generated[name]

    for forbidden in ("demo-only-password", "demo-user", "NETWORK_PASSWORD", "NETWORK_USERNAME",
                      ":\\", "\\Users\\", "/Users/", "/home/", "AppData"):
        assert forbidden not in text, forbidden


@pytest.mark.parametrize("name", DEMO_FILES)
def test_demo_uses_fixed_timestamp_and_relative_paths(generated, name):
    text = normalize(generated[name])

    assert "2026-10-19T10:00:00" in text
    assert "data/reports/" in text


def test_connectivity_demo(generated):
    text = normalize(generated["connectivity_demo.txt"])

    assert "$ python -m src.network_check" in text
    assert "Reachable:     3" in text
    assert "R1        192.168.100.11  core-router    UP      0%    2.5 ms" in text
    assert "--- data/reports/connectivity_report.csv ---" in text
    assert "[exit code 0]" in text


def test_device_info_demo(generated):
    text = normalize(generated["device_info_demo.txt"])

    assert "$ python -m src.device_info" in text
    assert "Collected:     3" in text
    assert "15.9(3)M6" in text and "15.2(20170321:233949)" in text
    assert "show interfaces status" in text   # recorded as unsupported on the routers


def test_backup_demo_shows_summary_but_not_config_contents(generated):
    text = normalize(generated["config_backup_demo.txt"])

    assert "Successful backups: 3" in text
    assert "R1_20261019_100000.cfg" in text
    assert "SW1_20261019_100000.cfg" in text
    assert "enable secret" not in text
    assert "username admin" not in text


def test_validation_demo_has_compliant_and_drift_scenarios(generated):
    text = normalize(generated["config_validation_demo.txt"])

    assert "R1      PASS    11/11" in text
    assert "R2      FAIL    10/11" in text
    assert "SW1     FAIL    10/11" in text
    assert "prohibited configuration line found" in text
    assert text.count("[exit code 0]") == 1
    assert text.count("[exit code 1]") == 1


def test_demo_output_is_deterministic(generated, tmp_path):
    demo.generate_simulated_demos(tmp_path)

    for name in DEMO_FILES:
        assert (tmp_path / name).read_text(encoding="utf-8") == generated[name]


@pytest.mark.parametrize("name", DEMO_FILES)
def test_committed_demo_matches_regenerated_output(generated, name):
    committed = (demo.DEMO_DIR / name).read_text(encoding="utf-8")

    assert normalize(committed) == normalize(generated[name]), (
        f"docs/demo_output/{name} is out of date: run python -m scripts.generate_demo_output"
    )


def test_committed_pytest_demo_has_disclosure_and_result():
    text = (demo.DEMO_DIR / "pytest_demo.txt").read_text(encoding="utf-8")

    assert text.startswith("AUTOMATED TEST SUITE OUTPUT")
    assert "All Cisco device interactions" in text
    assert "simulated" in text
    assert "$ python -m pytest -q" in text
    assert " passed in " in text
    assert "Tests per file:" in text


# --- isolation --------------------------------------------------------------

def test_generator_does_not_touch_project_data_folders(tmp_path):
    before = _snapshot_output_dirs()

    demo.generate_simulated_demos(tmp_path)

    assert _snapshot_output_dirs() == before
    assert sorted(p.name for p in (PROJECT_ROOT / "data" / "backups").iterdir()) == [".gitkeep"]


def test_generator_aborts_if_a_run_writes_to_project_data():
    stray = PROJECT_ROOT / "data" / "reports" / "_demo_isolation_check.csv"

    def writes_to_project_data(argv):
        stray.write_text("x", encoding="utf-8")
        return 0

    try:
        with pytest.raises(RuntimeError, match="changed files in the project's data/ folders"):
            demo.run_in_simulated_lab(demo.lab_outputs(), writes_to_project_data)
    finally:
        stray.unlink(missing_ok=True)


def test_real_netmiko_connect_handler_is_blocked():
    def opens_real_ssh(argv):
        ssh_client.connect(R1, {"username": "u", "password": "p"})
        return 0

    with pytest.raises(demo.RealConnectionBlocked, match="real SSH connection attempted to 192.168.100.11"):
        demo.run_in_simulated_lab(demo.lab_outputs(), opens_real_ssh)


def test_ping_is_simulated_not_executed():
    results = {}

    def pings_r1(argv):
        results["r1"] = connectivity.ping_host("192.168.100.11")
        return 0

    demo.run_in_simulated_lab(demo.lab_outputs(), pings_r1)

    assert results["r1"].reachable is True
    assert results["r1"].avg_rtt_ms == 2.5   # the simulated 2 ms / 3 ms replies


@pytest.mark.parametrize("command", [["ping", "-n", "2", "8.8.8.8"], ["nmap", "192.168.100.11"]])
def test_simulated_ping_refuses_anything_else(command):
    with pytest.raises(RuntimeError, match="simulated ping refused"):
        demo.simulated_ping(command)


def test_subprocess_run_is_restored_after_a_demo_run():
    original = subprocess.run

    demo.run_in_simulated_lab(demo.lab_outputs(), lambda argv: 0)

    assert subprocess.run is original
    assert connectivity.subprocess.run is original


def test_simulated_session_refuses_non_show_commands():
    session = demo.SimulatedSession({"show version": "x"})

    assert session.send_command("show version") == "x"
    for command in ("configure terminal", "write memory", "reload"):
        with pytest.raises(RuntimeError, match="refused a non-show command"):
            session.send_command(command)


def test_simulated_lab_uses_only_inventory_hosts():
    from src.inventory import load_inventory

    hosts = {device["host"] for device in load_inventory()}
    assert set(demo.lab_outputs()) == hosts
    assert set(demo.PING_RTTS) == hosts
