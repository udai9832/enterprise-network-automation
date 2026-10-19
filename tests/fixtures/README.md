# Test Fixtures

Sample Cisco IOS command outputs used by the unit tests in place of real devices.
They follow the format of Cisco IOSv 15.9, IOSvL2 15.2 and IOS-XE 16.9 output, with lab
hostnames and private lab IP addresses.

| File | Command | Device |
|---|---|---|
| `show_version_iosv.txt` | `show version` | Router R1 (IOSv) |
| `show_version_iosvl2.txt` | `show version` | Switch SW1 (IOSvL2) |
| `show_version_iosxe.txt` | `show version` | IOS-XE router (format variation) |
| `show_ip_interface_brief_router.txt` | `show ip interface brief` | Router R1 |
| `show_ip_interface_brief_switch.txt` | `show ip interface brief` | Switch SW1 |
| `show_interfaces_status_switch.txt` | `show interfaces status` | Switch SW1 |
| `show_running_config_r1.txt` | `show running-config` | Router R1 (IOSv) |
| `invalid_input.txt` | any unsupported command | Router (`% Invalid input detected`) |

The password hashes in `show_running_config_r1.txt` are placeholders (`EXAMPLEONLY...`), not real secrets.
