"""URL validation and fixing for UMM metadata.

Validates URLs using HEAD requests, then acts on findings:
- Upgrades HTTP → HTTPS where the secure version works
- Removes dead/unreachable URLs from metadata
"""

import asyncio
import copy
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

from lambdas.enrichment.umm.json_path import get_value_at_path, set_value_at_path
from util.cache import get_cache_client

logger = logging.getLogger(__name__)

# Timeout for individual URL requests (seconds).
# Needs headroom for redirect chains (e.g. doi.org → nsidc.org) under load.
REQUEST_TIMEOUT = 30

# Maximum concurrent outbound HTTP requests per Lambda invocation.
# Keep this low — with 200 concurrent enrichment Lambdas, aggregate
# outbound requests = 200 × MAX_CONCURRENT. Target URL servers (earthdata,
# doi.org) will rate-limit if this is too high.
MAX_CONCURRENT = 3

# HTTP status codes considered successful
SUCCESS_CODES = {200, 201, 202, 203, 204, 301, 302, 303, 307, 308}

# Cache TTL for URL validation results (1 hour)
URL_CACHE_TTL = 3600

# Retry config for 429 (Too Many Requests) responses
MAX_429_RETRIES = 3
INITIAL_429_BACKOFF = 2  # seconds, doubles each retry

# Identify as a NASA metadata validator so servers don't block us as a bot.
USER_AGENT = (
    "NASA-EarthData-MCP-URLValidator/1.0 (metadata-enrichment; +https://earthdata.nasa.gov)"
)


def _get_url_cache_key(url: str) -> str:
    """Generate Redis key for a cached URL validation result."""
    return f"url:status:{url}"


@dataclass
class URLValidationResult:
    """Result of validating a single URL."""

    url: str
    is_valid: bool
    status_code: int | None = None
    error: str | None = None
    redirected_url: str | None = None
    upgraded_to_https: bool = False


@dataclass
class URLValidationSummary:
    """Summary of URL validation and fixes for a metadata record."""

    total_urls: int
    valid_urls: int
    invalid_urls: int
    results: list[URLValidationResult] = field(default_factory=list)
    removed_urls: list[str] = field(default_factory=list)
    fixed_urls: list[dict[str, str]] = field(default_factory=list)


def extract_urls_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract all URLs from UMM metadata.

    Returns a list of dicts with 'url' and 'path' (JSON path) keys.
    """
    urls = []

    # RelatedUrls
    for i, related_url in enumerate(metadata.get("RelatedUrls") or []):
        if url := related_url.get("URL"):
            urls.append(
                {
                    "url": url,
                    "path": f"$.RelatedUrls[{i}].URL",
                }
            )

    # DataCenters URLs
    for i, dc in enumerate(metadata.get("DataCenters") or []):
        if contact_info := dc.get("ContactInformation"):
            for j, url_obj in enumerate(contact_info.get("RelatedUrls") or []):
                if url := url_obj.get("URL"):
                    urls.append(
                        {
                            "url": url,
                            "path": f"$.DataCenters[{i}].ContactInformation.RelatedUrls[{j}].URL",
                        }
                    )

    # CollectionCitations
    for i, citation in enumerate(metadata.get("CollectionCitations") or []):
        if url := citation.get("OnlineResource", {}).get("Linkage"):
            urls.append(
                {
                    "url": url,
                    "path": f"$.CollectionCitations[{i}].OnlineResource.Linkage",
                }
            )

    # UseConstraints LicenseURL
    if (use_constraints := metadata.get("UseConstraints")) and (
        license_url := use_constraints.get("LicenseURL", {}).get("Linkage")
    ):
        urls.append(
            {
                "url": license_url,
                "path": "$.UseConstraints.LicenseURL.Linkage",
            }
        )

    return urls


async def _head_or_get(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int,
) -> tuple[int, str | None]:
    """Try HEAD then GET fallback. Returns (status_code, redirect_url)."""
    async with session.head(
        url,
        timeout=aiohttp.ClientTimeout(total=timeout),
        allow_redirects=True,
    ) as response:
        if response.status in SUCCESS_CODES:
            redirect = str(response.url) if str(response.url) != url else None
            return response.status, redirect

    # HEAD returned non-success — try GET fallback (some servers block HEAD)
    async with session.get(
        url,
        timeout=aiohttp.ClientTimeout(total=timeout),
        allow_redirects=True,
    ) as response:
        redirect = str(response.url) if str(response.url) != url else None
        return response.status, redirect


async def _validate_url_async(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> URLValidationResult:
    """
    Validate a single URL using a HEAD request.

    Tries HTTPS first, then falls back to HTTP if needed.
    Falls back to GET if HEAD returns a non-success status.
    Retries with exponential backoff on 429 (Too Many Requests).
    """
    parsed = urlparse(url)
    original_url = url

    # Try HTTPS first if currently HTTP
    if parsed.scheme == "http":
        https_url = url.replace("http://", "https://", 1)
        try:
            async with session.head(
                https_url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
            ) as response:
                if response.status in SUCCESS_CODES:
                    return URLValidationResult(
                        url=original_url,
                        is_valid=True,
                        status_code=response.status,
                        redirected_url=str(response.url)
                        if str(response.url) != https_url
                        else None,
                        upgraded_to_https=True,
                    )
        except Exception:
            # HTTPS failed, try original HTTP
            pass

    # Try original URL with HEAD, then GET fallback.
    # Retry with exponential backoff on 429.
    last_status = None
    backoff = INITIAL_429_BACKOFF

    for attempt in range(1 + MAX_429_RETRIES):
        try:
            status, redirect_url = await _head_or_get(session, url, timeout)
            last_status = status

            if status in SUCCESS_CODES:
                return URLValidationResult(
                    url=original_url,
                    is_valid=True,
                    status_code=status,
                    redirected_url=redirect_url,
                )

            # 429: retry with backoff if we have attempts left
            if status == 429 and attempt < MAX_429_RETRIES:
                logger.debug("429 for %s, retrying in %ds (attempt %d)", url, backoff, attempt + 1)
                await asyncio.sleep(backoff)
                backoff *= 2
                continue

            # Non-retryable failure
            return URLValidationResult(
                url=original_url,
                is_valid=False,
                status_code=status,
                redirected_url=redirect_url,
                error=f"HTTP {status}",
            )
        except (TimeoutError, aiohttp.ClientError) as e:
            # Connection-level failures (timeout, TLS reset, DNS error) are
            # inconclusive — the URL may be live but the server is blocking
            # our Lambda's IP/TLS fingerprint.  Mark as valid so we don't
            # remove potentially good URLs from metadata.
            error_msg = "Timeout" if isinstance(e, TimeoutError) else str(e)[:100]
            logger.info("Inconclusive URL check for %s: %s (keeping URL)", url, error_msg)
            return URLValidationResult(
                url=original_url,
                is_valid=True,
                status_code=last_status,
                error=f"Inconclusive: {error_msg}",
            )
        except Exception as e:
            return URLValidationResult(
                url=original_url,
                is_valid=False,
                status_code=last_status,
                error=f"Unexpected error: {str(e)[:100]}",
            )

    # Exhausted 429 retries
    return URLValidationResult(
        url=original_url,
        is_valid=False,
        status_code=last_status,
        error=f"HTTP 429 after {MAX_429_RETRIES} retries",
    )


async def _validate_single_entry(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    entry: dict[str, Any],
    cache: Any = None,
) -> URLValidationResult:
    """Validate a single URL entry, respecting the concurrency semaphore.

    Checks Redis cache first; on miss, validates via HTTP and caches the result.
    Only caches successful results to avoid poisoning from transient failures.
    """
    url = entry["url"]

    # Check cache
    if cache is not None:
        cache_key = _get_url_cache_key(url)
        cached = cache.get(cache_key)
        if cached is not None:
            return URLValidationResult(**cached)

    # Cache miss — validate via HTTP
    async with semaphore:
        result = await _validate_url_async(session, url)

    # Only cache successful results — failures may be transient (timeouts,
    # rate-limiting) and caching them would poison all subsequent lookups.
    if cache is not None and result.is_valid:
        cache_key = _get_url_cache_key(url)
        cache.set(cache_key, asdict(result), ttl=URL_CACHE_TTL)

    return result


async def _validate_urls_async(
    url_entries: list[dict[str, Any]],
    max_concurrent: int = MAX_CONCURRENT,
) -> list[URLValidationResult]:
    """Validate multiple URLs concurrently using a shared session."""
    semaphore = asyncio.Semaphore(max_concurrent)
    cache = get_cache_client()

    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [
            _validate_single_entry(session, semaphore, entry, cache=cache) for entry in url_entries
        ]
        return await asyncio.gather(*tasks)


def validate_urls(url_entries: list[dict[str, Any]]) -> list[URLValidationResult]:
    """
    Validate multiple URLs synchronously.

    This is a wrapper around the async implementation for use in Lambda.
    """
    if not url_entries:
        return []

    return asyncio.run(_validate_urls_async(url_entries))


def _remove_dead_urls(
    metadata: dict[str, Any],
    url_info: list[dict[str, Any]],
    results: list[URLValidationResult],
) -> list[str]:
    """
    Remove dead URLs from metadata.

    Groups removals by parent array and pops indices in reverse order
    to avoid index shifting.

    Returns list of removed URL strings.
    """
    # Group removals by parent array path
    removals: dict[str, list[int]] = {}
    removed_urls: list[str] = []

    for url_entry, result in zip(url_info, results, strict=True):
        if result.is_valid:
            continue

        path = url_entry["path"]
        # Parse path to find the array and index
        # e.g. "$.RelatedUrls[2].URL" → array_path="$.RelatedUrls", index=2
        # e.g. "$.DataCenters[0].ContactInformation.RelatedUrls[1].URL"
        #   → array_path="$.DataCenters[0].ContactInformation.RelatedUrls", index=1

        # Find the last [N] before .URL (or .Linkage)
        bracket_match = list(re.finditer(r"\[(\d+)\]", path))
        if not bracket_match:
            continue

        last_bracket = bracket_match[-1]
        index = int(last_bracket.group(1))
        array_path = path[: last_bracket.start()]

        removals.setdefault(array_path, []).append(index)
        removed_urls.append(url_entry["url"])

    # Remove in reverse index order per array
    for array_path, indices in removals.items():
        arr = get_value_at_path(metadata, array_path)
        if not isinstance(arr, list):
            continue
        for idx in sorted(indices, reverse=True):
            if idx < len(arr):
                arr.pop(idx)

    # Clean up empty arrays left after removal
    if "RelatedUrls" in metadata and not metadata["RelatedUrls"]:
        del metadata["RelatedUrls"]

    return removed_urls


def _apply_https_upgrades(
    metadata: dict[str, Any],
    url_info: list[dict[str, Any]],
    results: list[URLValidationResult],
) -> list[dict[str, str]]:
    """Upgrade HTTP URLs to HTTPS in metadata. Returns list of upgrades."""
    fixed = []
    for url_entry, result in zip(url_info, results, strict=True):
        if result.is_valid and result.upgraded_to_https:
            https_url = url_entry["url"].replace("http://", "https://", 1)
            set_value_at_path(metadata, url_entry["path"], https_url)
            fixed.append({"original": url_entry["url"], "fixed": https_url})
    return fixed


def validate_metadata_urls(
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], URLValidationSummary]:
    """
    Validate and fix all URLs in UMM metadata.

    Applies two fixes:
    1. Upgrades HTTP → HTTPS where the secure version is reachable
    2. Removes dead/unreachable URLs from metadata

    Args:
        metadata: UMM metadata dict

    Returns:
        Tuple of (modified_metadata, validation_summary)
    """
    metadata = copy.deepcopy(metadata)

    url_info = extract_urls_from_metadata(metadata)
    if not url_info:
        return metadata, URLValidationSummary(
            total_urls=0,
            valid_urls=0,
            invalid_urls=0,
        )

    # Validate all URLs
    results = validate_urls(url_info)

    # Build summary counts
    valid_count = sum(1 for r in results if r.is_valid)
    invalid_count = len(results) - valid_count

    # Apply fixes in order: HTTPS upgrades first, then removals
    # (removals last because they shift indices)
    fixed_urls = _apply_https_upgrades(metadata, url_info, results)
    removed_urls = _remove_dead_urls(metadata, url_info, results)

    summary = URLValidationSummary(
        total_urls=len(url_info),
        valid_urls=valid_count,
        invalid_urls=invalid_count,
        results=results,
        removed_urls=removed_urls,
        fixed_urls=fixed_urls,
    )

    return metadata, summary
