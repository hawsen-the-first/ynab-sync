"""
Database migration script for yanb-sync.

Run this on the server after pulling new code:

    cd backend
    python migrate.py

Each migration is versioned and idempotent — running the script multiple times
is safe.  Applied migrations are recorded in the `schema_migrations` table so
they are never re-applied.

The database path is resolved in this order:
  1. DATABASE_URL environment variable  (strips the SQLAlchemy driver prefix)
  2. DATABASE_URL in a .env file in the same directory as this script
  3. Default: ./data/yanb_sync.db
"""

import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Migration definitions
# Add new migrations to the end of this list.  Never change the version
# number or SQL of an already-applied migration.
# ---------------------------------------------------------------------------

MIGRATIONS = [
    {
        "version": 1,
        "description": "Add balance check and reconciliation columns to sync_logs",
        "sql": [
            "ALTER TABLE sync_logs ADD COLUMN balance_checked BOOLEAN",
            "ALTER TABLE sync_logs ADD COLUMN akahu_balance FLOAT",
            "ALTER TABLE sync_logs ADD COLUMN ynab_balance FLOAT",
            "ALTER TABLE sync_logs ADD COLUMN balance_matched BOOLEAN",
            "ALTER TABLE sync_logs ADD COLUMN reconciliation_triggered BOOLEAN",
            "ALTER TABLE sync_logs ADD COLUMN reconciliation_imported INTEGER",
        ],
    },
    {
        "version": 2,
        "description": "Add progressive reconciliation tracking columns to sync_logs",
        "sql": [
            "ALTER TABLE sync_logs ADD COLUMN reconciliation_passes INTEGER",
            "ALTER TABLE sync_logs ADD COLUMN reconciliation_window_days INTEGER",
        ],
    },
    {
        "version": 3,
        "description": "Add account_name to sync_logs for friendly display",
        "sql": [
            "ALTER TABLE sync_logs ADD COLUMN account_name VARCHAR(255)",
        ],
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_db_path() -> Path:
    """Return the path to the SQLite database file."""
    raw_url = os.environ.get("DATABASE_URL", "")

    # Try loading from .env if not already set
    if not raw_url:
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL"):
                    _, _, value = line.partition("=")
                    raw_url = value.strip().strip('"').strip("'")
                    break

    if raw_url:
        # Strip SQLAlchemy async driver prefixes, e.g.
        # sqlite+aiosqlite:///./data/yanb_sync.db  →  ./data/yanb_sync.db
        raw_url = re.sub(r"^sqlite(?:\+\w+)?:///", "", raw_url)
        return Path(raw_url)

    return Path("./data/yanb_sync.db")


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            description TEXT    NOT NULL,
            applied_at  TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run_migration(conn: sqlite3.Connection, migration: dict) -> None:
    version = migration["version"]
    description = migration["description"]

    print(f"  Applying migration {version}: {description}")

    for statement in migration["sql"]:
        # Parse out table and column from ALTER TABLE … ADD COLUMN …
        # so we can skip statements that have already been applied
        # (handles partial failures on older SQLite versions).
        match = re.match(
            r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)",
            statement,
            re.IGNORECASE,
        )
        if match:
            table, column = match.group(1), match.group(2)
            if column_exists(conn, table, column):
                print(f"    ↳ column '{table}.{column}' already exists, skipping")
                continue

        conn.execute(statement)
        print(f"    ↳ {statement}")

    conn.execute(
        "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
        (version, description, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    print(f"  Migration {version} applied.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db_path = resolve_db_path()

    if not db_path.exists():
        print(f"No database found at {db_path.resolve()} — skipping migrations.")
        print("The app will create and initialise the database on first start.")
        return

    print(f"Database: {db_path.resolve()}")

    conn = sqlite3.connect(db_path)
    try:
        applied = get_applied_versions(conn)

        pending = [m for m in MIGRATIONS if m["version"] not in applied]

        if not pending:
            print("Already up to date — no migrations to apply.")
            return

        print(f"{len(pending)} migration(s) to apply:\n")
        for migration in pending:
            run_migration(conn, migration)

        print("All migrations applied successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
