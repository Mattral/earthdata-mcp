"""
Enrichment step: validate — Validates UMM metadata against schema and KMS.

Validates metadata using JSON Schema and checks KMS keywords.
Returns a list of validation errors for the fixer agent to address.
"""

import logging
from dataclasses import asdict
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event, prepare_event
from lambdas.enrichment.umm import validate_metadata

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


MAX_FIX_ATTEMPTS_CAP = 30


def _compute_max_fix_attempts(metadata: dict[str, Any], errors: list) -> int:
    """
    Compute max fix attempts from fixable (kms_invalid) field counts only.

    Each loop iteration fixes one kms_invalid error then re-validates, so
    worst case is one attempt per KMS field.  Unfixable errors (schema errors
    like ``required``, ``enum``, etc.) are skipped by the fixer and should not
    extend the loop.
    """
    kms_error_count = sum(1 for e in errors if e.error_type == "kms_invalid")

    if kms_error_count == 0:
        return 0

    science_keywords = metadata.get("ScienceKeywords", [])
    platforms = metadata.get("Platforms", [])
    instruments = sum(len(p.get("Instruments", [])) for p in platforms)
    kms_field_count = len(science_keywords) + len(platforms) + instruments

    return min(kms_field_count, MAX_FIX_ATTEMPTS_CAP)


def validate(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Validate UMM metadata.

    Note: metadata and enriched_metadata are offloaded to Redis between
    steps (see payload.py). The shapes below show the hydrated view.

    Input:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection",
            "metadata": { ... UMM metadata ... },
            "enriched_metadata": { ... optional, if fixes applied ... },
            "fix_attempt": 0  # Current iteration in the fix loop
        }

    Output:
        {
            ... pass through all input fields ...,
            "validation": {
                "is_valid": bool,
                "schema_url": str,
                "errors": [
                    {
                        "path": "$.Platforms[0].Type",
                        "message": "...",
                        "error_type": "enum",
                        "value": "...",
                        "allowed_values": [...],
                        "schema_fragment": {...}
                    }
                ]
            }
        }
    """
    event, concept_id, metadata = prepare_event(event)
    fix_attempt = event.get("fix_attempt", 0)

    logger.info(
        "Validating %s (attempt %d, using %s)",
        concept_id,
        fix_attempt,
        "enriched_metadata" if event.get("enriched_metadata") else "raw metadata",
    )

    result = validate_metadata(metadata)

    logger.info(
        "Validation result for %s: %s (%d errors)",
        concept_id,
        "valid" if result.is_valid else "invalid",
        len(result.errors),
    )

    # Compute dynamic fix loop cap
    max_fix_attempts = _compute_max_fix_attempts(metadata, result.errors)
    # Only flag as exceeded if still invalid — valid records don't need more attempts
    exceeded_max_attempts = fix_attempt >= max_fix_attempts and not result.is_valid

    logger.info(
        "Fix loop for %s: attempt %d / %d max%s",
        concept_id,
        fix_attempt,
        max_fix_attempts,
        " (exceeded)" if exceeded_max_attempts else "",
    )

    if not result.is_valid:
        for error in result.errors[:5]:
            logger.info("  - %s: %s", error.path, error.message[:100])
        if len(result.errors) > 5:
            logger.info("  ... and %d more errors", len(result.errors) - 5)

    validation_output = asdict(result)
    validation_output["max_fix_attempts"] = max_fix_attempts
    validation_output["exceeded_max_attempts"] = exceeded_max_attempts

    return dehydrate_event(
        {
            **event,
            "validation": validation_output,
        }
    )


@observe(name="enrichment:validate")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the validate step."""
    return validate(event, context)
