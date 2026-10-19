"""Tests for src/ssh_client.py.

Netmiko's ConnectHandler is always mocked: no SSH connection is ever opened.
"""

from unittest import mock

import pytest
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException

from src.ssh_client import (
    CredentialsError,
    SSHAuthenticationError,
    SSHConnectionError,
    SSHTimeoutError,
    build_connection_params,
    connect,
    disconnect,
    get_credentials,
)

R1 = {"hostname": "R1", "device_type": "cisco_ios", "host": "192.168.100.11", "role": "core-router"}
CREDENTIALS = {"username": "labuser", "password": "Test-Pass-123"}


# --- credentials ------------------------------------------------------------

def test_get_credentials(lab_credentials):
    assert get_credentials() == {"username": "labuser", "password": "Test-Pass-123"}


def test_get_credentials_strips_username(monkeypatch, lab_credentials):
    monkeypatch.setenv("NETWORK_USERNAME", "  labuser  ")

    assert get_credentials()["username"] == "labuser"


def test_missing_username(monkeypatch, no_credentials):
    monkeypatch.setenv("NETWORK_PASSWORD", "Test-Pass-123")

    with pytest.raises(CredentialsError, match=r"Missing environment variable\(s\): NETWORK_USERNAME\.") as exc_info:
        get_credentials()
    assert "Test-Pass-123" not in str(exc_info.value)


def test_missing_password(monkeypatch, no_credentials):
    monkeypatch.setenv("NETWORK_USERNAME", "labuser")

    with pytest.raises(CredentialsError, match=r"Missing environment variable\(s\): NETWORK_PASSWORD\."):
        get_credentials()


def test_missing_both_credentials(no_credentials):
    with pytest.raises(CredentialsError, match="NETWORK_USERNAME, NETWORK_PASSWORD"):
        get_credentials()


@pytest.mark.parametrize("username", ["", "   "])
def test_blank_username_is_missing(monkeypatch, lab_credentials, username):
    monkeypatch.setenv("NETWORK_USERNAME", username)

    with pytest.raises(CredentialsError, match="NETWORK_USERNAME"):
        get_credentials()


def test_empty_password_is_missing(monkeypatch, lab_credentials):
    monkeypatch.setenv("NETWORK_PASSWORD", "")

    with pytest.raises(CredentialsError, match="NETWORK_PASSWORD"):
        get_credentials()


# --- connection parameters --------------------------------------------------

def test_build_connection_params():
    params = build_connection_params(R1, CREDENTIALS, timeout=15)

    assert params == {
        "device_type": "cisco_ios",
        "host": "192.168.100.11",
        "port": 22,
        "username": "labuser",
        "password": "Test-Pass-123",
        "conn_timeout": 15,
        "auth_timeout": 15,
    }


def test_build_connection_params_custom_port():
    params = build_connection_params({**R1, "port": 2222}, CREDENTIALS)

    assert params["port"] == 2222


def test_build_connection_params_rejects_unsupported_device_type():
    with pytest.raises(ValueError, match="Unsupported device_type 'juniper_junos'"):
        build_connection_params({**R1, "device_type": "juniper_junos"}, CREDENTIALS)


# --- connect ----------------------------------------------------------------

@mock.patch("src.ssh_client.ConnectHandler")
def test_connect_success(mock_handler):
    connection = connect(R1, CREDENTIALS, timeout=5)

    assert connection is mock_handler.return_value
    mock_handler.assert_called_once_with(**build_connection_params(R1, CREDENTIALS, timeout=5))


@mock.patch("src.ssh_client.ConnectHandler", side_effect=NetmikoAuthenticationException("Authentication to device failed."))
def test_connect_authentication_failure(mock_handler):
    with pytest.raises(SSHAuthenticationError, match="authentication failed for 192.168.100.11") as exc_info:
        connect(R1, CREDENTIALS)

    assert exc_info.value.status == "auth_failed"
    assert "Test-Pass-123" not in str(exc_info.value)


@mock.patch("src.ssh_client.ConnectHandler", side_effect=NetmikoTimeoutException("TCP connection to device failed."))
def test_connect_timeout(mock_handler):
    with pytest.raises(SSHTimeoutError, match="timed out") as exc_info:
        connect(R1, CREDENTIALS)

    assert exc_info.value.status == "timeout"


@mock.patch("src.ssh_client.ConnectHandler", side_effect=ConnectionRefusedError("[WinError 10061] connection refused"))
def test_connect_general_failure(mock_handler):
    with pytest.raises(SSHConnectionError, match="failed: .*connection refused") as exc_info:
        connect(R1, CREDENTIALS)

    assert exc_info.value.status == "connection_failed"


def test_auth_and_timeout_errors_are_connection_errors():
    assert issubclass(SSHAuthenticationError, SSHConnectionError)
    assert issubclass(SSHTimeoutError, SSHConnectionError)


def test_real_ssh_is_blocked_in_tests():
    # The autouse block_real_ssh fixture in conftest.py must stop an unmocked connection.
    with pytest.raises(pytest.fail.Exception, match="Real SSH connection attempted"):
        connect(R1, CREDENTIALS)


# --- disconnect -------------------------------------------------------------

def test_disconnect():
    connection = mock.Mock()

    disconnect(connection)

    connection.disconnect.assert_called_once()


def test_disconnect_none_is_safe():
    disconnect(None)


def test_disconnect_ignores_errors():
    connection = mock.Mock()
    connection.disconnect.side_effect = OSError("socket already closed")

    disconnect(connection)  # must not raise
