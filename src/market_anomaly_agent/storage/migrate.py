"""Applies storage/migrations/*.sql in filename order, each exactly once.

Run with: uv run python -m market_anomaly_agent.storage.migrate
"""

from pathlib import Path

import psycopg

from market_anomaly_agent.storage.db import connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations(
    conn: psycopg.Connection[tuple[object, ...]], migrations_dir: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending migrations; returns the filenames applied by this call.

    `conn` must be in autocommit mode so that each `transaction()` block is a real transaction.
    """
    if not conn.autocommit:
        raise ValueError("apply_migrations needs an autocommit connection")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename   TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    already = {str(row[0]) for row in conn.execute("SELECT filename FROM schema_migrations")}
    applied: list[str] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in already:
            continue
        with conn.transaction():
            conn.execute(path.read_bytes())
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (%s, now())",
                (path.name,),
            )
        applied.append(path.name)
    return applied


def main() -> None:
    with connect() as conn:
        applied = apply_migrations(conn)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")


if __name__ == "__main__":
    main()
