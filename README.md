# Enterprise Network Automation Using Python

Python automation toolkit for a Cisco lab network simulated in GNS3. The project automates routine network operations tasks: keeping a device inventory, testing reachability, collecting device information, backing up configurations, and validating configurations against expected standards.

> **Status:** Phase 1 (Project Setup & Foundation), Phase 2 (Device Connectivity Testing), Phase 3 (Cisco Device Information Collection) and Phase 4 (Automated Configuration Backup) are complete. Phases 5–6 are planned. Phase 3 and Phase 4 code is verified with mocked SSH sessions; testing against the GNS3 lab is part of Phase 6.

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
| SSH connectivity to Cisco IOS devices (Netmiko) | Done (Phase 3), tested with mocks |
| Device information collection (version, uptime, interfaces) | Done (Phase 3), tested with mocks |
| CSV device information report | Done (Phase 3), tested with mocks |
| Automated running-configuration backups | Done (Phase 4), tested with mocks |
| CSV configuration backup report | Done (Phase 4), tested with mocks |
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

### Phase 3: Cisco Device Information Collection (complete, mock-tested)

- **SSH client** (`src/ssh_client.py`): builds Netmiko `cisco_ios` connection parameters from the inventory and reads credentials from the `NETWORK_USERNAME` / `NETWORK_PASSWORD` environment variables. Authentication failures, connection timeouts and other connection errors are turned into clear, separate error types. Sessions are always disconnected safely.
- **Device information collection** (`src/device_info.py`): runs read-only show commands on each device and parses the output:
  - `show version`: device hostname, IOS version, uptime
  - `show ip interface brief`: interface count, interfaces up/down, one-line interface summary
  - `show interfaces status`: switch port summary (optional; routers reject this command, which is recorded and does not fail the device)
- **Fault tolerance:** each device is processed one at a time. A device that fails authentication, times out or returns unexpected output is recorded with its status and error, and collection continues with the next device. Values that cannot be parsed are left blank, never guessed.
- **Dry-run mode:** validates the inventory and credentials without opening any SSH connection.
- **CSV report:** results are saved to `data/reports/device_info_report.csv`.
- **Mock-based tests:** all SSH sessions in the test suite are simulated with saved Cisco IOSv / IOSvL2 / IOS-XE command outputs (`tests/fixtures/`). A test safeguard makes any test fail if it attempts a real SSH connection.

> Phase 3 has not yet been run against live GNS3 devices. End-to-end lab testing is planned for Phase 6.

### Phase 4: Automated Configuration Backup (complete, mock-tested)

- **Configuration backup** (`src/config_backup.py`): connects to each device with the Phase 3 SSH client, runs `show running-config`, and saves the output to `data/backups/` as a timestamped `.cfg` file, for example `R1_20261019_125500.cfg`.
- **Read-only:** the only command sent is `show running-config`. The tool never enters configuration mode, never sends configuration commands, and never runs `write memory` or `copy running-config startup-config`.
- **Output checks:** empty output, Cisco CLI errors (`% Invalid input`, `% Authorization failed`, ...) and cut-off output (no final `end` line) are reported as failures and are **not** saved as backups.
- **Safe file handling:** existing backups are never overwritten, hostnames are sanitized for filenames, and a half-written file is removed if saving fails.
- **Fault tolerance:** one device failing (authentication, timeout, bad output, write error) does not stop the backups of the other devices.
- **Dry-run mode** and a **CSV summary report** (`data/reports/config_backup_report.csv`).
- **Mock-based tests:** SSH sessions are simulated with a sample IOSv running configuration. Tests check that no configuration-mode or save methods are ever called, and a safeguard fails any test that writes into the real `data/backups/` or `data/reports/` folders.

> No real device backup has been performed yet. Running backups against the GNS3 lab is part of Phase 6.

## Technologies

- Python 3.12
- PyYAML: device inventory
- Netmiko: SSH automation for Cisco IOS devices
- pytest and unittest.mock: unit testing with simulated devices
- GNS3: Cisco IOSv / IOSvL2 lab simulation
- Git / GitHub: version control

## Project Structure

```
enterprise-network-automation/
├── config/
│   └── devices.yaml          # Lab device inventory (no credentials)
├── data/
│   ├── backups/              # Running-config backups (generated, not committed)
│   └── reports/              # Generated reports (not committed)
├── src/
│   ├── __init__.py
│   ├── inventory.py          # Load and validate the device inventory
│   ├── connectivity.py       # ICMP reachability testing for one host
│   ├── network_check.py      # Inventory-wide connectivity check (CLI)
│   ├── ssh_client.py         # SSH credentials and Netmiko connections
│   ├── device_info.py        # Cisco device information collection (CLI)
│   ├── config_backup.py      # Running-config backup (CLI)
│   └── reports.py            # CSV report export and table formatting
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Shared fixtures and test safeguards
│   ├── fixtures/             # Sample Cisco IOS command outputs
│   ├── test_inventory.py
│   ├── test_connectivity.py
│   ├── test_network_check.py
│   ├── test_reports.py
│   ├── test_ssh_client.py
│   ├── test_device_info.py
│   └── test_config_backup.py
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

## Collecting Cisco Device Information

### 1. Set the lab credentials

SSH credentials are read from environment variables. They are **never** stored in `config/devices.yaml` or committed to Git, and they are never printed or written to reports. These credentials are for the dedicated Cisco/GNS3 lab devices only.

```powershell
# PowerShell: applies to the current terminal session only
$env:NETWORK_USERNAME="labuser"
$env:NETWORK_PASSWORD="example-password"
```

```bash
# Linux / macOS
export NETWORK_USERNAME="labuser"
export NETWORK_PASSWORD="example-password"
```

The lab account needs to be able to run `show` commands. No configuration commands are sent to the devices.

### 2. Validate the setup (no SSH)

```powershell
python -m src.device_info --dry-run
```

Checks the inventory and confirms that both credential variables are set, without connecting to any device:

```
Dry run: checking inventory and credentials. No SSH connections will be opened.

Inventory OK: 3 device(s) in C:\enterprise-network-automation\config\devices.yaml

HOSTNAME  HOST            ROLE           DEVICE TYPE
--------  --------------  -------------  -----------
R1        192.168.100.11  core-router    cisco_ios
R2        192.168.100.12  edge-router    cisco_ios
SW1       192.168.100.21  access-switch  cisco_ios

Credentials OK: NETWORK_USERNAME and NETWORK_PASSWORD are set (values not shown).
```

### 3. Run the collection (requires the GNS3 lab)

```powershell
python -m src.device_info
```

> Only run this once the dedicated GNS3 lab is running and its devices are reachable on the `192.168.100.0/24` management network with SSH enabled. It connects only to the devices listed in the inventory. No network discovery or scanning is performed.

Optional arguments:

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--report PATH` | `data/reports/device_info_report.csv` | Where to save the CSV report |
| `--timeout SECONDS` | `10` | SSH connection timeout |
| `--dry-run` | off | Validate inventory and credentials only |

Example output format, produced by the unit tests from simulated device output (R2 rejecting the credentials):

```
HOSTNAME  HOST            SSH STATUS   DEVICE NAME  IOS VERSION            UPTIME                     INTF UP/TOTAL  ERROR
--------  --------------  -----------  -----------  ---------------------  -------------------------  -------------  --------------------------------------------
R1        192.168.100.11  success      R1           15.9(3)M6              2 hours, 14 minutes        3/5
R2        192.168.100.12  auth_failed  -            -                      -                          -              SSH authentication failed for 192.168.100.12
SW1       192.168.100.21  success      SW1          15.2(20170321:233949)  1 day, 3 hours, 5 minutes  3/5

Total devices: 3
Collected:     2
Failed:        1
```

**SSH status values:** `success`, `auth_failed`, `timeout`, `connection_failed`, `failed` (connected, but a required command failed).

**Exit codes:** `0` = all devices collected, `1` = one or more devices failed, `2` = inventory, credential or report error.

### CSV Device Information Report

Each run overwrites `data/reports/device_info_report.csv` with one row per device:

| Column | Description |
|---|---|
| `inventory_hostname` | Hostname from the inventory |
| `host` | Management IP address |
| `role` | Device role |
| `ssh_status` | Result of the SSH session (see status values above) |
| `device_hostname` | Hostname reported by the device (`show version`) |
| `ios_version` | IOS software version (`show version`) |
| `uptime` | Device uptime (`show version`) |
| `interface_count` | Interfaces listed by `show ip interface brief` |
| `interfaces_up` | Interfaces with Status **and** Protocol `up` |
| `interfaces_down` | All other interfaces (down, administratively down, up/down) |
| `ip_interface_summary` | One-line summary: interface, IP address, status/protocol |
| `switchport_summary` | Switch port states from `show interfaces status` (switches only) |
| `unsupported_commands` | Optional commands the device rejected |
| `error` | Reason for a failure (blank on success) |
| `checked_at` | Local timestamp of the collection |

Values that could not be collected or parsed are left blank. Generated reports are excluded from Git by `.gitignore`.

## Backing Up Device Configurations

The backup tool saves each device's running configuration using the read-only command:

```
show running-config
```

It uses the same inventory and the same `NETWORK_USERNAME` / `NETWORK_PASSWORD` environment variables as the device information collection (see [Set the lab credentials](#1-set-the-lab-credentials)).

### Validate the setup (no SSH, no files)

```powershell
python -m src.config_backup --dry-run
```

Checks the inventory and credentials and shows what would be backed up. It opens no SSH connections and writes no files:

```
Dry run: checking inventory and credentials. No SSH connections will be opened and no files will be written.

Inventory OK: 3 device(s) in C:\enterprise-network-automation\config\devices.yaml
Backup directory: C:\enterprise-network-automation\data\backups

HOSTNAME  HOST            ROLE           BACKUP FILE
--------  --------------  -------------  -----------------------
R1        192.168.100.11  core-router    R1_YYYYMMDD_HHMMSS.cfg
R2        192.168.100.12  edge-router    R2_YYYYMMDD_HHMMSS.cfg
SW1       192.168.100.21  access-switch  SW1_YYYYMMDD_HHMMSS.cfg

Credentials OK: NETWORK_USERNAME and NETWORK_PASSWORD are set (values not shown).
```

### Run the backup (requires the GNS3 lab)

```powershell
python -m src.config_backup
```

> Only run this against the dedicated GNS3 lab, once its devices are reachable on the `192.168.100.0/24` management network with SSH enabled. It connects only to the devices listed in the inventory.

Optional arguments:

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--backup-dir PATH` | `data/backups` | Folder for the `.cfg` backup files |
| `--report PATH` | `data/reports/config_backup_report.csv` | Where to save the CSV summary |
| `--timeout SECONDS` | `10` | SSH connection timeout |
| `--dry-run` | off | Validate inventory and credentials only |

Example output format, produced by the unit tests from simulated device output (R2 rejecting the credentials):

```
HOSTNAME  HOST            ROLE           STATUS   BACKUP                   ERROR
--------  --------------  -------------  -------  -----------------------  --------------------------------------------
R1        192.168.100.11  core-router    SUCCESS  R1_20261019_125500.cfg
R2        192.168.100.12  edge-router    FAILED   -                        SSH authentication failed for 192.168.100.12
SW1       192.168.100.21  access-switch  SUCCESS  SW1_20261019_125500.cfg

Total devices: 3
Successful backups: 2
Failed backups: 1
```

**Exit codes:** `0` = all devices backed up, `1` = one or more backups failed, `2` = inventory, credential or report error.

### Backup Files

- **Location:** `data/backups/`
- **Filename:** `<HOSTNAME>_<YYYYMMDD>_<HHMMSS>.cfg`, using the inventory hostname and the local time of the backup (for example `R1_20261019_125500.cfg`). Every run creates new files, so older backups are kept.
- **Collisions:** if a file with the same name already exists (two backups of one device in the same second), `_1`, `_2`, ... is added. An existing backup is never overwritten.
- **Hostnames:** characters that are not safe in filenames (`/ \ : * ? " < > |`, spaces, ...) are replaced with `_`.
- **Contents:** the running configuration exactly as returned by the device. Nothing is added: no credentials, connection details or script information.
- **Not saved:** empty output, Cisco CLI error responses (`% Invalid input detected`, `% Authorization failed`, ...) and incomplete output that does not end with `end`. These are reported as `FAILED` with the reason.

> **Warning:** real configuration backups can contain sensitive data such as local usernames, password hashes, enable secrets, SNMP communities and keys. Treat backup files as confidential and do not publish them. Generated backups in `data/backups/` are excluded from Git by `.gitignore`.

### CSV Backup Report

Each run overwrites `data/reports/config_backup_report.csv` with one row per device:

| Column | Description |
|---|---|
| `hostname` | Hostname from the inventory |
| `host` | Management IP address |
| `role` | Device role |
| `status` | `SUCCESS` or `FAILED` |
| `backup_path` | Full path of the saved `.cfg` file (blank on failure) |
| `bytes_written` | Size of the saved backup in bytes (blank on failure) |
| `error` | Reason for a failure (blank on success) |
| `backed_up_at` | Local timestamp of the backup attempt |

## Using the Modules in Python

```python
from src.network_check import run_connectivity_check, summarize_results

results = run_connectivity_check()
print(summarize_results(results))   # {'total': 3, 'reachable': 2, 'unreachable': 1}
```

The parsers can be used on saved command output without any device:

```python
from src.device_info import parse_show_version

with open("tests/fixtures/show_version_iosv.txt") as f:
    print(parse_show_version(f.read()))
# {'device_hostname': 'R1', 'ios_version': '15.9(3)M6', 'uptime': '2 hours, 14 minutes'}
```

## Running Tests

```bash
python -m pytest -v
```

The tests do not require internet access, Cisco devices, SSH access or a running GNS3 lab:

- Ping calls are replaced with mocks.
- SSH sessions are replaced with mock Netmiko connections that return saved Cisco IOS outputs from `tests/fixtures/`.
- Shared safeguards in `tests/conftest.py` fail any test that tries to open a real SSH connection, or that creates or changes files in the real `data/backups/` or `data/reports/` folders. Tests write to temporary folders instead.

## Roadmap

- [x] **Phase 1: Project Setup & Foundation** (complete). Project structure, device inventory, inventory validation, basic ping reachability, unit tests.
- [x] **Phase 2: Device Connectivity Testing** (complete). Reachability tests across the whole inventory, command-line summary, CSV connectivity report.
- [x] **Phase 3: Cisco Device Information Collection** (complete, mock-tested). SSH with Netmiko, environment-variable credentials, `show version` and interface parsing, CSV device information report.
- [x] **Phase 4: Automated Configuration Backup** (complete, mock-tested). `show running-config` backups to timestamped `.cfg` files, output checks, dry-run mode, CSV backup report.
- [ ] **Phase 5: Network Configuration Validation.** Check device configurations against expected settings and report deviations.
- [ ] **Phase 6: GNS3 Integration, Final Testing & GitHub Polish.** Run workflows end-to-end against the GNS3 lab, finalize tests and documentation.

## Security Note

- This repository contains lab-only, private IP addresses and no credentials.
- Device credentials are read from the `NETWORK_USERNAME` and `NETWORK_PASSWORD` environment variables at run time. They are never printed, logged or written to reports.
- `.env` files, local credential files and log files are excluded by `.gitignore`.
- The tools connect only to devices listed in the inventory. They do not perform network discovery or subnet scanning.
- Only read-only `show` commands are sent to devices. Netmiko also sets `terminal length 0` and `terminal width` for the SSH session; these affect only that session and do not change the device configuration.
- Configuration backups can contain sensitive device data (password hashes, secrets, SNMP communities). Generated backups in `data/backups/` are excluded from Git and should not be published.
