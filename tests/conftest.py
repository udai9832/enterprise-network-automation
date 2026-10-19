"""Shared pytest fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name):
    """Return the text of a saved Cisco command output from tests/fixtures/."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def block_real_ssh(monkeypatch):
    """Safety net: no test may ever open a real SSH session.

    Netmiko's ConnectHandler is replaced for every test. A test that forgets to
    mock the SSH layer fails immediately. pytest.fail is used because it cannot
    be swallowed by the `except Exception` blocks in the code under test.
    """
    def _blocked(**params):
        pytest.fail(f"Real SSH connection attempted to {params.get('host')} - mock the SSH layer")

    monkeypatch.setattr("src.ssh_client.ConnectHandler", _blocked)


@pytest.fixture
def lab_credentials(monkeypatch):
    """Set fake lab credentials in the environment for one test."""
    monkeypatch.setenv("NETWORK_USERNAME", "labuser")
    monkeypatch.setenv("NETWORK_PASSWORD", "Test-Pass-123")
    return {"username": "labuser", "password": "Test-Pass-123"}


@pytest.fixture
def no_credentials(monkeypatch):
    """Make sure no credentials are set, even if the developer's shell has them."""
    monkeypatch.delenv("NETWORK_USERNAME", raising=False)
    monkeypatch.delenv("NETWORK_PASSWORD", raising=False)
