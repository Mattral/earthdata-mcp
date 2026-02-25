"""
Enrichment step: fetch — Fetches UMM metadata from CMR.

First step in the enrichment pipeline. Fetches the raw metadata
from CMR and passes it to subsequent validation and enrichment steps.

Also checks if the record already exists in the DB as valid with the same
schema version — if so, sets skip_validation=true to bypass enrichment steps.
"""

import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event
from util.cmr import CMRError, fetch_concept
from util.database import get_db_connection

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _get_existing_record(concept_id: str) -> tuple[bool, str | None]:
    """Check if record exists in DB and return (is_valid, schema_version)."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT is_valid, schema_version FROM collections WHERE concept_id = %s",
                (concept_id,),
            )
            row = cur.fetchone()
            if row:
                return row[0] or False, row[1]
    except Exception as e:
        logger.warning("Failed to check existing record for %s: %s", concept_id, e)
    return False, None


def _extract_schema_version(metadata: dict[str, Any]) -> str | None:
    """Extract the UMM schema version from MetadataSpecification."""
    spec = metadata.get("MetadataSpecification", {})
    return spec.get("Version")


def fetch(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Fetch UMM metadata from CMR.

    Note: metadata is offloaded to Redis on output (see payload.py).
    The shapes below show the hydrated view.

    Input:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection"
        }

    Output:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection",
            "metadata": { ... UMM metadata ... }
        }

    Raises:
        CMRError: If the metadata cannot be fetched from CMR.
    """
    concept_id = event["concept_id"]
    revision_id = event["revision_id"]
    concept_type = event.get("concept_type", "collection")

    logger.info("Fetching %s %s (revision %s)", concept_type, concept_id, revision_id)

    try:
        metadata = fetch_concept(concept_id, str(revision_id))
    except CMRError:
        logger.exception("Failed to fetch %s from CMR", concept_id)
        raise

    logger.info(
        "Fetched %s: %s",
        concept_id,
        metadata.get("EntryTitle", metadata.get("Name", "untitled"))[:100],
    )

    # Determine if we can skip the validation/fixing steps.
    # We skip only when ALL of these are true:
    #   1. Record already exists in DB
    #   2. Record is marked as valid
    #   3. Schema version matches (no new validation rules to apply)
    # If schema version changed, we must re-validate even if previously valid.
    incoming_version = _extract_schema_version(metadata)
    existing_valid, existing_version = _get_existing_record(concept_id)
    skip_validation = existing_valid and existing_version == incoming_version

    if skip_validation:
        logger.info(
            "Skipping validation for %s: already valid with schema %s", concept_id, existing_version
        )
    else:
        logger.info("Validating %s (schema %s)", concept_id, incoming_version)

    return dehydrate_event(
        {
            "concept_id": concept_id,
            "revision_id": revision_id,
            "concept_type": concept_type,
            "metadata": metadata,
            "skip_validation": skip_validation,
        }
    )


@observe(name="enrichment:fetch")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the fetch step."""
    return fetch(event, context)
