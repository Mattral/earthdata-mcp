"""
Enrichment step: store — Stores enriched metadata to database.

Stores the enriched_metadata to the collections table along with
validation status. Both valid and invalid records proceed to embedding.
"""

import json
import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event, prepare_event
from util.database import get_db_connection
from util.spatial import extract_spatial_extent
from util.temporal import extract_temporal_extent

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _extract_schema_version(metadata: dict[str, Any]) -> str | None:
    """Extract the UMM schema version from MetadataSpecification."""
    spec = metadata.get("MetadataSpecification", {})
    return spec.get("Version")


def store(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Store enriched metadata.

    Stores the final enriched_metadata to the collections table,
    including validation status for tracking.

    Note: metadata and enriched_metadata are offloaded to Redis between
    steps (see payload.py). The shapes below show the hydrated view.

    Input:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection",
            "metadata": { ... raw metadata ... },
            "enriched_metadata": { ... enriched metadata ... },
            "validation": {
                "is_valid": bool,
                "schema_url": str,
                "errors": [...],
                "max_fix_attempts": int,
                "exceeded_max_attempts": bool
            },
            "fix_history": [...]
        }

    Output:
        {
            ... pass through all fields ...,
            "store_result": {
                "success": bool,
                "is_valid": bool,
                "error_count": int
            }
        }
    """
    event, concept_id, enriched_metadata = prepare_event(event)
    metadata = event["metadata"]
    validation = event.get("validation", {})

    is_valid = validation.get("is_valid", False)
    errors = validation.get("errors", [])
    schema_version = _extract_schema_version(enriched_metadata)

    logger.info(
        "Storing %s: valid=%s, errors=%d",
        concept_id,
        is_valid,
        len(errors),
    )

    try:
        # Extract temporal/spatial for the collections table
        temporal_start, temporal_end, is_ongoing = extract_temporal_extent(enriched_metadata)
        spatial_wkt, is_global = extract_spatial_extent(enriched_metadata)

        # Store the validation result as-is from the validate step
        validation_state = validation

        # Store to database
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collections (
                    concept_id,
                    temporal_start,
                    temporal_end,
                    is_ongoing,
                    spatial_extent,
                    is_global,
                    metadata,
                    enriched_metadata,
                    is_valid,
                    validation_state,
                    schema_version
                ) VALUES (
                    %s, %s, %s, %s,
                    ST_GeomFromText(%s, 4326),
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (concept_id) DO UPDATE SET
                    temporal_start = EXCLUDED.temporal_start,
                    temporal_end = EXCLUDED.temporal_end,
                    is_ongoing = EXCLUDED.is_ongoing,
                    spatial_extent = EXCLUDED.spatial_extent,
                    is_global = EXCLUDED.is_global,
                    metadata = EXCLUDED.metadata,
                    enriched_metadata = EXCLUDED.enriched_metadata,
                    is_valid = EXCLUDED.is_valid,
                    validation_state = EXCLUDED.validation_state,
                    schema_version = EXCLUDED.schema_version
                """,
                (
                    concept_id,
                    temporal_start,
                    temporal_end,
                    is_ongoing,
                    spatial_wkt,
                    is_global,
                    json.dumps(metadata),
                    json.dumps(enriched_metadata),
                    is_valid,
                    json.dumps(validation_state),
                    schema_version,
                ),
            )

        logger.info("Stored %s successfully", concept_id)

        # Log enrichment changes
        fix_history = event.get("fix_history", [])
        if fix_history:
            log_enrichment_changes(concept_id, event.get("concept_type", "collection"), fix_history)

        return dehydrate_event(
            {
                **event,
                "store_result": {
                    "success": True,
                    "is_valid": is_valid,
                    "error_count": len(errors),
                },
            }
        )

    except Exception as e:
        logger.exception("Failed to store %s", concept_id)
        return dehydrate_event(
            {
                **event,
                "store_result": {
                    "success": False,
                    "is_valid": is_valid,
                    "error_count": len(errors),
                    "error": str(e),
                },
            }
        )


def log_enrichment_changes(
    concept_id: str,
    concept_type: str,
    fix_history: list[dict[str, Any]],
) -> None:
    """
    Log enrichment changes to the enrichment_log table.

    Args:
        concept_id: CMR concept ID
        concept_type: Type of concept (collection, variable)
        fix_history: List of fix results from the fixer agent
    """
    if not fix_history:
        return

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            for fix in fix_history:
                cur.execute(
                    """
                    INSERT INTO enrichment_log (
                        concept_id,
                        concept_type,
                        field_path,
                        action,
                        old_value,
                        new_value,
                        error,
                        notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        concept_id,
                        concept_type,
                        fix.get("field_path", ""),
                        fix.get("action", "unknown"),
                        json.dumps(fix.get("old_value"))
                        if fix.get("old_value") is not None
                        else None,
                        json.dumps(fix.get("new_value"))
                        if fix.get("new_value") is not None
                        else None,
                        fix.get("error"),
                        fix.get("notes"),
                    ),
                )
        logger.info("Logged %d enrichment changes for %s", len(fix_history), concept_id)
    except Exception as e:
        logger.warning("Failed to log enrichment changes for %s: %s", concept_id, e)


@observe(name="enrichment:store")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the store step."""
    return store(event, context)
