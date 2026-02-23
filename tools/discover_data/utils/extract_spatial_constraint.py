"""
Spatial constraint extraction with LLM parsing, geocoding, and caching.
"""

import logging

from langfuse import observe

from models.tools.discover_data import ParsedSpatialExtraction, SpatialConstraint
from tools.discover_data.utils.llm_extraction import run_llm_extraction
from util.cache import get_cache_client
from util.langfuse import trace_update
from util.natural_language_geocoder import convert_text_to_geom

logger = logging.getLogger(__name__)

try:
    cache = get_cache_client()
except Exception as e:
    logger.warning("Failed to initialize cache client: %s", e)
    cache = None


def _capitalize_location(location: str) -> str:
    """Capitalize first letter of a location name, preserving the rest."""
    if not location:
        return location
    return location[0].upper() + location[1:] if len(location) > 1 else location.upper()


@observe(name="extract_spatial_with_llm")
def extract_spatial_with_llm(query: str) -> ParsedSpatialExtraction | None:
    """LLM-based spatial extraction.

    Args:
        query: Natural language description potentially containing spatial info.

    Returns:
        ParsedSpatialExtraction (location name, contextual phrase, reasoning, cache key),
        or None when no spatial signal is found; raises on LLM setup/errors.
    """
    output = run_llm_extraction(
        query=query,
        prompt_filename="spatial_extraction.md",
        response_model=ParsedSpatialExtraction,
        extraction_label="spatial info",
    )

    if not output.location_name and not output.location_with_context:
        logger.debug("LLM returned no spatial information for query: %s", query)
        return None

    location_name = _capitalize_location(output.location_name) if output.location_name else None
    location_with_context = (
        _capitalize_location(output.location_with_context) if output.location_with_context else None
    )

    logger.debug(
        "LLM extracted location: %s (canonical: %s)",
        location_with_context,
        location_name,
    )

    return ParsedSpatialExtraction(
        location_name=location_name,
        location_with_context=location_with_context,
        reasoning=output.reasoning,
    )


@observe(name="extract_spatial_constraint")
def extract_spatial_constraint(query: str) -> SpatialConstraint:  # pylint: disable=too-many-branches,too-many-return-statements
    """Convert natural language location query to spatial constraint with caching.

    Args:
        query: A natural language description of a geographic location.
                 Can include cities, regions, countries, geographic features, etc.

    Returns:
        SpatialConstraint with location query and WKT geometry (if successful)
    """
    if not query:
        logger.warning("Empty query provided for spatial constraint extraction.")
        return SpatialConstraint(
            location=None,
            wkt_geometry=None,
        )

    # Use LLM to extract spatial info from the query
    extraction = extract_spatial_with_llm(query)
    if not extraction or not extraction.location_with_context:
        logger.debug("No spatial information extracted from query: %s", query)
        return SpatialConstraint(
            location=None,
            wkt_geometry=None,
            reasoning="No spatial information found in query",
        )

    location_to_geocode = extraction.location_with_context
    canonical_name = extraction.location_name

    # Check cache using standardized location name
    if cache and canonical_name:
        try:
            cache_key = extraction.cache_key
            cached_geom = cache.get(cache_key)
            if cached_geom:
                trace_update(
                    tags=["cache_hit", "success"],
                    metadata={"cache_hit": True, "canonical_name": canonical_name},
                )
                logger.debug("Cache hit for location: %s (key: %s)", canonical_name, cache_key)
                return SpatialConstraint(
                    location=location_to_geocode,
                    wkt_geometry=cached_geom,
                    reasoning=f"{extraction.reasoning} (cached)",
                )
        except Exception as exc:
            logger.debug("Cache lookup failed: %s", exc)

    # Cache miss or cache disabled - geocode the location
    try:
        geom = convert_text_to_geom(location_to_geocode)

        if geom is None:
            logger.warning(
                "Failed to geocode location: %s (check Redis/OpenSearch connectivity)",
                location_to_geocode,
            )
            trace_update(
                tags=["cache_miss", "error", "geocoding_failed"],
                metadata={"error_type": "geocoding_failed", "success": False},
            )
            return SpatialConstraint(
                location=location_to_geocode,
                wkt_geometry=None,
                reasoning=extraction.reasoning,
            )

        # Store in cache
        if cache and canonical_name:
            try:
                cache_key = extraction.cache_key
                cache.set(cache_key, geom, ttl=900)
            except Exception as exc:
                logger.debug("Cache store failed: %s", exc)

        trace_update(
            tags=["cache_miss", "success", "geocoded"],
            metadata={
                "cache_hit": False,
                "success": True,
            },
        )

        logger.debug(
            "Successfully geocoded location '%s': length=%d chars",
            canonical_name,
            len(geom),
        )
        return SpatialConstraint(
            location=location_to_geocode,
            wkt_geometry=geom,
            reasoning=extraction.reasoning,
        )

    except (ValueError, TypeError) as exc:
        logger.warning("Invalid location format for '%s': %s", location_to_geocode, exc)
        trace_update(
            tags=["error", "validation_error"],
            metadata={
                "error_type": "validation_error",
                "error_message": str(exc),
                "location": location_to_geocode,
            },
        )
        return SpatialConstraint(
            location=location_to_geocode,
            wkt_geometry=None,
            reasoning="Unable to resolve location to a geographic area.",
        )

    except Exception as exc:
        logger.exception("Unexpected error geocoding '%s'", location_to_geocode)
        trace_update(
            tags=["error", "exception"],
            metadata={
                "error_type": "exception",
                "exception_class": type(exc).__name__,
                "error_message": str(exc),
                "location": location_to_geocode,
            },
        )
        return SpatialConstraint(
            location=location_to_geocode,
            wkt_geometry=None,
            reasoning="Spatial search is temporarily unavailable. Please try again.",
        )
