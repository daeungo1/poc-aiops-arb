"""
Application-side PostgreSQL schema initialization.

Docker runs scripts/01_schema.sql only when the database volume is created.
This module lets the app apply the same idempotent schema SQL on startup,
and then runs lightweight migrations that are safe to repeat.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .db.connection import get_conn, is_db_configured

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = PROJECT_ROOT / "scripts" / "01_schema.sql"
MIGRATION_SQL_FILES = ()


def _execute_sql_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Database SQL file not found: {path}")

    sql = path.read_text(encoding="utf-8").strip()
    if not sql:
        return

    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
    finally:
        conn.close()


def ensure_tables_exist() -> None:
    """Ensure PostgreSQL tables, indexes, and repeatable migrations exist."""
    if not is_db_configured():
        logger.info("DB_HOST is not set; skipping database schema initialization.")
        return

    _execute_sql_file(SCHEMA_SQL)
    for migration in MIGRATION_SQL_FILES:
        _execute_sql_file(migration)

    logger.info("Database schema initialization completed.")
