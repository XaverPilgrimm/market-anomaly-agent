"""PostgreSQL connection helper. DATABASE_URL is read from the environment and never printed.

Two authentication modes, chosen by DATABASE_AUTH (002 contracts/cli-contract.md, Environment):

- `password` (also when DATABASE_AUTH is unset or empty): DATABASE_URL carries the credentials,
  exactly as in feature 001. Used locally with `podman compose`.
- `entra`: Azure Database for PostgreSQL with Microsoft Entra authentication only (002 FR-017a,
  FR-020). DATABASE_URL names the role, host and database, carries no password and requires
  `sslmode=verify-full`. Before every connection a short-lived access token is fetched from the
  user-assigned managed identity named by AZURE_CLIENT_ID and used as the password. Each token is
  recorded so the CLI can redact it from every message (002 FR-023); it is never logged.
"""

import os
from urllib.parse import parse_qs, urlsplit

import psycopg
from azure.identity import ManagedIdentityCredential

ENV_VAR = "DATABASE_URL"
AUTH_ENV_VAR = "DATABASE_AUTH"
CLIENT_ID_ENV_VAR = "AZURE_CLIENT_ID"
ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

_PASSWORD = "password"
_ENTRA = "entra"

_issued: list[str] = []


class DatabaseConfigError(RuntimeError):
    """The database connection is not configured."""


def issued_tokens() -> tuple[str, ...]:
    """Every access token issued in this process, for redaction."""
    return tuple(_issued)


def connect() -> psycopg.Connection[tuple[object, ...]]:
    """An autocommit connection: callers group writes with `conn.transaction()`."""
    url = os.environ.get(ENV_VAR)
    if not url:
        raise DatabaseConfigError(f"{ENV_VAR} is not set")
    auth = os.environ.get(AUTH_ENV_VAR) or _PASSWORD
    if auth == _PASSWORD:
        return psycopg.connect(url, autocommit=True)
    if auth == _ENTRA:
        return _connect_entra(url)
    raise DatabaseConfigError(f"{AUTH_ENV_VAR} must be '{_PASSWORD}' or '{_ENTRA}'")


def _connect_entra(url: str) -> psycopg.Connection[tuple[object, ...]]:
    client_id = os.environ.get(CLIENT_ID_ENV_VAR)
    if not client_id:
        raise DatabaseConfigError(f"{CLIENT_ID_ENV_VAR} is required with {AUTH_ENV_VAR}=entra")
    try:
        parts = urlsplit(url)
        has_password = parts.password is not None
        sslmodes = parse_qs(parts.query).get("sslmode", [])
    except ValueError:
        raise DatabaseConfigError(f"{ENV_VAR} is not a valid connection URL") from None
    if has_password:
        raise DatabaseConfigError(
            f"{ENV_VAR} must not contain a password with {AUTH_ENV_VAR}=entra"
        )
    if sslmodes != ["verify-full"]:
        raise DatabaseConfigError(
            f"{ENV_VAR} must set sslmode=verify-full with {AUTH_ENV_VAR}=entra"
        )
    token = ManagedIdentityCredential(client_id=client_id).get_token(ENTRA_SCOPE).token
    _issued.append(token)
    return psycopg.connect(url, password=token, autocommit=True)
