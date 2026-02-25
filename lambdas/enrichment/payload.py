"""
Payload offloading for Step Function events.

Offloads large fields (metadata, enriched_metadata) to Redis to avoid
the 256KB Step Functions payload limit. Each lambda calls hydrate_event()
on entry and dehydrate_event() on exit.

Key format: sfn:{concept_id}:{revision_id}:{field_name}
Claim check pattern: {"__redis_key": "sfn:C1234:5:metadata"}

If Redis write fails, data stays inline (degraded mode).
"""

import logging
from typing import Any

from util.cache import get_cache_client

logger = logging.getLogger(__name__)

OFFLOAD_FIELDS = ("metadata", "enriched_metadata")
KEY_PREFIX = "sfn"
PAYLOAD_TTL = 14400  # 4 hours — must exceed worst-case SFN execution time
# (step timeouts + fix loop iterations + throttle retry backoff)


def _generate_key(concept_id: str, revision_id: str | int, field_name: str) -> str:
    """Build a Redis key for an offloaded field."""
    return f"{KEY_PREFIX}:{concept_id}:{revision_id}:{field_name}"


def _is_claim_check(value: Any) -> bool:
    """Check whether a value is a claim check (a reference to data stored in Redis)."""
    return isinstance(value, dict) and "__redis_key" in value and len(value) == 1


def hydrate_event(event: dict[str, Any]) -> dict[str, Any]:
    """
    Replace claim check references with real data from Redis.

    Called at the start of each step to restore offloaded fields.
    Fields that are already inline (e.g. on the first step) pass
    through unchanged.

    Example::

        # Input (dehydrated — as received from Step Function):
        {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"__redis_key": "sfn:C1234-PROV:5:metadata"}
        }

        # Output (hydrated — ready for the step to use):
        {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"EntryTitle": "MODIS ...", ...}
        }
    """
    cache = get_cache_client()
    result = {**event}

    for field in OFFLOAD_FIELDS:
        value = result.get(field)

        # Skip fields that are missing or already inline
        if value is None or not _is_claim_check(value):
            continue

        # Fetch the real data from Redis
        redis_key = value["__redis_key"]
        data = cache.get(redis_key)
        if data is not None:
            result[field] = data
            logger.debug("Hydrated %s from %s", field, redis_key)
        else:
            # Leave the claim check in place — the step will fail on
            # access, which is correct since the data is genuinely gone.
            logger.warning("Redis miss for %s (key %s), leaving claim check", field, redis_key)

    return result


def dehydrate_event(event: dict[str, Any]) -> dict[str, Any]:
    """
    Offload large fields to Redis, replacing them with claim checks.

    Called at the end of each step before returning to the Step Function.
    Falls back to keeping data inline on Redis failure so the pipeline
    can still proceed (may hit the 256KB limit on very large records).

    Example::

        # Input (hydrated — as built by the step):
        {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"EntryTitle": "MODIS ...", ...}
        }

        # Output (dehydrated — returned to Step Function):
        {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"__redis_key": "sfn:C1234-PROV:5:metadata"}
        }
    """
    concept_id = event.get("concept_id", "unknown")
    revision_id = event.get("revision_id", "0")

    cache = get_cache_client()
    result = {**event}

    for field in OFFLOAD_FIELDS:
        value = result.get(field)

        # Skip fields that are missing or already offloaded
        if value is None or _is_claim_check(value):
            continue

        # Store the real data in Redis and replace with a claim check
        redis_key = _generate_key(concept_id, revision_id, field)
        if cache.set(redis_key, value, ttl=PAYLOAD_TTL):
            result[field] = {"__redis_key": redis_key}
            logger.debug("Dehydrated %s to %s", field, redis_key)
        else:
            # Keep data inline — better to risk hitting the 256KB limit
            # than to lose the data entirely.
            logger.warning(
                "Failed to offload %s to Redis (key %s), keeping inline",
                field,
                redis_key,
            )

    return result


def prepare_event(event: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """
    Hydrate an event and extract common fields used by every enrichment step.

    Returns:
        (hydrated_event, concept_id, metadata) where metadata falls back
        to the raw ``metadata`` field when ``enriched_metadata`` is absent.
    """
    event = hydrate_event(event)
    concept_id = event["concept_id"]
    metadata = event.get("enriched_metadata") or event["metadata"]
    return event, concept_id, metadata
