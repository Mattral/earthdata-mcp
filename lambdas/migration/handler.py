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


def run_migration(conn, migration_path: Path) -> None:
    """Execute a single migration file."""
    sql = migration_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def handler(_event: dict, _context) -> dict:
    """
    Lambda handler to run database migrations.

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
    results = []

    try:
        for migration in migrations:
            filename = migration.name
            logger.info("Running migration: %s", filename)
            try:
                run_migration(conn, migration)
                results.append({"file": filename, "status": "success"})
                logger.info("Completed: %s", filename)
            except Exception as e:
                logger.exception("Failed migration %s: %s", filename, e)
                results.append({"file": filename, "status": "failed", "error": str(e)})
                raise
    finally:
        conn.close()

    logger.info("All migrations completed successfully")

    return {
        "message": "Migrations completed",
        "total_run": len(results),
        "migrations_run": results,
    }
