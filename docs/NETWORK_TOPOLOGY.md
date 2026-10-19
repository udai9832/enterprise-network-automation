# Network Topology

Reference design of the Cisco lab the automation is built for. The automated tests and the [demonstration output](demo_output/) simulate these three devices with the same hostnames, management IPs, R1–R2 link and validation rules. The lab can optionally be deployed in GNS3 with [GNS3_LAB_SETUP.md](GNS3_LAB_SETUP.md).

> The repository itself is tested with mocked Netmiko sessions and saved Cisco IOS-style output. Deploying this lab is optional and was not performed as part of the project. All addresses are fictional private lab addresses.

## Diagram

```
                         10.0.12.0/30 - OSPF area 0
      +------+ Gi0/1 .1 -------------------------- .2 Gi0/1 +------+
      |  R1  |                                              |  R2  |
      +------+                                              +------+
      Gi0/0 192.168.100.11                       Gi0/0 192.168.100.12
         |                                                     |
         | 802.1Q trunk                                        | access
         | (VLAN 1 untagged = management)                      | VLAN 1
         |                                                     |
      Gi0/0                                                 Gi0/1
      +-------------------------------------------------------------+
      |                            SW1                              |
      |                 Vlan1 192.168.100.21 (management)           |
      +-------------------------------------------------------------+
      Gi0/3 (access VLAN 1)                 Gi0/2 (access VLAN 10, spare)
         |
   [GNS3 Cloud node]
         |
   Lab host adapter 192.168.100.100/24   (runs the Python automation)
```

## Devices

| Device | Role | Lab image type | Management interface | Management IP |
|---|---|---|---|---|
| R1 | core-router | Cisco IOSv-compatible router image | GigabitEthernet0/0 | 192.168.100.11/24 |
| R2 | edge-router | Cisco IOSv-compatible router image | GigabitEthernet0/0 | 192.168.100.12/24 |
| SW1 | access-switch | Cisco IOSvL2-compatible switch image | Vlan1 | 192.168.100.21/24 |

These hostnames and management IPs are the ones in [`config/devices.yaml`](../config/devices.yaml). The lab host adapter (`192.168.100.100`) is the machine running the automation. It is not a managed device, so it is not in the inventory.

## Links

| From | To | Type | Purpose |
|---|---|---|---|
| R1 Gi0/0 | SW1 Gi0/0 | 802.1Q trunk on SW1, VLAN 1 native (untagged) | R1 management |
| R2 Gi0/0 | SW1 Gi0/1 | Access port, VLAN 1 | R2 management |
| R1 Gi0/1 | R2 Gi0/1 | Routed point-to-point, 10.0.12.0/30 | OSPF area 0 between the routers |
| SW1 Gi0/3 | GNS3 Cloud node | Access port, VLAN 1 | Connects the automation host to the management network |
| SW1 Gi0/2 | (unused) | Access port, VLAN 10 `USERS` | Spare user port |

## Address Plan

| Network | Use |
|---|---|
| 192.168.100.0/24 | Management network (VLAN 1 on SW1) |
| 10.0.12.0/30 | R1 to R2 link (R1 = .1, R2 = .2) |
| 1.1.1.1/32 | R1 Loopback0 (OSPF router ID) |
| 2.2.2.2/32 | R2 Loopback0 (OSPF router ID) |

SW1 is a Layer 2 switch (`no ip routing`) and uses R1 (`192.168.100.11`) as its default gateway.

## Device Purpose

- **R1 (core-router):** central router. It runs OSPF with R2 and is the default gateway for SW1. Its management interface sits on the SW1 trunk's native VLAN, so a VLAN subinterface could be added later (router-on-a-stick) without changing management.
- **R2 (edge-router):** simulates the edge/WAN router. It forms an OSPF adjacency with R1 over 10.0.12.0/30.
- **SW1 (access-switch):** connects management for all devices and the automation host. It defines VLAN 10 for user access ports.

## What the Automation Exercises

| Tool | Commands / features used in the lab |
|---|---|
| `src.network_check` | ICMP ping to each management IP |
| `src.device_info` | SSH (Netmiko, `cisco_ios`); `show version`, `show ip interface brief`, `show interfaces status` (SW1 only; the routers reject it, and this is recorded as unsupported) |
| `src.config_backup` | SSH; `show running-config`, saved to timestamped `.cfg` files |
| `src.config_validation` | SSH; `show running-config`, checked against [`config/expected_config.yaml`](../config/expected_config.yaml): hostname, SSH v2, VTY `transport input ssh`, OSPF, VLAN 10, default gateway, interface IPs, trunk mode, and no `ip http server` or Telnet on the VTY lines |

All device commands are read-only `show` commands.

The full device configurations for this design are in [`sample_configs/`](sample_configs/). Build steps are in [`GNS3_LAB_SETUP.md`](GNS3_LAB_SETUP.md).
