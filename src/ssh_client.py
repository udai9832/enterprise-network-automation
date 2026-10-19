"""SSH connections to Cisco IOS lab devices using Netmiko.

Credentials are read from environment variables, never from the inventory:
    NETWORK_USERNAME
    NETWORK_PASSWORD
"""

import os

from netmiko import ConnectHandler, NetmikoAuthenticationException, NetmikoTimeoutException

USERNAME_ENV = "NETWORK_USERNAME"
PASSWORD_ENV = "NETWORK_PASSWORD"

# Netmiko driver for Cisco IOS / IOSv / IOSvL2. Only IOS parsers exist in this project.
CISCO_IOS = "cisco_ios"
DEFAULT_SSH_TIMEOUT = 10  # seconds


class CredentialsError(Exception):
    """Raised when the SSH credentials are not set in the environment."""


class SSHConnectionError(Exception):
    """Raised when an SSH session to a device cannot be opened."""

    status = "connection_failed"


class SSHAuthenticationError(SSHConnectionError):
    status = "auth_failed"


class SSHTimeoutError(SSHConnectionError):
    status = "timeout"


def get_credentials():
    """Read the SSH username and password from environment variables.

    Raises CredentialsError naming the missing variable(s). Values are never
    included in error messages.
    """
    username = os.environ.get(USERNAME_ENV, "").strip()
    password = os.environ.get(PASSWORD_ENV, "")

    missing = [name for name, value in ((USERNAME_ENV, username), (PASSWORD_ENV, password)) if not value]
    if missing:
        raise CredentialsError(
            f"Missing environment variable(s): {', '.join(missing)}. "
            f"Set {USERNAME_ENV} and {PASSWORD_ENV} with the GNS3 lab credentials before running."
        )
    return {"username": username, "password": password}


def build_connection_params(device, credentials, timeout=DEFAULT_SSH_TIMEOUT):
    """Build the Netmiko ConnectHandler arguments for one inventory device."""
    device_type = device.get("device_type")
    if device_type != CISCO_IOS:
        raise ValueError(f"Unsupported device_type '{device_type}' for {device.get('hostname')}: only '{CISCO_IOS}' is supported")

    return {
        "device_type": CISCO_IOS,
        "host": device["host"],
        "port": device.get("port", 22),
        "username": credentials["username"],
        "password": credentials["password"],
        "conn_timeout": timeout,
        "auth_timeout": timeout,
    }


def connect(device, credentials, timeout=DEFAULT_SSH_TIMEOUT):
    """Open an SSH session to a device and return the Netmiko connection.

    Raises SSHAuthenticationError, SSHTimeoutError or SSHConnectionError.
    """
    params = build_connection_params(device, credentials, timeout)
    host = params["host"]

    try:
        return ConnectHandler(**params)
    except NetmikoAuthenticationException as exc:
        raise SSHAuthenticationError(f"SSH authentication failed for {host}") from exc
    except NetmikoTimeoutException as exc:
        raise SSHTimeoutError(f"SSH connection to {host} timed out") from exc
    except Exception as exc:  # refused connection, DNS error, SSH protocol error, ...
        raise SSHConnectionError(f"SSH connection to {host} failed: {exc}") from exc


def disconnect(connection):
    """Close an SSH session. Safe to call with None or an already-broken session."""
    if connection is None:
        return
    try:
        connection.disconnect()
    except Exception:
        pass  # the session is being discarded anyway
