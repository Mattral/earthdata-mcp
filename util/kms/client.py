"""NASA KMS (Keyword Management System) API client with Redis caching.

Uses scheme-level caching: fetches entire concept schemes and stores them in
Redis HASHes for O(1) term lookups. This avoids hitting the KMS API with
concurrent pattern-match requests that can overwhelm the gateway.
"""

import logging
from urllib.parse import quote

import requests

from util.cache import get_cache_client
from util.models import KMSTerm

logger = logging.getLogger(__name__)

KMS_BASE_URL = "https://cmr.earthdata.nasa.gov/kms"
KMS_CACHE_TTL = 86400  # 24 hours - KMS terms are very static


def _get_scheme_cache_key(scheme: str) -> str:
    """Generate Redis HASH key for a KMS scheme."""
    return f"kms:scheme:{scheme}"


def _fetch_scheme(scheme: str) -> dict[str, dict]:
    """
    Fetch all concepts in a scheme from the KMS API.

    Returns:
        Dict mapping uppercase term to {uuid, term, definition}
    """
    encoded_scheme = quote(scheme, safe="")
    url = f"{KMS_BASE_URL}/concepts/concept_scheme/{encoded_scheme}"

    response = requests.get(url, params={"format": "json"}, timeout=60)
    response.raise_for_status()
    data = response.json()

    terms = {}
    for concept in data.get("concepts", []):
        pref_label = concept.get("prefLabel")
        uuid = concept.get("uuid")

        if not pref_label or not uuid:
            continue

        # Extract definition if available
        definition = None
        definitions = concept.get("definitions", [])
        if definitions and isinstance(definitions, list):
            definition = definitions[0].get("text")

        # Store with uppercase key for case-insensitive lookup
        terms[pref_label.upper()] = {
            "uuid": uuid,
            "term": pref_label,
            "definition": definition,
        }

    logger.info("Fetched %d terms from KMS scheme '%s'", len(terms), scheme)
    return terms


def _ensure_scheme_cached(scheme: str) -> bool:
    """
    Ensure a KMS scheme is cached in Redis.

    Returns:
        True if scheme is cached (or caching succeeded), False on error
    """
    cache = get_cache_client()

    # Check if already cached
    cache_key = _get_scheme_cache_key(scheme)
    if cache.hexists(cache_key):
        return True

    # Fetch and cache the entire scheme
    try:
        terms = _fetch_scheme(scheme)
    except (requests.RequestException, ValueError) as e:
        logger.warning("Failed to fetch KMS scheme '%s': %s", scheme, e)
        return False

    if not terms:
        logger.warning("KMS scheme '%s' returned no terms", scheme)
        return False

    # Store in Redis HASH
    return cache.hmset(cache_key, terms, ttl=KMS_CACHE_TTL)


def lookup_term(term: str, scheme: str) -> KMSTerm | None:
    """
    Look up a single term in KMS.

    Args:
        term: The term to look up (e.g., "MODIS", "TERRA")
        scheme: KMS concept scheme (e.g., "sciencekeywords", "platforms", "instruments")

    Returns:
        KMSTerm or None if not found
    """
    results = lookup_terms([(term, scheme)])
    return results.get((term, scheme))


def lookup_terms(terms: list[tuple[str, str]]) -> dict[tuple[str, str], KMSTerm | None]:
    """
    Look up multiple terms in KMS with Redis caching.

    Optimized batch lookup: groups terms by scheme, ensures each scheme is
    cached once, then uses HMGET for batch retrieval.

    Args:
        terms: List of (term, scheme) tuples

    Returns:
        Dict mapping (term, scheme) to KMSTerm or None
    """
    if not terms:
        return {}

    cache = get_cache_client()
    results: dict[tuple[str, str], KMSTerm | None] = {}

    # Group terms by scheme for batch operations
    by_scheme: dict[str, list[str]] = {}
    for term, scheme in terms:
        if scheme not in by_scheme:
            by_scheme[scheme] = []
        by_scheme[scheme].append(term)

    # Process each scheme
    for scheme, scheme_terms in by_scheme.items():
        # Ensure scheme is cached
        if not _ensure_scheme_cached(scheme):
            # Failed to cache - mark all terms from this scheme as not found
            for term in scheme_terms:
                results[(term, scheme)] = None
            continue

        # Batch lookup from cached HASH
        cache_key = _get_scheme_cache_key(scheme)
        fields = [t.upper() for t in scheme_terms]
        cached_values = cache.hmget(cache_key, fields)

        # Map results back to original terms
        for term, upper_term in zip(scheme_terms, fields, strict=True):
            cached = cached_values.get(upper_term)
            if cached:
                results[(term, scheme)] = KMSTerm(
                    uuid=cached["uuid"],
                    scheme=scheme,
                    term=cached["term"],
                    definition=cached.get("definition"),
                )
            else:
                results[(term, scheme)] = None

    return results


def clear_cache() -> None:
    """
    Clear the KMS lookup cache. Useful for testing.

    Note: This is a no-op since Redis entries expire via TTL. The function
    exists to maintain a consistent API if a different caching strategy is used.
    """
