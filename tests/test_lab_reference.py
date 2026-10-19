"""Consistency tests for the lab reference: inventory, validation rules, sample configs and docs.

These tests read project files only. They make sure the documented GNS3 lab,
config/devices.yaml and config/expected_config.yaml describe the same lab.
"""

import ipaddress
import re

import pytest

from src.config_validation import PASS, load_expected_config, validate_config_text
from src.inventory import DEFAULT_INVENTORY_PATH, PROJECT_ROOT, load_inventory

DOCS_DIR = PROJECT_ROOT / "docs"
SAMPLE_DIR = DOCS_DIR / "sample_configs"

DEVICES = load_inventory(DEFAULT_INVENTORY_PATH)
EXPECTED = load_expected_config()
HOSTNAMES = [device["hostname"] for device in DEVICES]

# The only networks that may appear in the lab documentation.
LAB_NETWORKS = [ipaddress.ip_network(n) for n in ("192.168.100.0/24", "10.0.12.0/30", "1.1.1.1/32", "2.2.2.2/32")]
IPV4_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")


def sample_config(hostname):
    return (SAMPLE_DIR / f"{hostname}_sample.cfg").read_text(encoding="utf-8")


def lab_doc_files():
    """Documentation, sample configs, simulated device output and demo output."""
    return (
        sorted(DOCS_DIR.rglob("*.md"))
        + sorted(SAMPLE_DIR.glob("*.cfg"))
        + sorted((PROJECT_ROOT / "tests" / "fixtures").glob("*.txt"))
        + sorted((DOCS_DIR / "demo_output").glob("*.txt"))
    )


# --- inventory and rules agree ----------------------------------------------

def test_every_inventory_device_has_rules_and_vice_versa():
    assert sorted(EXPECTED) == sorted(HOSTNAMES)


def test_rule_hostnames_match_inventory():
    assert all(EXPECTED[name]["hostname"] == name for name in HOSTNAMES)


def test_default_gateway_rule_points_to_an_inventory_device():
    hosts = {device["host"] for device in DEVICES}
    gateways = [
        line.split()[-1]
        for rules in EXPECTED.values()
        for line in rules.get("required_config_lines") or []
        if line.startswith("ip default-gateway ")
    ]
    assert gateways
    assert all(gateway in hosts for gateway in gateways)


def test_inventory_management_ips_are_in_lab_management_network():
    management = ipaddress.ip_network("192.168.100.0/24")
    assert all(ipaddress.ip_address(device["host"]) in management for device in DEVICES)


# --- sample configs match the rules and the inventory -----------------------

@pytest.mark.parametrize("hostname", HOSTNAMES)
def test_sample_config_passes_validation_rules(hostname):
    checks = validate_config_text(EXPECTED[hostname], sample_config(hostname))

    failed = [c["check_name"] for c in checks if c["status"] != PASS]
    assert failed == []
    assert len(checks) == 11


@pytest.mark.parametrize("device", DEVICES, ids=HOSTNAMES)
def test_sample_config_uses_inventory_hostname_and_management_ip(device):
    text = sample_config(device["hostname"])

    assert f"\nhostname {device['hostname']}\n" in text
    assert f" ip address {device['host']} 255.255.255.0\n" in text


@pytest.mark.parametrize("hostname", HOSTNAMES)
def test_sample_config_contains_only_placeholder_secrets(hostname):
    secret_lines = [
        line for line in sample_config(hostname).splitlines()
        if line.startswith(("username ", "enable secret", "enable password"))
    ]

    assert secret_lines
    assert all("REPLACE_WITH_" in line for line in secret_lines)


@pytest.mark.parametrize("hostname", HOSTNAMES)
def test_sample_config_is_labeled_as_sample(hostname):
    assert "SAMPLE LAB CONFIGURATION" in sample_config(hostname).splitlines()[1]


# --- documentation ----------------------------------------------------------

@pytest.mark.parametrize("doc", ["NETWORK_TOPOLOGY.md", "GNS3_LAB_SETUP.md"])
def test_docs_mention_every_inventory_device_and_ip(doc):
    text = (DOCS_DIR / doc).read_text(encoding="utf-8")

    for device in DEVICES:
        assert device["hostname"] in text
        assert device["host"] in text


@pytest.mark.parametrize("path", lab_doc_files(), ids=lambda p: p.name)
def test_docs_use_only_lab_addresses(path):
    """Only lab networks (plus subnet masks / wildcards) may appear in lab docs and simulated data."""
    for value in IPV4_PATTERN.findall(path.read_text(encoding="utf-8")):
        if value.startswith(("255.", "0.")):
            continue   # subnet mask or OSPF wildcard
        address = ipaddress.ip_address(value)
        assert any(address in network for network in LAB_NETWORKS), f"{value} in {path.name}"


@pytest.mark.parametrize(
    "path",
    [PROJECT_ROOT / "README.md", PROJECT_ROOT / "screenshots" / "README.md", *sorted(DOCS_DIR.glob("*.md"))],
    ids=lambda p: str(p.relative_to(PROJECT_ROOT)),
)
def test_relative_links_point_to_existing_files(path):
    for target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#")):
            continue
        linked = (path.parent / target.split("#")[0]).resolve()
        assert linked.exists(), f"broken link in {path.name}: {target}"
