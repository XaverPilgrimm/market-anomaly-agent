import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest


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
