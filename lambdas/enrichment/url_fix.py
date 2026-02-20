"""
Enrichment step: url_fix — Validates and fixes URLs in metadata.

Checks all URLs via HEAD requests and acts on findings:
- Upgrades HTTP → HTTPS where the secure version works
- Removes dead/unreachable URLs from metadata
"""

import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event, prepare_event
from lambdas.enrichment.url_validator import validate_metadata_urls

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def fix_urls(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Validate and fix URLs in metadata, upgrading HTTP to HTTPS and removing dead links."""
    event, concept_id, metadata = prepare_event(event)

    logger.info("Fixing URLs for %s", concept_id)

    modified_metadata, summary = validate_metadata_urls(metadata)

    logger.info(
        "URL fixes for %s: %d total, %d valid, %d invalid, %d HTTPS upgrades, %d removed",
        concept_id,
        summary.total_urls,
        summary.valid_urls,
        summary.invalid_urls,
        len(summary.fixed_urls),
        len(summary.removed_urls),
    )

    # Build a lookup of URL → validation result for logging
    result_by_url = {r.url: r for r in summary.results}
    for removed_url in summary.removed_urls:
        result = result_by_url.get(removed_url)
        status = result.status_code if result else None
        error = result.error if result else None
        logger.warning(
            "Removed dead URL from %s: %s (status=%s, error=%s)",
            concept_id,
            removed_url[:100],
            status,
            error,
        )

    return dehydrate_event(
        {
            **event,
            "enriched_metadata": modified_metadata,
            "url_fix": {
                "total_urls": summary.total_urls,
                "valid_urls": summary.valid_urls,
                "invalid_urls": summary.invalid_urls,
                "https_upgrades": summary.fixed_urls,
                "urls_removed": summary.removed_urls,
            },
        }
    )


@observe(name="enrichment:url_fix")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the url_fix step."""
    return fix_urls(event, context)
