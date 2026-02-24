"""
Enrichment step: warm_kms_cache — Pre-warm KMS scheme caches.

Non-blocking cache warm that returns immediately.  The Step Function
handles retry/wait via a Choice + Wait loop so the Lambda never idles.
"""

import logging
from typing import Any

from langfuse import observe

from util.kms import warm_scheme

logger = logging.getLogger(__name__)

_KMS_SCHEMES = ("sciencekeywords", "platforms", "instruments")


@observe(name="enrichment:warm_kms_cache")
def handle(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Warm all KMS scheme caches; return readiness status."""
    all_ready = True
    results = {}

    for scheme in _KMS_SCHEMES:
        status = warm_scheme(scheme)
        results[scheme] = status
        if status == "locked":
            all_ready = False

    attempt = event.get("kms_warm_attempt", 0) + 1

    logger.info(
        "KMS cache warm attempt %d: %s (ready=%s)",
        attempt,
        results,
        all_ready,
    )

    return {
        **event,
        "kms_cache_ready": all_ready,
        "kms_warm_attempt": attempt,
        "kms_warm_result": results,
    }
