"""Tests for src/inventory.py."""

import pytest

from src.inventory import (
    DEFAULT_INVENTORY_PATH,
    REQUIRED_FIELDS,
    InventoryError,
    get_device,
    load_inventory,
)

VALID_INVENTORY = """
devices:
  - hostname: R1
    device_type: cisco_ios
    host: 192.168.100.11
    role: core-router
  - hostname: SW1
    device_type: cisco_ios
    host: 192.168.100.21
    role: access-switch
"""


def write_inventory(tmp_path, content):
    path = tmp_path / "devices.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_valid_inventory(tmp_path):
    devices = load_inventory(write_inventory(tmp_path, VALID_INVENTORY))

    assert len(devices) == 2
    assert devices[0]["hostname"] == "R1"
    assert devices[0]["host"] == "192.168.100.11"
    assert devices[1]["role"] == "access-switch"


def test_project_inventory_is_valid():
    devices = load_inventory(DEFAULT_INVENTORY_PATH)

    hostnames = [device["hostname"] for device in devices]
    assert hostnames == ["R1", "R2", "SW1"]
    for device in devices:
        for field in REQUIRED_FIELDS:
            assert device[field]


def test_project_inventory_has_no_credentials():
    devices = load_inventory(DEFAULT_INVENTORY_PATH)

    for device in devices:
        assert "password" not in device
        assert "secret" not in device


def test_missing_inventory_file(tmp_path):
    with pytest.raises(InventoryError, match="not found"):
        load_inventory(tmp_path / "does_not_exist.yaml")


def test_invalid_yaml(tmp_path):
    path = write_inventory(tmp_path, "devices: [hostname: R1\n  bad: : :")

    with pytest.raises(InventoryError, match="not valid YAML"):
        load_inventory(path)


@pytest.mark.parametrize(
    "content",
    [
        "",                       # empty file
        "- hostname: R1\n",       # list instead of mapping
        "routers:\n  - R1\n",     # wrong top-level key
    ],
)
def test_missing_devices_key(tmp_path, content):
    with pytest.raises(InventoryError, match="'devices' key"):
        load_inventory(write_inventory(tmp_path, content))


@pytest.mark.parametrize("content", ["devices: []\n", "devices:\n", "devices: R1\n"])
def test_devices_must_be_non_empty_list(tmp_path, content):
    with pytest.raises(InventoryError, match="non-empty list"):
        load_inventory(write_inventory(tmp_path, content))


@pytest.mark.parametrize("missing_field", REQUIRED_FIELDS)
def test_missing_required_field(tmp_path, missing_field):
    device = {
        "hostname": "R1",
        "device_type": "cisco_ios",
        "host": "192.168.100.11",
        "role": "core-router",
    }
    del device[missing_field]
    lines = "\n".join(f"    {key}: {value}" for key, value in device.items())
    content = "devices:\n  -\n" + lines + "\n"

    with pytest.raises(InventoryError, match=missing_field):
        load_inventory(write_inventory(tmp_path, content))


def test_empty_required_field(tmp_path):
    content = """
devices:
  - hostname: R1
    device_type: cisco_ios
    host: ""
    role: core-router
"""
    with pytest.raises(InventoryError, match="Device R1 is missing required field\\(s\\): host"):
        load_inventory(write_inventory(tmp_path, content))


def test_device_entry_must_be_mapping(tmp_path):
    content = "devices:\n  - R1\n"

    with pytest.raises(InventoryError, match="Device #1 must be a mapping"):
        load_inventory(write_inventory(tmp_path, content))


def test_duplicate_hostnames(tmp_path):
    content = VALID_INVENTORY.replace("hostname: SW1", "hostname: R1")

    with pytest.raises(InventoryError, match="Duplicate hostname"):
        load_inventory(write_inventory(tmp_path, content))


def test_get_device(tmp_path):
    devices = load_inventory(write_inventory(tmp_path, VALID_INVENTORY))

    assert get_device(devices, "SW1")["host"] == "192.168.100.21"
    with pytest.raises(InventoryError, match="not found"):
        get_device(devices, "R9")
