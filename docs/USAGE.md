# Usage Reference

Detailed reference for each command-line tool: options, output, exit codes and CSV report columns. Run all commands from the project root with the virtual environment activated.

The example outputs below come from the simulated devices used in the automated tests, or describe the expected format. They are not output from a live lab. Complete simulated runs of every tool are in [demo_output/](demo_output/).

- [Connectivity check](#connectivity-check)
- [Device information collection](#device-information-collection)
- [Configuration backup](#configuration-backup)
- [Configuration validation](#configuration-validation)

The SSH-based tools (device information, backup, validation) read the lab credentials from the `NETWORK_USERNAME` and `NETWORK_PASSWORD` environment variables and support `--dry-run`, which validates the inputs without opening any SSH connection or writing any file.

---

## Connectivity Check

```powershell
python -m src.network_check
```

Pings every device in the inventory and saves `data/reports/connectivity_report.csv`.

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--report PATH` | `data/reports/connectivity_report.csv` | Where to save the CSV report |
| `--count N` | `2` | Echo requests sent to each device |
| `--timeout SECONDS` | `2` | Time to wait for each reply |

Example output format (R2 powered off):

```
HOSTNAME  HOST            ROLE           STATUS  LOSS  AVG RTT  ERROR
--------  --------------  -------------  ------  ----  -------  ---------------------------------
R1        192.168.100.11  core-router    UP      0%    2.5 ms
R2        192.168.100.12  edge-router    DOWN    100%  -        no replies received (exit code 1)
SW1       192.168.100.21  access-switch  UP      0%    3.0 ms

Total devices: 3
Reachable:     2
Unreachable:   1
```

A "Destination host unreachable" reply on Windows is correctly treated as a failure: replies are counted by their `TTL=` value, not by the ping exit code.

**Exit codes:** `0` = all reachable, `1` = one or more unreachable, `2` = inventory or report error.

| CSV column | Description |
|---|---|
| `hostname`, `host`, `role` | Device details from the inventory |
| `reachable` | `True` if at least one echo reply was received |
| `packets_sent` / `packets_received` | Echo requests sent / replies received |
| `packet_loss_percent` | Packet loss in percent |
| `average_rtt_ms` | Average round-trip time (blank if no replies) |
| `error` | Reason the device was unreachable |
| `checked_at` | Local timestamp |

---

## Device Information Collection

```powershell
python -m src.device_info --dry-run   # inventory + credentials only, no SSH
python -m src.device_info             # requires the GNS3 lab
```

Runs read-only show commands over SSH and saves `data/reports/device_info_report.csv`:

- `show version`: device hostname, IOS version, uptime
- `show ip interface brief`: interface count, interfaces up/down, interface summary
- `show interfaces status`: switch port summary (switches only; routers reject this command, which is recorded and is not a failure)

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--report PATH` | `data/reports/device_info_report.csv` | Where to save the CSV report |
| `--timeout SECONDS` | `10` | SSH connection timeout |
| `--dry-run` | off | Validate inventory and credentials only |

Example output format (simulated devices, R2 rejecting the credentials):

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

**Exit codes:** `0` = all collected, `1` = one or more failed, `2` = inventory, credential or report error.

| CSV column | Description |
|---|---|
| `inventory_hostname`, `host`, `role` | Device details from the inventory |
| `ssh_status` | Result of the SSH session |
| `device_hostname` / `ios_version` / `uptime` | From `show version` |
| `interface_count` | Interfaces listed by `show ip interface brief` |
| `interfaces_up` | Interfaces with Status **and** Protocol `up` |
| `interfaces_down` | All other interfaces (down, administratively down, up/down) |
| `ip_interface_summary` | One line per interface: name, IP, status/protocol |
| `switchport_summary` | Port states from `show interfaces status` (switches) |
| `unsupported_commands` | Optional commands the device rejected |
| `error` | Reason for a failure |
| `checked_at` | Local timestamp |

Values that could not be collected or parsed are left blank, never guessed.

---

## Configuration Backup

```powershell
python -m src.config_backup --dry-run   # inventory + credentials only, no SSH, no files
python -m src.config_backup             # requires the GNS3 lab
```

Sends `show running-config` to each device and saves the output to `data/backups/`. The device is never put into configuration mode, and nothing is saved or changed on it.

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--backup-dir PATH` | `data/backups` | Folder for the `.cfg` files |
| `--report PATH` | `data/reports/config_backup_report.csv` | Where to save the CSV summary |
| `--timeout SECONDS` | `10` | SSH connection timeout |
| `--dry-run` | off | Validate inventory and credentials only |

Example output format (simulated devices, R2 rejecting the credentials):

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

**Backup files:**

- **Filename:** `<HOSTNAME>_<YYYYMMDD>_<HHMMSS>.cfg`, using the inventory hostname, for example `R1_20261019_125500.cfg`.
- **No overwrites:** existing backups are never overwritten. If a name is already taken (same device, same second), `_1`, `_2`, ... is added.
- **Safe names:** characters that are unsafe in filenames are replaced with `_`.
- **Contents:** the running configuration exactly as returned by the device, with nothing added.
- **Rejected output:** empty output, Cisco CLI errors (`% Invalid input`, `% Authorization failed`, ...) and output without a final `end` line are reported as `FAILED` and are not saved.

> Real configuration backups can contain password hashes, secrets and other sensitive data. Do not publish them. `data/backups/` is excluded from Git.

**Exit codes:** `0` = all backed up, `1` = one or more failed, `2` = inventory, credential or report error.

| CSV column | Description |
|---|---|
| `hostname`, `host`, `role` | Device details from the inventory |
| `status` | `SUCCESS` or `FAILED` |
| `backup_path` | Full path of the saved `.cfg` file (blank on failure) |
| `bytes_written` | Size of the backup in bytes (blank on failure) |
| `error` | Reason for a failure |
| `backed_up_at` | Local timestamp |

---

## Configuration Validation

```powershell
python -m src.config_validation --dry-run   # inventory + rules + credentials, no SSH, no reports
python -m src.config_validation             # requires the GNS3 lab
```

Collects `show running-config` from each device and checks it against `config/expected_config.yaml`. The rules are explained in the main [README](../README.md#configuration-validation-rules).

| Option | Default | Description |
|---|---|---|
| `--inventory PATH` | `config/devices.yaml` | Inventory file to use |
| `--expected PATH` | `config/expected_config.yaml` | Expected configuration rules |
| `--report PATH` | `data/reports/config_validation_report.csv` | Detailed CSV (one row per check) |
| `--summary-report PATH` | `data/reports/config_validation_summary.csv` | Summary CSV (one row per device) |
| `--timeout SECONDS` | `10` | SSH connection timeout |
| `--dry-run` | off | Validate inventory, rules and credentials only |

Example output format (simulated devices: R2 not responding, SW1 with the HTTP server enabled):

```
DEVICE  CHECK                                                        STATUS  MESSAGE
------  -----------------------------------------------------------  ------  ------------------------------------------
R1      hostname R1                                                  PASS
R1      ip ssh version 2                                             PASS
R1      transport input ssh                                          PASS
R1      router ospf 1                                                PASS
R1      network 10.0.12.0 0.0.0.3 area 0                             PASS
R1      prohibited: ip http server                                   PASS
R1      prohibited: transport input telnet                           PASS
R1      interface GigabitEthernet0/0                                 PASS
R1      GigabitEthernet0/0: ip address 192.168.100.11 255.255.255.0  PASS
R1      interface GigabitEthernet0/1                                 PASS
R1      GigabitEthernet0/1: ip address 10.0.12.1 255.255.255.252     PASS
R2      show running-config                                          ERROR   SSH connection to 192.168.100.12 timed out
SW1     hostname SW1                                                 PASS
SW1     ip ssh version 2                                             PASS
SW1     transport input ssh                                          PASS
SW1     vlan 10                                                      PASS
SW1     ip default-gateway 192.168.100.11                            PASS
SW1     prohibited: ip http server                                   FAIL    prohibited configuration line found
SW1     prohibited: transport input telnet                           PASS
SW1     interface Vlan1                                              PASS
SW1     Vlan1: ip address 192.168.100.21 255.255.255.0               PASS
SW1     interface GigabitEthernet0/0                                 PASS
SW1     GigabitEthernet0/0: switchport mode trunk                    PASS

Summary:
DEVICE  RESULT  PASSED
------  ------  ------
R1      PASS    11/11
R2      ERROR   0/1
SW1     FAIL    10/11
```

**Exit codes:** `0` = all devices passed, `1` = one or more devices failed or could not be checked, `2` = inventory, expected config, credential or report error.

**Detailed report:** `config_validation_report.csv`, one row per check.

| Column | Description |
|---|---|
| `hostname`, `host`, `role` | Device details from the inventory |
| `check_type` | `hostname`, `required_config`, `prohibited_config`, `interface`, `interface_config`, `collection` or `rules` |
| `check_name` | The line or item checked |
| `expected` | Expected value: hostname, `present` or `absent` |
| `actual` | Found value: hostname, `present`, `absent` or `missing` |
| `status` | `PASS`, `FAIL` or `ERROR` |
| `message` | Short explanation |
| `checked_at` | Local timestamp |

**Summary report:** `config_validation_summary.csv`, one row per device.

| Column | Description |
|---|---|
| `hostname`, `host`, `role` | Device details from the inventory |
| `total_checks` | Number of checks run |
| `passed_checks` / `failed_checks` / `error_checks` | Count of each result |
| `overall_status` | `PASS`, `FAIL` or `ERROR` |
| `checked_at` | Local timestamp |

All reports are overwritten on each run, contain no credentials, and are excluded from Git.
