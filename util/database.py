"""Database utilities for PostgreSQL with pgvector support."""

# pylint: disable=no-member  # psycopg3 has type inference issues with pylint

import contextlib
import json
import logging
import os
from functools import lru_cache
from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from util.secrets import get_secrets_client

logger = logging.getLogger(__name__)

DATABASE_SECRET_ID = os.environ.get("DATABASE_SECRET_ID")

# Module-level connection pool (reused across Lambda invocations)
_connection: Any = None


@lru_cache(maxsize=1)
def get_database_credentials() -> dict[str, Any]:
    """Fetch database credentials from Secrets Manager (cached)."""
    if not DATABASE_SECRET_ID:
        raise RuntimeError(
            "DATABASE_SECRET_ID environment variable is not set. "
            "Set this to your AWS Secrets Manager secret ID containing database credentials."
        )
    client = get_secrets_client()
    response = client.get_secret_value(SecretId=DATABASE_SECRET_ID)
    return json.loads(response["SecretString"])


def _is_connection_healthy(conn: Any) -> bool:
    """Check if connection is still usable."""
    if conn is None or conn.closed:
        return False
    try:
        # Quick health check - will fail if connection is stale
        # Use a transaction context to ensure clean state
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def get_db_connection() -> Any:
    """
    Get the database connection (lazy initialization, reused across Lambda invocations).

    The connection is cached at module level for reuse during warm starts.
    If the connection is closed or broken, a new one will be created.
    """
    global _connection
    if not _is_connection_healthy(_connection):
        # Close stale connection if it exists
        if _connection is not None:
            with contextlib.suppress(Exception):
                _connection.close()
        creds = get_database_credentials()
        _connection = psycopg.connect(creds["url"], autocommit=True)
        register_vector(_connection)
        logger.info("Created new database connection")
    return _connection
