"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from src.inventory import PROJECT_ROOT

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Real output folders. Tests must write to pytest's tmp_path instead.
PROJECT_OUTPUT_DIRS = (PROJECT_ROOT / "data" / "backups", PROJECT_ROOT / "data" / "reports")


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


def _snapshot_output_dirs():
    """Return (path, modified time) for every file in the real output folders."""
    return {
        (path, path.stat().st_mtime_ns)
        for folder in PROJECT_OUTPUT_DIRS
        if folder.is_dir()
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def block_writes_to_project_data():
    """Safety net: no test may create or change files in data/backups/ or data/reports/.

    Real backups and reports belong to real runs; tests must use tmp_path.
    """
    before = _snapshot_output_dirs()
    yield
    changed = _snapshot_output_dirs() - before
    if changed:
        pytest.fail(f"Test wrote to project data folders (use tmp_path): {sorted(str(p) for p, _ in changed)}")


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
