# Enterprise Network Automation Using Python

Python automation toolkit for a Cisco lab network simulated in GNS3. The project automates routine network operations tasks: keeping a device inventory, testing reachability, collecting device information, backing up configurations, and validating configurations against expected standards.

> **Status:** Phase 1 (Project Setup & Foundation) and Phase 2 (Device Connectivity Testing) are complete. Phases 3–6 are planned.

## Project Objectives

- Automate repetitive network administration tasks that are normally done by hand, device by device.
- Validate device connectivity and configuration in a consistent, repeatable way.
- Back up device configurations automatically.
- Test automation workflows safely in a simulated GNS3 lab before using them on production equipment.
- Keep the code simple, tested, and easy to maintain.

## Planned Capabilities

| Capability | Status |
|---|---|
| YAML-based Cisco device inventory with validation | Done (Phase 1) |
| ICMP reachability check for a single host | Done (Phase 1) |
| Reachability testing across the full inventory | Done (Phase 2) |
| Command-line connectivity check with summary | Done (Phase 2) |
| CSV connectivity report | Done (Phase 2) |
| SSH connectivity to Cisco IOS devices | Planned (Phase 3) |
| Device information collection (version, interfaces, etc.) | Planned (Phase 3) |
| Automated configuration backups | Planned (Phase 4) |
| Configuration validation against expected settings | Planned (Phase 5) |
| GNS3 lab integration and end-to-end testing | Planned (Phase 6) |

## Current Status

### Phase 1: Project Setup & Foundation (complete)

- **Device inventory** (`config/devices.yaml`): a small GNS3 lab with two routers (R1, R2) and one switch (SW1) on a private management network (`192.168.100.0/24`). Credentials are deliberately kept out of the inventory.
- **Inventory loader** (`src/inventory.py`): loads the YAML file and checks that every device has the required fields (`hostname`, `device_type`, `host`, `role`). Missing files, invalid YAML, empty fields and duplicate hostnames produce clear error messages.
- **Reachability check** (`src/connectivity.py`): pings a host using the operating system's `ping` command (Windows and Linux) and returns a structured result: reachable or not, packets sent/received, packet loss and average round-trip time. Errors are reported in the result instead of crashing the script. "Destination host unreachable" replies on Windows are correctly treated as failures.
- **Unit tests** (`tests/`): pytest suite covering valid and invalid inventories and ping output parsing. Ping calls are mocked, so the tests run without network access, real devices or GNS3.

### Phase 2: Device Connectivity Testing (complete)

- **Inventory-wide connectivity check** (`src/network_check.py`): loads every device from the inventory, pings each one with the Phase 1 ping logic, and combines inventory details (hostname, IP, role) with the ping results.
- **Fault tolerance:** an unreachable device or an unexpected error on one device is recorded in that device's result, and the run continues with the remaining devices. Only a missing or invalid inventory stops the run.
- **Command-line interface:** prints an aligned results table and a reachable/unreachable summary.
- **CSV report** (`src/reports.py`): results are saved to `data/reports/connectivity_report.csv`.

## Technologies

- Python 3.12
- PyYAML: device inventory
- pytest: unit testing
- GNS3: Cisco IOSv / IOSvL2 lab simulation
- Git / GitHub: version control

Netmiko (SSH automation) will be added when SSH features are built.

## Project Structure

```
enterprise-network-automation/
├── config/
│   └── devices.yaml          # Lab device inventory (no credentials)
├── data/
│   ├── backups/              # Configuration backups (generated, not committed)
│   └── reports/              # Generated reports (not committed)
├── src/
│   ├── __init__.py
│   ├── inventory.py          # Load and validate the device inventory
│   ├── connectivity.py       # ICMP reachability testing for one host
│   ├── network_check.py      # Inventory-wide connectivity check (CLI)
│   └── reports.py            # CSV report export
├── tests/
│   ├── __init__.py
│   ├── test_inventory.py
│   ├── test_connectivity.py
│   ├── test_network_check.py
│   └── test_reports.py
├── .gitignore
├── pytest.ini
├── requirements.txt
└── README.md
```

## Local Setup

Requires Python 3.10 or newer.

```bash
git clone https://github.com/udai9832/enterprise-network-automation.git
cd enterprise-network-automation
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running the Connectivity Check

From the project root, with the virtual environment activated:

```powershell
python -m src.network_check
```

Optional arguments:

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--report PATH` | `data/reports/connectivity_report.csv` | Where to save the CSV report |
| `--count N` | `2` | Echo requests sent to each device |
| `--timeout SECONDS` | `2` | Time to wait for each reply |

Example output (R2 powered off in the GNS3 lab):

```
Running connectivity check using inventory: C:\enterprise-network-automation\config\devices.yaml

HOSTNAME  HOST            ROLE           STATUS  LOSS  AVG RTT  ERROR
--------  --------------  -------------  ------  ----  -------  ---------------------------------
R1        192.168.100.11  core-router    UP      0%    2.5 ms
R2        192.168.100.12  edge-router    DOWN    100%  -        no replies received (exit code 1)
SW1       192.168.100.21  access-switch  UP      0%    3.0 ms

Total devices: 3
Reachable:     2
Unreachable:   1

Report saved to: C:\enterprise-network-automation\data\reports\connectivity_report.csv
```

If the GNS3 lab is not running, all devices are reported as `DOWN`. That is expected.

**Exit codes** (useful for scheduled tasks or scripts):

| Code | Meaning |
|---|---|
| `0` | All devices reachable |
| `1` | One or more devices unreachable |
| `2` | Check could not complete (inventory missing or invalid, or report could not be written) |

### CSV Connectivity Report

Each run overwrites `data/reports/connectivity_report.csv` with one row per device:

| Column | Description |
|---|---|
| `hostname` | Device hostname from the inventory |
| `host` | Management IP address |
| `role` | Device role (for example `core-router`) |
| `reachable` | `True` if at least one echo reply was received |
| `packets_sent` | Echo requests sent |
| `packets_received` | Echo replies received |
| `packet_loss_percent` | Packet loss in percent |
| `average_rtt_ms` | Average round-trip time in milliseconds (blank if no replies) |
| `error` | Reason the device was unreachable (blank if reachable) |
| `checked_at` | Local timestamp of the test |

Example:

```csv
hostname,host,role,reachable,packets_sent,packets_received,packet_loss_percent,average_rtt_ms,error,checked_at
R1,192.168.100.11,core-router,True,2,2,0.0,2.5,,2026-10-19T12:10:03
R2,192.168.100.12,edge-router,False,2,0,100.0,,no replies received (exit code 1),2026-10-19T12:10:08
```

The file opens directly in Excel. Generated reports are excluded from Git by `.gitignore`.

## Using the Modules in Python

```python
from src.network_check import run_connectivity_check, summarize_results

results = run_connectivity_check()
print(summarize_results(results))   # {'total': 3, 'reachable': 2, 'unreachable': 1}
```

## Running Tests

```bash
python -m pytest -v
```

The tests do not require internet access, Cisco devices or a running GNS3 lab. Ping calls are replaced with mocks.

## Roadmap

- [x] **Phase 1: Project Setup & Foundation** (complete). Project structure, device inventory, inventory validation, basic ping reachability, unit tests.
- [x] **Phase 2: Device Connectivity Testing** (complete). Reachability tests across the whole inventory, command-line summary, CSV connectivity report.
- [ ] **Phase 3: Cisco Device Information Collection.** SSH into devices and collect `show version`, interface status and other operational data.
- [ ] **Phase 4: Automated Configuration Backup.** Save running configurations to timestamped backup files.
- [ ] **Phase 5: Network Configuration Validation.** Check device configurations against expected settings and report deviations.
- [ ] **Phase 6: GNS3 Integration, Final Testing & GitHub Polish.** Run workflows end-to-end against the GNS3 lab, finalize tests and documentation.

## Security Note

This repository contains lab-only, private IP addresses and no credentials. Device credentials will be loaded from environment variables or a local file excluded by `.gitignore`.
