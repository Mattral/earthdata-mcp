"""UMM schema validation with Redis caching.

Validates UMM metadata against JSON Schema and validates KMS keywords.
"""

import logging
from typing import Any

import requests
from jsonschema import Draft7Validator
from jsonschema import ValidationError as JsonSchemaError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

from lambdas.enrichment.models import ValidationError, ValidationResult
from util.cache import get_cache_client
from util.kms import lookup_terms

logger = logging.getLogger(__name__)

# Schema cache TTL - 24 hours (schemas change rarely)
SCHEMA_CACHE_TTL = 86400


def _get_schema_cache_key(schema_url: str) -> str:
    """Generate Redis key for a cached schema."""
    return f"umm:schema:{schema_url}"


def fetch_schema(schema_url: str) -> dict[str, Any] | None:
    """
    Fetch a UMM JSON Schema from URL with Redis caching.

    If the URL doesn't end in .json (i.e. it's a CDN directory URL like
    ``https://cdn.earthdata.nasa.gov/umm/collection/v1.18.4``), appends
    ``/umm-c-json-schema.json`` to get the actual schema file.

    Args:
        schema_url: URL of the JSON Schema (or its CDN directory)

    Returns:
        The JSON Schema as a dict, or None if fetch fails
    """
    # Normalise directory URLs to the actual schema file
    if not schema_url.endswith(".json"):
        schema_url = schema_url.rstrip("/") + "/umm-c-json-schema.json"

    cache = get_cache_client()
    cache_key = _get_schema_cache_key(schema_url)

    # Check cache first
    cached = cache.get(cache_key)
    if cached:
        logger.debug("Schema cache hit for %s", schema_url)
        return cached

    # Fetch from URL
    try:
        response = requests.get(schema_url, timeout=30)
        response.raise_for_status()
        schema = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Failed to fetch schema from %s: %s", schema_url, e)
        return None

    # Cache the schema
    if not cache.set(cache_key, schema, ttl=SCHEMA_CACHE_TTL):
        logger.warning("Failed to cache schema for %s", schema_url)

    return schema


def _fetch_common_schema(base_url: str) -> dict[str, Any] | None:
    """
    Fetch the common UMM JSON Schema (umm-cmn-json-schema.json) from the
    same CDN directory as the collection schema.

    Args:
        base_url: Base CDN directory URL (e.g.
            ``https://cdn.earthdata.nasa.gov/umm/collection/v1.18.4``)

    Returns:
        The common schema as a dict, or None if fetch fails
    """
    base_url = base_url.rstrip("/")
    common_url = f"{base_url}/umm-cmn-json-schema.json"

    cache = get_cache_client()
    cache_key = _get_schema_cache_key(common_url)

    cached = cache.get(cache_key)
    if cached:
        logger.debug("Common schema cache hit for %s", common_url)
        return cached

    try:
        response = requests.get(common_url, timeout=30)
        response.raise_for_status()
        schema = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Failed to fetch common schema from %s: %s", common_url, e)
        return None

    if not cache.set(cache_key, schema, ttl=SCHEMA_CACHE_TTL):
        logger.warning("Failed to cache common schema for %s", common_url)

    return schema


def _extract_schema_url(metadata: dict[str, Any]) -> str | None:
    """
    Extract the schema URL from UMM metadata.

    Args:
        metadata: UMM metadata dict

    Returns:
        Schema URL or None if not found
    """
    spec = metadata.get("MetadataSpecification", {})
    return spec.get("URL")


def _jsonschema_error_to_validation_error(
    error: JsonSchemaError, _schema: dict[str, Any]
) -> ValidationError:
    """Convert a jsonschema ValidationError to our ValidationError format."""
    # Build JSON path from error path
    path_parts = ["$"]
    for part in error.absolute_path:
        if isinstance(part, int):
            path_parts.append(f"[{part}]")
        else:
            path_parts.append(f".{part}")
    json_path = "".join(path_parts)

    error_type = error.validator
    allowed_values = None
    schema_fragment = None

    # For enum errors, capture the allowed values directly.
    # For all other error types, extract the constraint-relevant subset of
    # the schema node so the fixer has enough context to attempt a repair.
    CONSTRAINT_KEYS = {
        "type",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "enum",
        "required",
    }

    if error.validator == "enum":
        allowed_values = list(error.validator_value)
        schema_fragment = {"enum": allowed_values}
    elif error.schema:
        schema_fragment = {k: v for k, v in error.schema.items() if k in CONSTRAINT_KEYS}

    return ValidationError(
        path=json_path,
        message=error.message,
        error_type=error_type,
        value=error.instance if not isinstance(error.instance, dict) else None,
        allowed_values=allowed_values,
        schema_fragment=schema_fragment,
    )


def validate_schema(
    metadata: dict[str, Any],
    schema: dict[str, Any],
    registry: Registry | None = None,
) -> list[ValidationError]:
    """
    Validate metadata against a JSON Schema.

    Args:
        metadata: UMM metadata to validate
        schema: JSON Schema to validate against
        registry: Optional referencing.Registry for resolving ``$ref`` links

    Returns:
        List of validation errors (empty if valid)
    """
    kwargs: dict[str, Any] = {}
    if registry is not None:
        kwargs["registry"] = registry
    validator = Draft7Validator(schema, **kwargs)
    errors = []

    for error in validator.iter_errors(metadata):
        errors.append(_jsonschema_error_to_validation_error(error, schema))

    return errors


def _extract_kms_terms_for_validation(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Extract all KMS terms from metadata for validation.

    Returns:
        List of (term, scheme) tuples
    """
    terms = []

    # Science keywords - extract hierarchical path
    keyword_levels = get_science_keyword_levels(metadata)
    if keyword_levels:
        for kw in metadata.get("ScienceKeywords") or []:
            for level in keyword_levels:
                if value := kw.get(level):
                    terms.append((value, "sciencekeywords"))

    # Platforms
    for platform in metadata.get("Platforms") or []:
        if short_name := platform.get("ShortName"):
            terms.append((short_name, "platforms"))

        # Instruments within platforms
        for instrument in platform.get("Instruments") or []:
            if short_name := instrument.get("ShortName"):
                terms.append((short_name, "instruments"))

    return terms


def _build_kms_path(term: str, scheme: str, metadata: dict[str, Any]) -> str | None:
    """Build JSON path for a KMS term in the metadata.

    Returns None if the term cannot be located — callers should log this
    rather than silently falling back to a vague root path.
    """
    if scheme == "sciencekeywords":
        keyword_levels = get_science_keyword_levels(metadata)
        if keyword_levels:
            for i, kw in enumerate(metadata.get("ScienceKeywords") or []):
                for level in keyword_levels:
                    if kw.get(level) == term:
                        return f"$.ScienceKeywords[{i}].{level}"
    elif scheme == "platforms":
        for i, platform in enumerate(metadata.get("Platforms") or []):
            if platform.get("ShortName") == term:
                return f"$.Platforms[{i}].ShortName"
    elif scheme == "instruments":
        for i, platform in enumerate(metadata.get("Platforms") or []):
            for j, instrument in enumerate(platform.get("Instruments") or []):
                if instrument.get("ShortName") == term:
                    return f"$.Platforms[{i}].Instruments[{j}].ShortName"

    return None


def get_science_keyword_levels(metadata: dict[str, Any]) -> list[str] | None:
    """
    Read the science keyword hierarchy levels from the UMM common schema.

    Extracts property names from ``ScienceKeywordType`` in
    ``umm-cmn-json-schema.json``, which is already Redis-cached (24h TTL).

    Args:
        metadata: UMM metadata dict (must contain ``MetadataSpecification``)

    Returns:
        Ordered list of level names (e.g. ``["Category", "Topic", ...]``),
        or ``None`` if the schema cannot be fetched or parsed.
    """
    schema_url = _extract_schema_url(metadata)
    if not schema_url:
        return None

    base_url = schema_url.rstrip("/")
    if base_url.endswith(".json"):
        base_url = base_url.rsplit("/", 1)[0]

    common_schema = _fetch_common_schema(base_url)
    if common_schema is None:
        return None

    try:
        props = common_schema["definitions"]["ScienceKeywordType"]["properties"]
        return list(props.keys())
    except (KeyError, TypeError):
        logger.warning("ScienceKeywordType not found in common schema")
        return None


def validate_kms_keywords(metadata: dict[str, Any]) -> list[ValidationError]:
    """
    Validate KMS keywords against the KMS cache.

    ScienceKeyword errors are grouped per entry: all invalid levels of a single
    keyword produce ONE error whose ``path`` points to the whole entry
    (``$.ScienceKeywords[i]``), ``value`` is the leaf (most-specific populated
    level), and ``schema_fragment`` carries ``leaf_level`` and ``invalid_levels``.

    Args:
        metadata: UMM metadata containing keywords

    Returns:
        List of validation errors for invalid keywords
    """
    terms = _extract_kms_terms_for_validation(metadata)
    if not terms:
        return []

    # Deduplicate terms for lookup
    unique_terms = list(set(terms))

    # Lookup all terms
    results = lookup_terms(unique_terms)

    errors = []

    # ScienceKeywords: group all invalid levels of each entry into one error.
    # Walk Category → Topic → Term → VariableLevel1-3 to find invalid levels
    # and identify the leaf (most specific populated level) for recommendation.
    keyword_levels = get_science_keyword_levels(metadata)
    for i, kw in enumerate(metadata.get("ScienceKeywords") or []):
        invalid_levels: dict[str, str] = {}
        leaf_level: str | None = None

        for level in keyword_levels or []:
            value = kw.get(level)
            if not value:
                continue
            # Track the leaf (last populated level)
            leaf_level = level
            if results.get((value, "sciencekeywords")) is None:
                invalid_levels[level] = value

        # Emit one error per entry pointing at the whole keyword, not individual fields
        if invalid_levels and leaf_level:
            leaf_value = kw.get(leaf_level, "")
            errors.append(
                ValidationError(
                    path=f"$.ScienceKeywords[{i}]",
                    message=(
                        f"Invalid KMS science keyword at ScienceKeywords[{i}]: "
                        f"invalid levels {invalid_levels}"
                    ),
                    error_type="kms_invalid",
                    value=leaf_value,
                    schema_fragment={
                        "scheme": "sciencekeywords",
                        "leaf_level": leaf_level,
                        "invalid_levels": invalid_levels,
                    },
                )
            )

    # Platforms / Instruments: one term per entry
    seen: set[tuple[str, str]] = set()
    for term, scheme in terms:
        if scheme == "sciencekeywords":
            continue  # already handled above
        if (term, scheme) in seen:
            continue

        kms_term = results.get((term, scheme))
        if kms_term is None:
            seen.add((term, scheme))
            path = _build_kms_path(term, scheme, metadata)
            if path is None:
                logger.warning(
                    "Could not locate KMS term '%s' (scheme '%s') in metadata", term, scheme
                )
            errors.append(
                ValidationError(
                    path=path,
                    message=f"Invalid KMS term '{term}' not found in scheme '{scheme}'",
                    error_type="kms_invalid",
                    value=term,
                    schema_fragment={"scheme": scheme},
                )
            )

    return errors


def _build_ref_registry(schema_url: str) -> Registry | None:
    """
    Build a ``referencing.Registry`` that resolves ``$ref`` links in the
    UMM collection schema (e.g. references to ``umm-cmn-json-schema.json``).

    Args:
        schema_url: The MetadataSpecification URL (CDN directory or .json URL)

    Returns:
        A Registry with the common schema pre-loaded, or None if fetch fails
    """
    # Derive the CDN directory base URL
    base_url = schema_url.rstrip("/")
    if base_url.endswith(".json"):
        base_url = base_url.rsplit("/", 1)[0]

    common_schema = _fetch_common_schema(base_url)
    if common_schema is None:
        return None

    resource = Resource.from_contents(common_schema, default_specification=DRAFT7)
    registry = Registry().with_resource("umm-cmn-json-schema.json", resource)
    return registry


def validate_metadata(
    metadata: dict[str, Any],
    schema: dict[str, Any] | None = None,
    validate_kms: bool = True,
) -> ValidationResult:
    """
    Validate UMM metadata against schema and KMS.

    Args:
        metadata: UMM metadata to validate
        schema: Optional JSON Schema (will be fetched from metadata if not provided)
        validate_kms: Whether to validate KMS keywords

    Returns:
        ValidationResult with is_valid flag and list of errors
    """
    errors = []
    schema_url = _extract_schema_url(metadata)

    # Fetch schema if not provided
    if schema is None and schema_url:
        schema = fetch_schema(schema_url)

    # Build $ref registry when we have a schema URL
    registry = None
    if schema_url:
        registry = _build_ref_registry(schema_url)

    # Validate against JSON Schema
    if schema:
        errors.extend(validate_schema(metadata, schema, registry=registry))
    else:
        logger.warning("No schema available for validation")

    # Validate KMS keywords
    if validate_kms:
        errors.extend(validate_kms_keywords(metadata))

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        schema_url=schema_url,
    )
