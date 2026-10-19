"""Generate the demonstration output in docs/demo_output/ from a simulated Cisco lab.

The project's real command-line tools (network_check, device_info,
config_backup, config_validation) are run unchanged, but against a simulated lab:

- ping replies come from canned Windows ping output (the real parser still runs)
- SSH sessions are fake sessions that return saved Cisco IOS outputs from
  tests/fixtures/ and only accept read-only "show" commands
- Netmiko's real ConnectHandler is blocked, so no SSH connection can be opened
- timestamps are fixed so the output is reproducible
- every run happens in a temporary folder with relative paths; the run is
  aborted if anything in the project's data/ folders changes

No real device, SSH session or network connection is used.

Run from the project root:
    python -m scripts.generate_demo_output
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

from src import config_backup, config_validation, device_info, network_check
from src.inventory import PROJECT_ROOT

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
DEMO_DIR = PROJECT_ROOT / "docs" / "demo_output"
PROJECT_DATA_DIRS = (PROJECT_ROOT / "data" / "backups", PROJECT_ROOT / "data" / "reports")
FIXED_TIME = datetime(2026, 10, 19, 10, 0, 0)

REPORTS = Path("data") / "reports"

# Default paths of every tool, pointed at the temporary workspace (relative paths).
WORKSPACE_DEFAULTS = [
    (network_check, "DEFAULT_INVENTORY_PATH", Path("config") / "devices.yaml"),
    (network_check, "DEFAULT_REPORT_PATH", REPORTS / "connectivity_report.csv"),
    (device_info, "DEFAULT_INVENTORY_PATH", Path("config") / "devices.yaml"),
    (device_info, "DEFAULT_REPORT_PATH", REPORTS / "device_info_report.csv"),
    (config_backup, "DEFAULT_INVENTORY_PATH", Path("config") / "devices.yaml"),
    (config_backup, "DEFAULT_REPORT_PATH", REPORTS / "config_backup_report.csv"),
    (config_backup, "BACKUP_DIR", Path("data") / "backups"),
    (config_validation, "DEFAULT_INVENTORY_PATH", Path("config") / "devices.yaml"),
    (config_validation, "DEFAULT_EXPECTED_PATH", Path("config") / "expected_config.yaml"),
    (config_validation, "DEFAULT_REPORT_PATH", REPORTS / "config_validation_report.csv"),
    (config_validation, "DEFAULT_SUMMARY_PATH", REPORTS / "config_validation_summary.csv"),
]

# Simulated round-trip times (ms) for the two echo requests sent to each device.
PING_RTTS = {"192.168.100.11": (2, 3), "192.168.100.12": (3, 4), "192.168.100.21": (4, 5)}

DEMO_HEADER = """\
DEMONSTRATION OUTPUT - generated from simulated/mock lab data.

The project's real command-line tools were run against a simulated Cisco lab:
ping replies and SSH sessions were replaced with fixture-backed fakes that
return saved Cisco IOS-style output (tests/fixtures/). No real device, SSH
session or network connection was used. This is not output from a GNS3 lab.

Regenerate with: python -m scripts.generate_demo_output
"""


class RealConnectionBlocked(BaseException):
    """Raised if anything tries to open a real SSH connection during the demo."""


# --- simulated lab ----------------------------------------------------------

def fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def lab_outputs():
    """Saved command outputs for each simulated device, keyed by management IP."""
    router_version = fixture("show_version_iosv.txt")
    return {
        "192.168.100.11": {
            "show version": router_version,
            "show ip interface brief": fixture("show_ip_interface_brief_router.txt"),
            "show interfaces status": fixture("invalid_input.txt"),
            "show running-config": fixture("show_running_config_r1.txt"),
        },
        "192.168.100.12": {
            "show version": router_version.replace("R1 uptime is 2 hours, 14 minutes", "R2 uptime is 2 hours, 11 minutes"),
            "show ip interface brief": fixture("show_ip_interface_brief_r2.txt"),
            "show interfaces status": fixture("invalid_input.txt"),
            "show running-config": fixture("show_running_config_r2.txt"),
        },
        "192.168.100.21": {
            "show version": fixture("show_version_iosvl2.txt"),
            "show ip interface brief": fixture("show_ip_interface_brief_switch.txt"),
            "show interfaces status": fixture("show_interfaces_status_switch.txt"),
            "show running-config": fixture("show_running_config_sw1.txt"),
        },
    }


class SimulatedSession:
    """Stands in for a Netmiko connection. Only read-only show commands are accepted."""

    def __init__(self, outputs):
        self.outputs = outputs

    def send_command(self, command, **kwargs):
        if not command.startswith("show "):
            raise RuntimeError(f"simulated session refused a non-show command: {command}")
        return self.outputs[command]

    def disconnect(self):
        pass


def simulated_connect(outputs):
    def _connect(device, credentials, timeout=10):
        return SimulatedSession(outputs[device["host"]])
    return _connect


def simulated_ping(command, **kwargs):
    """Return Windows-style ping output for a simulated lab host. Anything else is refused."""
    host = command[-1]
    if command[0] != "ping" or host not in PING_RTTS:
        raise RuntimeError(f"simulated ping refused: {command}")
    replies = [f"Reply from {host}: bytes=32 time={rtt}ms TTL=255" for rtt in PING_RTTS[host]]
    output = "\n".join([f"Pinging {host} with 32 bytes of data:", *replies, ""])
    return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


def _block_real_ssh(**params):
    raise RealConnectionBlocked(f"real SSH connection attempted to {params.get('host')}")


def _snapshot_project_data():
    """(path, modified time) of every file in the project's real data/backups and data/reports."""
    return {(p, p.stat().st_mtime_ns) for folder in PROJECT_DATA_DIRS for p in folder.rglob("*") if p.is_file()}


def run_in_simulated_lab(outputs, main):
    """Run one tool's main([]) in a temporary workspace against the simulated lab.

    Returns (printed output, exit code, {"data/reports/...": text} for every report and backup written).
    """
    previous_dir = os.getcwd()
    project_data_before = _snapshot_project_data()

    with tempfile.TemporaryDirectory() as workspace, ExitStack() as stack:
        workspace = Path(workspace)
        (workspace / "config").mkdir()
        for name in ("devices.yaml", "expected_config.yaml"):
            shutil.copy(PROJECT_ROOT / "config" / name, workspace / "config" / name)

        stack.enter_context(mock.patch("src.ssh_client.ConnectHandler", _block_real_ssh))
        stack.enter_context(mock.patch("src.connectivity.subprocess.run", simulated_ping))
        stack.enter_context(mock.patch("src.connectivity.platform.system", return_value="Windows"))
        for module in (device_info, config_backup, config_validation):
            stack.enter_context(mock.patch.object(module, "connect", simulated_connect(outputs)))
        for module in (network_check, device_info, config_backup, config_validation):
            fake_datetime = stack.enter_context(mock.patch.object(module, "datetime"))
            fake_datetime.now.return_value = FIXED_TIME
        for module, name, value in WORKSPACE_DEFAULTS:
            stack.enter_context(mock.patch.object(module, name, value))
        stack.enter_context(mock.patch.dict(os.environ, {
            "NETWORK_USERNAME": "demo-user",
            "NETWORK_PASSWORD": "demo-only-password",
        }))

        os.chdir(workspace)
        try:
            printed = io.StringIO()
            with redirect_stdout(printed), redirect_stderr(printed):
                exit_code = main([])
            files = {
                path.relative_to(workspace).as_posix(): path.read_text(encoding="utf-8")
                for path in sorted(workspace.rglob("*"))
                if path.is_file() and path.parent.name in ("reports", "backups")
            }
        finally:
            os.chdir(previous_dir)

    if _snapshot_project_data() != project_data_before:
        raise RuntimeError("demo run changed files in the project's data/ folders")
    return printed.getvalue(), exit_code, files


# --- formatting -------------------------------------------------------------

def format_run(module_name, output, exit_code):
    return f"$ python -m {module_name}\n\n{output.rstrip()}\n\n[exit code {exit_code}]\n"


def format_csv(path, text):
    return f"--- {path} ---\n{text.rstrip()}\n"


# --- demo files -------------------------------------------------------------

def connectivity_demo():
    output, exit_code, files = run_in_simulated_lab(lab_outputs(), network_check.main)
    report = "data/reports/connectivity_report.csv"
    return "\n".join([
        DEMO_HEADER,
        "Scenario: all three lab devices answer ping.\n",
        format_run("src.network_check", output, exit_code),
        format_csv(report, files[report]),
    ])


def device_info_demo():
    output, exit_code, files = run_in_simulated_lab(lab_outputs(), device_info.main)
    report = "data/reports/device_info_report.csv"
    return "\n".join([
        DEMO_HEADER,
        "Scenario: SSH collection from R1 and R2 (IOSv-style routers) and SW1 (IOSvL2-style switch).",
        "The routers reject 'show interfaces status', which is recorded in unsupported_commands.\n",
        format_run("src.device_info", output, exit_code),
        format_csv(report, files[report]),
    ])


def config_backup_demo():
    output, exit_code, files = run_in_simulated_lab(lab_outputs(), config_backup.main)
    report = "data/reports/config_backup_report.csv"
    backups = [name.split("/")[-1] for name in files if name.startswith("data/backups/")]
    return "\n".join([
        DEMO_HEADER,
        "Scenario: running-config backup of all three devices.",
        "Configuration contents are not shown here; only the summary and file names.\n",
        format_run("src.config_backup", output, exit_code),
        format_csv(report, files[report]),
        "--- files created in data/backups/ ---",
        *backups,
        "",
    ])


def config_validation_demo():
    output, exit_code, files = run_in_simulated_lab(lab_outputs(), config_validation.main)
    summary = "data/reports/config_validation_summary.csv"

    drifted = lab_outputs()
    r2, sw1 = drifted["192.168.100.12"], drifted["192.168.100.21"]
    r2["show running-config"] = r2["show running-config"].replace(" network 10.0.12.0 0.0.0.3 area 0\n", "")
    sw1["show running-config"] = sw1["show running-config"].replace("no ip http server", "ip http server")
    drift_output, drift_exit_code, drift_files = run_in_simulated_lab(drifted, config_validation.main)

    return "\n".join([
        DEMO_HEADER,
        "Scenario 1: all three simulated devices match config/expected_config.yaml.\n",
        format_run("src.config_validation", output, exit_code),
        format_csv(summary, files[summary]),
        "=" * 79,
        "",
        "Scenario 2: configuration drift. In the simulated running-configs, R2 is missing",
        "its OSPF 'network 10.0.12.0 0.0.0.3 area 0' line and SW1 has 'ip http server' enabled.\n",
        format_run("src.config_validation", drift_output, drift_exit_code),
        format_csv(summary, drift_files[summary]),
    ])


SIMULATED_DEMOS = {
    "connectivity_demo.txt": connectivity_demo,
    "device_info_demo.txt": device_info_demo,
    "config_backup_demo.txt": config_backup_demo,
    "config_validation_demo.txt": config_validation_demo,
}


def generate_simulated_demos(output_dir=DEMO_DIR):
    """Write the four simulated-lab demo files and return their paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, build in SIMULATED_DEMOS.items():
        path = output_dir / name
        path.write_text(build(), encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def pytest_demo():
    """Run the real test suite and return its output with a per-file test count."""
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    run = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
    collected = subprocess.run(command + ["--collect-only"], cwd=PROJECT_ROOT, capture_output=True, text=True)

    counts = {}
    for line in collected.stdout.splitlines():
        if "::" in line:
            test_file = line.split("::")[0]
            counts[test_file] = counts.get(test_file, 0) + 1
    table = "\n".join(f"{count:>5}  {name}" for name, count in sorted(counts.items()))

    return "\n".join([
        "AUTOMATED TEST SUITE OUTPUT",
        "",
        "Result of running the project's pytest suite. All Cisco device interactions in",
        "the tests are simulated (mocked Netmiko sessions and saved Cisco IOS-style output),",
        "so no network, GNS3 lab or Cisco device is needed to run it.",
        "",
        "Regenerate with: python -m scripts.generate_demo_output",
        "",
        "$ python -m pytest -q",
        "",
        run.stdout.rstrip(),
        "",
        "Tests per file:",
        table,
        "",
    ])


def main():
    for path in generate_simulated_demos():
        print(f"Written: {path.relative_to(PROJECT_ROOT)}")
    pytest_path = DEMO_DIR / "pytest_demo.txt"
    pytest_path.write_text(pytest_demo(), encoding="utf-8", newline="\n")
    print(f"Written: {pytest_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
