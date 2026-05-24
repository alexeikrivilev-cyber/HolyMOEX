from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg


DEFAULT_MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    pass


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise MigrationError("DATABASE_URL is required to apply PostgreSQL migrations")
    return url


def migration_files(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Path]:
    if not migrations_dir.exists():
        raise MigrationError(f"migrations directory does not exist: {migrations_dir}")
    return sorted(path for path in migrations_dir.iterdir() if path.suffix == ".sql")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_schema_migrations(conn: psycopg.Connection) -> None:
    conn.execute("CREATE SCHEMA IF NOT EXISTS audit")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit.schema_migration (
          migration_name TEXT PRIMARY KEY,
          checksum TEXT NOT NULL,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def already_applied(conn: psycopg.Connection, migration_name: str, checksum: str) -> bool:
    row = conn.execute(
        "SELECT checksum FROM audit.schema_migration WHERE migration_name = %s",
        (migration_name,),
    ).fetchone()
    if row is None:
        return False
    stored_checksum = str(row[0])
    if stored_checksum != checksum:
        raise MigrationError(
            f"migration checksum changed after applying: {migration_name}. "
            "Create a new migration instead of editing an applied one."
        )
    return True


def apply_migration(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    checksum = sha256_text(sql)
    if already_applied(conn, path.name, checksum):
        print(f"skip {path.name}")
        return
    print(f"apply {path.name}")
    conn.execute(sql)
    conn.execute(
        "INSERT INTO audit.schema_migration (migration_name, checksum) VALUES (%s, %s)",
        (path.name, checksum),
    )


def main() -> None:
    migrations_dir = Path(os.getenv("POSTGRES_MIGRATIONS_DIR", str(DEFAULT_MIGRATIONS_DIR)))
    with psycopg.connect(database_url(), autocommit=True) as conn:
        ensure_schema_migrations(conn)
        for path in migration_files(migrations_dir):
            apply_migration(conn, path)
    print("postgres migrations applied")


if __name__ == "__main__":
    main()
