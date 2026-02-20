"""
Enrichment step: resolution — Enriches temporal and spatial resolution.

Extracts temporal and spatial resolution from collection titles when
not already present in the metadata. Runs in parallel with URL validation.
"""

import copy
import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event, prepare_event
from util.spatial import parse_spatial_resolution_from_title
from util.temporal import parse_temporal_resolution_from_title

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def enrich_resolution(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Enrich temporal and spatial resolution fields in collection metadata."""
    event, concept_id, metadata = prepare_event(event)

    title = metadata.get("EntryTitle", "")
    logger.info("Enriching resolution for %s: %s", concept_id, title[:100])

    # Check what exists before enrichment
    temporal_before = _has_temporal_resolution(metadata)
    spatial_before = _has_spatial_resolution(metadata)

    # Apply enrichment to a copy
    enriched = copy.deepcopy(metadata)
    _enrich_temporal_resolution(enriched)
    _enrich_spatial_resolution(enriched)

    # Remove empty nested objects that would fail schema validation.
    # These can come from incoming metadata or from setdefault() calls.
    _cleanup_empty_resolution_objects(enriched)

    # Check what exists after enrichment
    temporal_after = _has_temporal_resolution(enriched)
    spatial_after = _has_spatial_resolution(enriched)

    # Extract the resolution values for logging
    temporal_resolution = _extract_temporal_resolution(enriched)
    spatial_resolution = _extract_spatial_resolution(enriched)

    temporal_added = not temporal_before and temporal_after
    spatial_added = not spatial_before and spatial_after

    if temporal_added:
        logger.info(
            "Added temporal resolution for %s: %s",
            concept_id,
            temporal_resolution,
        )
    if spatial_added:
        logger.info(
            "Added spatial resolution for %s: %s",
            concept_id,
            spatial_resolution,
        )

    return dehydrate_event(
        {
            **event,
            "enriched_metadata": enriched,
        }
    )


def _enrich_temporal_resolution(metadata: dict[str, Any]) -> None:
    """Enrich temporal resolution if missing, by parsing from title."""
    temporal_extents = metadata.get("TemporalExtents", [])

    if _has_temporal_resolution(metadata):
        return

    title = metadata.get("EntryTitle", "")
    resolution = parse_temporal_resolution_from_title(title)

    if resolution and temporal_extents:
        temporal_extents[0]["TemporalResolution"] = resolution
    elif resolution:
        metadata["TemporalExtents"] = [{"TemporalResolution": resolution}]


def _enrich_spatial_resolution(metadata: dict[str, Any]) -> None:
    """Enrich spatial resolution if missing, by parsing from title."""
    if _has_spatial_resolution(metadata):
        return

    title = metadata.get("EntryTitle", "")
    resolution = parse_spatial_resolution_from_title(title)

    if not resolution:
        return

    # Only create the nested structure when we actually have a resolution to write
    spatial = metadata.setdefault("SpatialExtent", {})
    horiz = spatial.setdefault("HorizontalSpatialDomain", {})
    res_sys = horiz.setdefault("ResolutionAndCoordinateSystem", {})
    res_sys["HorizontalDataResolution"] = {"GriddedResolutions": [resolution]}


def _has_temporal_resolution(metadata: dict[str, Any]) -> bool:
    """Check if metadata has temporal resolution."""
    return any(extent.get("TemporalResolution") for extent in metadata.get("TemporalExtents", []))


def _has_spatial_resolution(metadata: dict[str, Any]) -> bool:
    """Check if metadata has spatial resolution."""
    spatial = metadata.get("SpatialExtent", {})
    horiz = spatial.get("HorizontalSpatialDomain", {})
    res_sys = horiz.get("ResolutionAndCoordinateSystem", {})
    horiz_res = res_sys.get("HorizontalDataResolution", {})

    return bool(
        horiz_res.get("VariesResolution")
        or horiz_res.get("PointResolution")
        or horiz_res.get("GriddedResolutions")
        or horiz_res.get("NonGriddedResolutions")
    )


def _cleanup_empty_resolution_objects(metadata: dict[str, Any]) -> None:
    """Remove empty nested resolution objects that would fail schema validation.

    Walks the spatial and temporal resolution paths bottom-up and prunes any
    empty dicts left behind (e.g. ``ResolutionAndCoordinateSystem: {}``).
    Does not remove non-empty parents even if resolution is absent.
    """
    # Spatial: SpatialExtent.HorizontalSpatialDomain.ResolutionAndCoordinateSystem
    spatial = metadata.get("SpatialExtent")
    if isinstance(spatial, dict):
        horiz = spatial.get("HorizontalSpatialDomain")
        if isinstance(horiz, dict):
            res_sys = horiz.get("ResolutionAndCoordinateSystem")
            if isinstance(res_sys, dict) and not res_sys:
                del horiz["ResolutionAndCoordinateSystem"]
            if not horiz:
                del spatial["HorizontalSpatialDomain"]
        if not spatial:
            del metadata["SpatialExtent"]

    # Temporal: TemporalExtents[i].TemporalResolution
    temporal_extents = metadata.get("TemporalExtents")
    if isinstance(temporal_extents, list):
        for extent in temporal_extents:
            if isinstance(extent, dict):
                res = extent.get("TemporalResolution")
                if isinstance(res, dict) and not res:
                    del extent["TemporalResolution"]


def _extract_temporal_resolution(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Extract temporal resolution from metadata for logging."""
    for extent in metadata.get("TemporalExtents", []):
        if res := extent.get("TemporalResolution"):
            return res
    return None


def _extract_spatial_resolution(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Extract spatial resolution from metadata for logging."""
    spatial = metadata.get("SpatialExtent", {})
    horiz = spatial.get("HorizontalSpatialDomain", {})
    res_sys = horiz.get("ResolutionAndCoordinateSystem", {})
    horiz_res = res_sys.get("HorizontalDataResolution", {})

    if gridded := horiz_res.get("GriddedResolutions"):
        return gridded[0] if gridded else None
    if non_gridded := horiz_res.get("NonGriddedResolutions"):
        return non_gridded[0] if non_gridded else None

    return None


@observe(name="enrichment:resolution")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the resolution step."""
    return enrich_resolution(event, context)
