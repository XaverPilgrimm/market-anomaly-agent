"""PostgreSQL connection helper. DATABASE_URL is read from the environment and never printed."""

import os

import psycopg

ENV_VAR = "DATABASE_URL"


class DatabaseConfigError(RuntimeError):
    """The database connection is not configured."""


def connect() -> psycopg.Connection[tuple[object, ...]]:
    """An autocommit connection: callers group writes with `conn.transaction()`."""
    url = os.environ.get(ENV_VAR)
    if not url:
        raise DatabaseConfigError(f"{ENV_VAR} is not set")
    return psycopg.connect(url, autocommit=True)
