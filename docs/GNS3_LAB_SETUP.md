# GNS3 Lab Setup (Optional)

Optional live-lab deployment guide. It describes how to reproduce the project's reference lab in GNS3 and run the tools against virtual Cisco devices. The design is described in [NETWORK_TOPOLOGY.md](NETWORK_TOPOLOGY.md).

> **Optional.** The repository is tested with mocked Netmiko sessions and realistic Cisco IOS-style fixtures, and does not need GNS3. This guide is a reproduction path for anyone who wants to run the tools on virtual devices. A live deployment was not performed as part of the project. The sample configurations contain fictional lab values and placeholder secrets only.

All steps in this guide are manual. The project's scripts never change GNS3, network adapters or device configurations.

## 1. Before You Start

- **Dedicated lab only.** Use these steps for an isolated GNS3 lab. Do not point the tools at real routers, switches or home/office networks.
- **Check the address range.** The lab uses `192.168.100.0/24`. Before you start, make sure your own home or office network does not use this range (check your router's LAN settings). If it does, choose a different private range and update `config/devices.yaml`, `config/expected_config.yaml` and `docs/sample_configs/` together.
- **Never bridge the lab into your real LAN.** The GNS3 Cloud node must only use a host-only or loopback virtual adapter, never your Wi-Fi or Ethernet adapter.
- **Temporary credentials only.** Choose a temporary username and password for the lab. Do not reuse a real password.

## 2. Requirements

- GNS3 (GUI, plus the GNS3 VM if your images need it)
- A Cisco IOSv-compatible router image and a Cisco IOSvL2-compatible switch image that you are licensed to use. Images are not included in this repository.
- This project installed in its virtual environment (see the main [README](../README.md#setup))

## 3. Build the Topology

1. Create a new GNS3 project, for example `enterprise-network-automation-lab`.
2. Add two IOSv routers and name them **R1** and **R2**.
3. Add one IOSvL2 switch and name it **SW1**.
4. Add a **Cloud** node (configured in step 4).
5. Connect the links:

| Link | Endpoint A | Endpoint B |
|---|---|---|
| R1 management | R1 GigabitEthernet0/0 | SW1 GigabitEthernet0/0 |
| R2 management | R2 GigabitEthernet0/0 | SW1 GigabitEthernet0/1 |
| Router link | R1 GigabitEthernet0/1 | R2 GigabitEthernet0/1 |
| Automation host | SW1 GigabitEthernet0/3 | Cloud node (lab adapter) |

6. Start all nodes.

## 4. Connect the Automation Host to the Lab (manual)

The Python tools run on your computer and need to reach `192.168.100.0/24`. The safe way is a **virtual adapter that is not connected to any physical network**, attached to the GNS3 Cloud node.

**Option A: devices run on the local GNS3 server**

1. Add the *Microsoft KM-TEST Loopback Adapter* in Windows (Device Manager > Action > Add legacy hardware > Network adapters > Microsoft > KM-TEST Loopback Adapter).
2. In its IPv4 settings, set a static address **192.168.100.100**, mask **255.255.255.0**, **no default gateway**, **no DNS server**.
3. In GNS3, configure the Cloud node to use **only** this loopback adapter and connect it to SW1 Gi0/3.

**Option B: devices run in the GNS3 VM**

1. In the Cloud node (running on the GNS3 VM), select the VM's **host-only** interface.
2. On Windows, give the matching host-only adapter (VMware VMnet / VirtualBox Host-Only) an additional static address **192.168.100.100/24** with no gateway.

The exact adapter names depend on your GNS3 and hypervisor setup. Whichever option you use, the adapter attached to the Cloud node must be virtual and host-only.

## 5. Configure the Devices

The complete configurations are in [`sample_configs/`](sample_configs/):

| Device | File |
|---|---|
| R1 | [`R1_sample.cfg`](sample_configs/R1_sample.cfg) |
| R2 | [`R2_sample.cfg`](sample_configs/R2_sample.cfg) |
| SW1 | [`SW1_sample.cfg`](sample_configs/SW1_sample.cfg) |

For each device:

1. Copy the sample file to a temporary text editor (do not edit the file in the repository).
2. Replace `REPLACE_WITH_TEMP_LAB_PASSWORD` and `REPLACE_WITH_TEMP_ENABLE_SECRET` with your own temporary lab values. Use the same username and password on all three devices.
3. Open the device console in GNS3 and enter:
   ```
   enable
   configure terminal
   ```
4. Paste the configuration in small sections. Console pasting can drop characters if large blocks are pasted at once.
5. If you want the lab configuration to survive a reload, save it on the lab device with `copy running-config startup-config`. This is a manual lab step; the automation never saves or changes device configurations.

What the configuration sets up:

| Item | R1 | R2 | SW1 |
|---|---|---|---|
| Hostname | R1 | R2 | SW1 |
| Management IP | Gi0/0 192.168.100.11/24 | Gi0/0 192.168.100.12/24 | Vlan1 192.168.100.21/24 |
| SSH | Domain name, RSA 2048, SSH v2 | Same | Same |
| Lab user | `labuser`, privilege 15 | Same | Same |
| VTY lines | `login local`, `transport input ssh` | Same | Same |
| HTTP server | Disabled | Disabled | Disabled |
| Routing | OSPF 1: Lo0 1.1.1.1, 10.0.12.0/30 | OSPF 1: Lo0 2.2.2.2, 10.0.12.0/30 | `no ip routing`, default gateway 192.168.100.11 |
| Switching | n/a | n/a | VTP transparent, VLAN 10 `USERS`, Gi0/0 trunk |

Notes:
- **Privilege 15** is required so that `show running-config` works for backups and validation.
- **VTP transparent mode** keeps VLAN definitions in the running-config. Without it, `vlan 10` is stored only in `vlan.dat` and the validation rule cannot see it.
- The username in the sample files is `labuser`. If you choose a different username, use it consistently on all devices and in `NETWORK_USERNAME`.

## 6. Verify on the Devices

Run these read-only commands on the device consoles:

| Device | Command | Expected |
|---|---|---|
| All | `show ip interface brief` | Management interface `up/up` with the IP above |
| All | `show ip ssh` | `SSH Enabled - version 2.0` |
| R1, R2 | `show ip ospf neighbor` | The other router in `FULL` state |
| R1 | `ping 10.0.12.2` | Success |
| SW1 | `show interfaces trunk` | Gi0/0 trunking |
| SW1 | `show vlan brief` | VLAN 10 `USERS` present |
| SW1 | `ping 192.168.100.11` and `ping 192.168.100.12` | Success |

## 7. Verify From the Automation Host

In PowerShell, test only the three lab management addresses:

```powershell
ping 192.168.100.11
ping 192.168.100.12
ping 192.168.100.21
```

All three should reply. If not, see [Troubleshooting](#10-troubleshooting).

## 8. Set the Lab Credentials

Set the temporary lab credentials for the **current PowerShell session only**. Do not save them in any file.

```powershell
$env:NETWORK_USERNAME="labuser"
$env:NETWORK_PASSWORD="<your temporary lab password>"
```

When you are finished, remove them:

```powershell
Remove-Item Env:NETWORK_USERNAME, Env:NETWORK_PASSWORD
```

## 9. Run the Automation

From the project root, with the virtual environment activated.

**Step 1: checks without SSH**

```powershell
python -m pytest -q
python -m src.device_info --dry-run
python -m src.config_backup --dry-run
python -m src.config_validation --dry-run
```

Each dry run should report `Inventory OK` and `Credentials OK`. The validation dry run should show 11 checks per device.

**Step 2: run the tools against the lab**

| Order | Command | Expected result |
|---|---|---|
| 1 | `python -m src.network_check` | R1, R2 and SW1 `UP`; exit code 0 |
| 2 | `python -m src.device_info` | `success` for all three devices: hostname, IOS version, uptime and interface counts. R1 and R2 list `show interfaces status` as unsupported (expected on routers) |
| 3 | `python -m src.config_backup` | `SUCCESS` for all three devices; one `.cfg` file per device in `data/backups/` |
| 4 | `python -m src.config_validation` | `PASS` for all three devices (11/11) |

The reports are written to `data/reports/`. Reports and backups are excluded from Git.

## 10. Troubleshooting

| Symptom | Likely cause | What to check (lab only) |
|---|---|---|
| Ping from the host fails | Host adapter not connected to the lab | Cloud node uses the lab adapter; adapter has 192.168.100.100/24; SW1 Gi0/3 is connected and in VLAN 1 |
| Ping to SW1 works, routers fail | Router interface down | `show ip interface brief` on R1/R2; `no shutdown` on Gi0/0; SW1 Gi0/0 trunk / Gi0/1 access VLAN 1 |
| `connection_failed` / `timeout` | SSH not running | `show ip ssh`; RSA key generated; `ip ssh version 2`; VTY `transport input ssh` |
| `auth_failed` | Credential mismatch | `NETWORK_USERNAME` / `NETWORK_PASSWORD` match the lab user; `login local` on the VTY lines |
| Backup or validation `% Invalid input` | Privilege too low | Lab user has `privilege 15` |
| Validation `FAIL` on `vlan 10` | VTP server mode | `vtp mode transparent` on SW1, then re-create VLAN 10 |
| Validation `FAIL` on other lines | Lab config differs from the sample | Compare the device with `docs/sample_configs/`; fix the lab device, not the rules |
| `show interfaces status` unsupported on routers | Expected | Recorded in `unsupported_commands`; not a failure |

## 11. Evidence and Clean-Up

- If you want screenshots of your lab run, follow the checklist and privacy rules in [`screenshots/README.md`](../screenshots/README.md).
- Backups in `data/backups/` contain the real lab configuration, including password hashes. Do not publish them. They are ignored by Git; delete them when no longer needed.
- Before committing, run `git status` and confirm that no `.cfg` backups, CSV reports or credential files are listed.
- Remove the credential environment variables (step 8).
