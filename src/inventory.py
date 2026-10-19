"""Load and validate the network device inventory (config/devices.yaml)."""

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INVENTORY_PATH = PROJECT_ROOT / "config" / "devices.yaml"

# Every device in the inventory must define these fields.
REQUIRED_FIELDS = ("hostname", "device_type", "host", "role")


class InventoryError(Exception):
    """Raised when the inventory file is missing or contains invalid data."""


def load_inventory(path=DEFAULT_INVENTORY_PATH):
    """Load the YAML inventory file and return a list of validated devices.

    Each device is returned as a dictionary, for example:
        {"hostname": "R1", "device_type": "cisco_ios",
         "host": "192.168.100.11", "role": "core-router"}
    """
    path = Path(path)
    if not path.is_file():
        raise InventoryError(f"Inventory file not found: {path}")

    try:
        with path.open(encoding="utf-8") as inventory_file:
            data = yaml.safe_load(inventory_file)
    except yaml.YAMLError as exc:
        raise InventoryError(f"Inventory file is not valid YAML: {path}\n{exc}") from exc

    if not isinstance(data, dict) or "devices" not in data:
        raise InventoryError(f"Inventory must contain a top-level 'devices' key: {path}")

    devices = data["devices"]
    if not isinstance(devices, list) or not devices:
        raise InventoryError("'devices' must be a non-empty list of devices")

    for position, device in enumerate(devices, start=1):
        validate_device(device, position)

    check_duplicate_hostnames(devices)
    return devices


def validate_device(device, position):
    """Check that a single device entry has all required fields filled in.

    `position` is the 1-based position in the inventory, used in error messages.
    """
    if not isinstance(device, dict):
        raise InventoryError(f"Device #{position} must be a mapping of fields, got: {device!r}")

    missing = [field for field in REQUIRED_FIELDS if not str(device.get(field) or "").strip()]
    if missing:
        name = device.get("hostname") or f"#{position}"
        raise InventoryError(f"Device {name} is missing required field(s): {', '.join(missing)}")


def check_duplicate_hostnames(devices):
    """Make sure no hostname appears more than once in the inventory."""
    seen = set()
    for device in devices:
        hostname = device["hostname"]
        if hostname in seen:
            raise InventoryError(f"Duplicate hostname in inventory: {hostname}")
        seen.add(hostname)


def get_device(devices, hostname):
    """Return the device with the given hostname, or raise InventoryError."""
    for device in devices:
        if device["hostname"] == hostname:
            return device
    raise InventoryError(f"Device not found in inventory: {hostname}")
