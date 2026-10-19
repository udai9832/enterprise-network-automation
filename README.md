# Enterprise Network Automation Using Python

## Overview

A Python-based Cisco network automation portfolio project that demonstrates device inventory management, reachability testing, SSH-based operational data collection, configuration backup, and configuration validation.

Everything starts from a single YAML inventory of lab devices (two routers and a switch). Each tool processes the devices one at a time, never lets one failing device stop the run, and reports results as terminal tables and CSV files.

The project is verified through an automated pytest suite, mocked Netmiko sessions, realistic Cisco IOS-style CLI fixtures, configuration fixtures and consistency tests. Optional GNS3 deployment documentation is included for anyone who wants to run the project against a dedicated virtual Cisco lab.

## Key Features

- **Device inventory management:** YAML inventory with validation (required fields, duplicate hostnames, malformed files)
- **Connectivity testing:** ICMP reachability for every device on Windows and Linux, with packet loss and round-trip time
- **SSH device information collection:** Netmiko SSH sessions that collect hostname, IOS version, uptime and interface status from read-only `show` commands
- **Configuration backup:** `show running-config` saved to timestamped `.cfg` files, never overwriting older backups
- **Configuration validation:** running configurations checked against expected rules, with PASS / FAIL / ERROR results per check and per device
- **CSV reporting:** one CSV report per tool, ready for Excel
- **CLI usage:** one command per task, `--dry-run` mode, meaningful exit codes for scripting
- **Safe credential handling:** credentials come from environment variables only and are never stored, printed or written to reports

## Technologies

- **Python 3** (developed with Python 3.12)
- **Netmiko**: SSH automation for Cisco IOS
- **PyYAML**: inventory and validation rules
- **pytest** and `unittest.mock`: automated testing with simulated devices
- **Cisco IOS-style CLI**: `show version`, `show ip interface brief`, `show interfaces status`, `show running-config`
- **GNS3-compatible lab design**: reference topology and device configurations (optional deployment)

## Architecture

```
config/devices.yaml          config/expected_config.yaml
        |                                |
        v                                |
 Inventory validation                    |
        |                                |
        +--> Connectivity testing        |     ICMP ping
        |                                |
        +--> SSH device information      |     show version, show ip interface brief, ...
        |                                |
        +--> Configuration backup        |     show running-config -> data/backups/*.cfg
        |                                |
        +--> Configuration validation <--+     show running-config vs expected rules
        |
        v
 Terminal tables + CSV reports (data/reports/)
```

The SSH tools share one connection module (`src/ssh_client.py`). It checks credentials before any device is contacted, maps authentication failures, timeouts and other connection errors to clear statuses, and always disconnects.

## Repository Structure

```
enterprise-network-automation/
├── config/
│   ├── devices.yaml            # Lab device inventory (no credentials)
│   └── expected_config.yaml    # Expected configuration rules
├── data/
│   ├── backups/                # Running-config backups (generated, not committed)
│   └── reports/                # CSV reports (generated, not committed)
├── docs/
│   ├── USAGE.md                # Command options, outputs, exit codes, CSV columns
│   ├── demo_output/            # Simulated demonstration output
│   ├── NETWORK_TOPOLOGY.md     # Lab topology, addressing and device roles
│   ├── GNS3_LAB_SETUP.md       # Optional live-lab deployment guide
│   └── sample_configs/         # Reference device configurations (fictional values)
├── scripts/
│   └── generate_demo_output.py # Regenerates docs/demo_output/ from the simulated lab
├── screenshots/
│   └── README.md               # Optional live-lab screenshot checklist
├── src/
│   ├── inventory.py            # Load and validate the inventory
│   ├── connectivity.py         # Ping one host and parse the result
│   ├── network_check.py        # Connectivity check for all devices (CLI)
│   ├── ssh_client.py           # Credentials and Netmiko connections
│   ├── device_info.py          # Device information collection (CLI)
│   ├── config_backup.py        # Running-config backup (CLI)
│   ├── config_validation.py    # Configuration validation (CLI)
│   ├── reports.py              # CSV export and terminal tables
│   └── cli.py                  # Shared command-line helpers
├── tests/
│   ├── conftest.py             # Shared fixtures and test safeguards
│   ├── fixtures/               # Saved Cisco IOS-style command outputs
│   └── test_*.py               # Automated tests
├── .gitignore
├── pytest.ini
├── requirements.txt
└── README.md
```

## Setup

Requires Python 3.10 or newer.

```powershell
git clone https://github.com/udai9832/enterprise-network-automation.git
cd enterprise-network-automation
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Credentials

SSH credentials are read from environment variables only. Use temporary credentials that exist only on lab devices.

```powershell
# PowerShell: current terminal session only
$env:NETWORK_USERNAME="labuser"
$env:NETWORK_PASSWORD="<temporary lab password>"
```

```bash
# Linux / macOS
export NETWORK_USERNAME="labuser"
export NETWORK_PASSWORD="<temporary lab password>"
```

## Commands

Run from the project root with the virtual environment activated.

| Task | Command |
|---|---|
| Run the automated tests | `python -m pytest -q` |
| Connectivity check | `python -m src.network_check` |
| Device information | `python -m src.device_info` |
| Configuration backup | `python -m src.config_backup` |
| Configuration validation | `python -m src.config_validation` |
| Regenerate demo output | `python -m scripts.generate_demo_output` |

The SSH tools support **`--dry-run`**. It validates the inventory, the rules and the credentials, and shows what would be done, without opening any SSH connection or writing any file:

```powershell
python -m src.device_info --dry-run
python -m src.config_backup --dry-run
python -m src.config_validation --dry-run
```

> Without `--dry-run`, the tools connect to the devices listed in `config/devices.yaml`. Use them only against a dedicated lab, such as the optional GNS3 lab described below. They send only read-only `show` commands.

Options, example outputs, exit codes and CSV columns for every tool are documented in **[docs/USAGE.md](docs/USAGE.md)**.

## Device Inventory

[`config/devices.yaml`](config/devices.yaml) lists the lab devices. Credentials are deliberately not part of the inventory.

```yaml
devices:
  - hostname: R1
    device_type: cisco_ios
    host: 192.168.100.11
    role: core-router
```

| Device | Role | Management IP |
|---|---|---|
| R1 | core-router | 192.168.100.11 |
| R2 | edge-router | 192.168.100.12 |
| SW1 | access-switch | 192.168.100.21 |

Required fields are `hostname`, `device_type`, `host` and `role`. All addresses are fictional private lab addresses.

## Configuration Validation Rules

Expected settings are kept in [`config/expected_config.yaml`](config/expected_config.yaml), one entry per inventory device:

```yaml
devices:
  R1:
    hostname: R1                        # expected "hostname" line
    required_config_lines:              # must appear anywhere in the config
      - ip ssh version 2
      - transport input ssh
      - router ospf 1
      - network 10.0.12.0 0.0.0.3 area 0
    prohibited_config_lines:            # must NOT appear
      - ip http server
      - transport input telnet
    interfaces:                         # lines that must appear under each interface
      GigabitEthernet0/0:
        - ip address 192.168.100.11 255.255.255.0
      GigabitEthernet0/1:
        - ip address 10.0.12.1 255.255.255.252
```

| Check type | Passes when |
|---|---|
| `hostname` | The `hostname` line has the expected value |
| `required_config` | The line is present anywhere in the running config |
| `prohibited_config` | The line is not present |
| `interface` | The `interface <name>` section exists |
| `interface_config` | The line is inside that interface's section (an IP on the wrong interface fails) |

How matching works:

- Lines are compared as **whole lines**. Leading and trailing spaces are ignored and repeated spaces count as one. There is no partial or fuzzy matching, and case matters.
- `no ip http server` does not match the prohibited line `ip http server`.
- A device is `ERROR` if its configuration could not be collected, `FAIL` if any check failed, and otherwise `PASS`.
- Mistakes in the rules file (unknown keys, wrong types, duplicate or contradictory lines) are reported before any device is contacted.

## Automated Testing

```powershell
python -m pytest -q
```

The project includes **389 automated tests**, all passing. They cover:

- inventory parsing and reachability logic
- SSH connection handling and Cisco output parsing
- device information collection, configuration backup and configuration validation
- reporting and CLI behaviour, including `--dry-run`
- credential safety
- cross-file lab consistency
- isolation of the demo generator

Cisco SSH interactions are tested with mocked Netmiko sessions and realistic IOS-style CLI fixtures. No external network or real device is needed to run the automated test suite.

Safeguards built into the suite:

- A shared guard fails any test that reaches Netmiko's real `ConnectHandler`.
- A second guard fails any test that creates or changes files in the real `data/backups/` or `data/reports/` folders.
- Fake connections are built from Netmiko's real Cisco IOS class. Tests confirm that only `show` commands are sent and that no configuration-mode or save methods are ever used.
- Consistency tests check that the inventory, the validation rules, the reference lab configurations and the documentation all describe the same lab.

## Demonstration Output

> **Simulated / mock-backed demonstration output.** These files were produced by running the project's real command-line tools against a simulated Cisco lab. Ping replies and SSH sessions were replaced with fixture-backed fakes, so no real device, SSH session or GNS3 lab was used.

| Demo | Shows |
|---|---|
| [Connectivity check](docs/demo_output/connectivity_demo.txt) | Reachability table, summary and CSV report |
| [Device information](docs/demo_output/device_info_demo.txt) | SSH collection results from IOSv-style routers and an IOSvL2-style switch |
| [Configuration backup](docs/demo_output/config_backup_demo.txt) | Backup summary, report and timestamped file names |
| [Configuration validation](docs/demo_output/config_validation_demo.txt) | A fully compliant run, and a drift scenario detected as FAIL |
| [Test suite](docs/demo_output/pytest_demo.txt) | Full `pytest` run with the number of tests per file |

The files are regenerated with `python -m scripts.generate_demo_output`. A test checks that the committed files match what the code produces.

## Optional GNS3 Deployment

A complete, reproducible lab design is included for anyone with legally obtained Cisco IOSv / IOSvL2 images who wants to run the tools against virtual devices in GNS3:

- [docs/NETWORK_TOPOLOGY.md](docs/NETWORK_TOPOLOGY.md): topology diagram, addressing and device roles
- [docs/GNS3_LAB_SETUP.md](docs/GNS3_LAB_SETUP.md): build the lab, connect the automation host safely, configure the devices and run the tools
- [docs/sample_configs/](docs/sample_configs/): reference configurations for R1, R2 and SW1, with fictional values and placeholder secrets

GNS3 deployment is optional. The project does not need GNS3 to be installed: the automated tests and the demonstration output run entirely on simulated devices. Live GNS3 execution was not performed as part of this repository. An optional checklist for capturing screenshots from a live lab is in [screenshots/README.md](screenshots/README.md).

## Security Notes

- **Credentials:** read from `NETWORK_USERNAME` / `NETWORK_PASSWORD` at run time. Never stored in the repository, printed, logged or written to reports.
- **Read-only:** only `show` commands are sent. The tools never enter configuration mode, change, save or reload a device. Netmiko sets `terminal length 0` and `terminal width` for its own SSH session; this does not change the device configuration.
- **Generated files stay local:** backups (`data/backups/`), reports (`data/reports/`), `.env` files, local credential files and logs are excluded from Git.
- **Backups are sensitive:** real running configurations can contain password hashes, secrets and SNMP communities. Do not publish them.
- **Lab only:** the tools connect only to devices listed in the inventory. There is no network discovery or scanning. All addresses in the repository are fictional private lab addresses, and the sample configurations contain placeholder secrets only.

## Project Status

**Project Status: Complete**

| Phase | Scope | Status |
|---|---|---|
| 1. Project Setup & Foundation | Structure, inventory, inventory validation, ping, tests | Complete |
| 2. Device Connectivity Testing | Inventory-wide reachability, CLI, CSV report | Complete |
| 3. Cisco Device Information Collection | Netmiko SSH, credentials, `show` parsing, CSV report | Complete |
| 4. Automated Configuration Backup | `show running-config` backups, safe file names, CSV report | Complete |
| 5. Network Configuration Validation | Expected rules, PASS/FAIL/ERROR checks, detailed and summary reports | Complete |
| 6. Documentation, Lab Reference, Simulated Validation & GitHub Polish | Lab topology and sample configurations, optional GNS3 guide, demonstration output, consistency tests, repository polish | Complete |

What "complete" means here:

- All automation features and the automated test suite are complete.
- Cisco device behaviour is validated through mocked Netmiko sessions and realistic IOS-style fixtures. This simulated testing is complete.
- Live GNS3 execution was not performed. The GNS3 deployment documentation is an optional way to reproduce the lab on virtual devices; the repository does not need it to work.
