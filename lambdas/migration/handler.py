"""
Migration Lambda - Runs SQL migrations against the database.

Manually invoked to apply database schema changes. Reads SQL files from
the migrations directory and executes them in order.
"""

# pylint: disable=no-member  # psycopg3 has type inference issues with pylint

import json
import logging
import os
from pathlib import Path

import psycopg

from util.secrets import get_secrets_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_db_connection():
    """Get a plain database connection for running SQL migrations."""
    secret_id = os.environ.get("DATABASE_SECRET_ID")
    if not secret_id:
        raise RuntimeError("DATABASE_SECRET_ID environment variable is not set")

    client = get_secrets_client()
    response = client.get_secret_value(SecretId=secret_id)
    creds = json.loads(response["SecretString"])
    return psycopg.connect(creds["url"])


def get_migration_files() -> list[Path]:
    """Get sorted list of migration SQL files."""
    if not MIGRATIONS_DIR.exists():
        raise RuntimeError(f"Migrations directory not found: {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def ensure_migrations_table(conn) -> None:
    """Create migrations tracking table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                migration_name VARCHAR(255) NOT NULL UNIQUE,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.commit()


def get_executed_migrations(conn) -> set[str]:
    """Get set of migration filenames that have been executed."""
    ensure_migrations_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT migration_name FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def run_migration(conn, migration_path: Path) -> None:
    """Execute a single migration file with tracking to prevent re-execution."""
    migration_name = migration_path.name

    # Check if already executed
    executed = get_executed_migrations(conn)
    if migration_name in executed:
        logger.info("Migration already executed: %s", migration_name)
        return

    sql = migration_path.read_text()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

        # Record successful migration
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations (migration_name) VALUES (%s)",
                (migration_name,),
            )
        conn.commit()

        logger.info("Successfully executed migration: %s", migration_name)
    except Exception as e:
        conn.rollback()
        logger.error("Failed to execute migration %s: %s", migration_name, e)
        raise RuntimeError(f"Migration failed: {migration_name}") from e


def handler(_event: dict, _context) -> dict:
    """
    Lambda handler to run database migrations with idempotency.

    Migrations are tracked in schema_migrations table to prevent re-execution.
    Returns a summary of migrations executed.
    """
    logger.info("Starting database migrations")

    migrations = get_migration_files()
    logger.info("Found %d migration files", len(migrations))

    if not migrations:
        return {
            "message": "No migrations found",
            "migrations_run": [],
        }

    conn = get_db_connection()
    executed_migrations = []
    skipped_migrations = []

    try:
        executed = get_executed_migrations(conn)
        logger.info("Previously executed migrations: %d", len(executed))

        for migration in migrations:
            filename = migration.name

            if filename in executed:
                skipped_migrations.append({"file": filename, "status": "skipped"})
                logger.info("Skipping already executed migration: %s", filename)
                continue

            logger.info("Running migration: %s", filename)
            try:
                run_migration(conn, migration)
                executed_migrations.append({"file": filename, "status": "success"})
            except RuntimeError as e:
                logger.exception("Failed migration %s: %s", filename, e)
                executed_migrations.append({"file": filename, "status": "failed", "error": str(e)})
                raise
    finally:
        conn.close()

    logger.info("Migration complete - run: %d, skipped: %d", len(executed_migrations), len(skipped_migrations))

    return {
        "message": "Migrations completed",
        "total_executed": len(executed_migrations),
        "total_skipped": len(skipped_migrations),
        "migrations_executed": executed_migrations,
        "migrations_skipped": skipped_migrations,
    }
