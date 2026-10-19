# Enterprise Network Automation Using Python

Python automation toolkit for a Cisco lab network simulated in GNS3. The project automates routine network operations tasks: keeping a device inventory, testing reachability, collecting device information, backing up configurations, and validating configurations against expected standards.

> **Status:** Phase 1 (Project Setup & Foundation) is complete. Later phases are in progress. See the [Roadmap](#roadmap).

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
| Reachability testing across the full inventory with reports | Planned (Phase 2) |
| SSH connectivity to Cisco IOS devices | Planned (Phase 2–3) |
| Device information collection (version, interfaces, etc.) | Planned (Phase 3) |
| Automated configuration backups | Planned (Phase 4) |
| Configuration validation against expected settings | Planned (Phase 5) |
| GNS3 lab integration and end-to-end testing | Planned (Phase 6) |

## Current Status: Phase 1

What's done so far:

- **Device inventory** (`config/devices.yaml`): a small GNS3 lab with two routers (R1, R2) and one switch (SW1) on a private management network (`192.168.100.0/24`). Credentials are deliberately kept out of the inventory.
- **Inventory loader** (`src/inventory.py`): loads the YAML file and checks that every device has the required fields (`hostname`, `device_type`, `host`, `role`). Missing files, invalid YAML, empty fields and duplicate hostnames produce clear error messages.
- **Reachability check** (`src/connectivity.py`): pings a host using the operating system's `ping` command (Windows and Linux) and returns a structured result: reachable or not, packets sent/received, packet loss and average round-trip time. Errors are reported in the result instead of crashing the script. "Destination host unreachable" replies on Windows are correctly treated as failures.
- **Unit tests** (`tests/`): pytest suite covering valid and invalid inventories and ping output parsing. Ping calls are mocked, so the tests run without network access, real devices or GNS3.

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
│   └── connectivity.py       # ICMP reachability testing
├── tests/
│   ├── __init__.py
│   ├── test_inventory.py
│   └── test_connectivity.py
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

## Usage Example

```python
from src.inventory import load_inventory
from src.connectivity import ping_host

for device in load_inventory():
    result = ping_host(device["host"])
    status = "UP" if result.reachable else "DOWN"
    print(f"{device['hostname']:<5} {device['host']:<16} {status}")
```

## Running Tests

```bash
python -m pytest -v
```

The tests do not require internet access, Cisco devices or a running GNS3 lab.

## Roadmap

- [x] **Phase 1: Project Setup & Foundation.** Project structure, device inventory, inventory validation, basic ping reachability, unit tests.
- [ ] **Phase 2: Device Connectivity Testing.** Reachability tests across the whole inventory, SSH port checks, connectivity reports.
- [ ] **Phase 3: Cisco Device Information Collection.** SSH into devices and collect `show version`, interface status and other operational data.
- [ ] **Phase 4: Automated Configuration Backup.** Save running configurations to timestamped backup files.
- [ ] **Phase 5: Network Configuration Validation.** Check device configurations against expected settings and report deviations.
- [ ] **Phase 6: GNS3 Integration, Final Testing & GitHub Polish.** Run workflows end-to-end against the GNS3 lab, finalize tests and documentation.

## Security Note

This repository contains lab-only, private IP addresses and no credentials. Device credentials will be loaded from environment variables or a local file excluded by `.gitignore`.
