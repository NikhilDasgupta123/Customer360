"""SQLite helpers for CustomerGraph local authentication storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from customergraph.core.config import BASE_DIR, get_settings


def get_sqlite_path() -> Path:
    """Return the SQLite database file path from DATABASE_URL."""
    database_url = get_settings().database_url
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("CustomerGraph supports SQLite DATABASE_URL only, example: sqlite:///./customergraph.db")

    raw_path = database_url.removeprefix(prefix)
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    return db_path.resolve()


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with row dict support."""
    db_path = get_sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_auth_db() -> dict:
    """Create and safely migrate CustomerGraph auth tables."""
    db_path = get_sqlite_path()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                hashed_password TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                company_team TEXT,
                is_first_admin INTEGER NOT NULL DEFAULT 0,
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

        # Existing local databases are upgraded automatically without data loss.
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "company_team" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN company_team TEXT")
        if "token_version" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id)")
        conn.commit()

    return {"ok": True, "database_path": str(db_path), "tables": ["users", "refresh_tokens"]}
