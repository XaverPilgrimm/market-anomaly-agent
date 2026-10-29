import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from market_anomaly_agent.storage.migrate import apply_migrations


@pytest.fixture
def db_conn() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """An autocommit connection whose search_path is a fresh schema, dropped afterwards."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"'.encode())
        conn.execute(f'SET search_path TO "{schema}"'.encode())
        try:
            yield conn
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE'.encode())


@pytest.fixture
def migrated_conn(
    db_conn: psycopg.Connection[tuple[object, ...]],
) -> psycopg.Connection[tuple[object, ...]]:
    """`db_conn` with every migration applied."""
    apply_migrations(db_conn)
    return db_conn
