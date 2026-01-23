"""
Collection metadata enrichment utilities.

Derives enriched_metadata from raw CMR UMM-C metadata by:
1. Copying all existing fields
2. Filling in missing fields where we can compute them (e.g., resolution from title)
"""

import copy
import re
from typing import Any

from util.temporal import parse_temporal_resolution_from_title


def enrich_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Create enriched version of UMM-C metadata with derived fields filled in.

    Args:
        metadata: Raw UMM-C metadata from CMR

    Returns:
        Enriched metadata (schema compliant) with computed fields
    """
    enriched = copy.deepcopy(metadata)

    # Enrich temporal resolution if missing
    _enrich_temporal_resolution(enriched)

    # Enrich spatial resolution if missing
    _enrich_spatial_resolution(enriched)

    return enriched


def extract_spatial_extent(metadata: dict[str, Any]) -> tuple[str | None, bool]:
    """
    Extract spatial extent as WKT and determine if global.

    Prefers GPolygons over BoundingRectangles. Returns None for Points/Lines.

    Args:
        metadata: UMM-C metadata

    Returns:
        Tuple of (wkt_geometry, is_global)
    """
    spatial = metadata.get("SpatialExtent", {})
    horiz = spatial.get("HorizontalSpatialDomain", {})
    geometry = horiz.get("Geometry", {})

    coordinates = None
    is_global = False

    # Try GPolygons first
    gpolygons = geometry.get("GPolygons", [])
    if gpolygons:
        gpolygon = gpolygons[0]
        points = gpolygon.get("Boundary", {}).get("Points", [])
        if points:
            coordinates = [(p["Longitude"], p["Latitude"]) for p in points]

    # Fall back to BoundingRectangles
    if coordinates is None:
        bboxes = geometry.get("BoundingRectangles", [])
        if bboxes:
            rect = bboxes[0]
            west = rect.get("WestBoundingCoordinate")
            east = rect.get("EastBoundingCoordinate")
            north = rect.get("NorthBoundingCoordinate")
            south = rect.get("SouthBoundingCoordinate")

            if all(coord is not None for coord in [west, east, north, south]):
                coordinates = [
                    (west, north),
                    (east, north),
                    (east, south),
                    (west, south),
                    (west, north),  # Close the polygon
                ]

                # Check if global
                lon_span = east - west
                lat_span = north - south
                is_global = lon_span >= 350 and lat_span >= 170

    if not coordinates:
        return None, False

    # Convert to WKT
    wkt = f"POLYGON(({', '.join(f'{lon} {lat}' for lon, lat in coordinates)}))"

    return wkt, is_global


def _enrich_temporal_resolution(metadata: dict[str, Any]) -> None:
    """Enrich temporal resolution if missing, by parsing from title."""
    temporal_extents = metadata.get("TemporalExtents", [])

    # Check if any extent already has TemporalResolution
    has_resolution = any(extent.get("TemporalResolution") for extent in temporal_extents)

    if has_resolution:
        return

    # Try to extract from title
    title = metadata.get("EntryTitle", "")
    resolution = parse_temporal_resolution_from_title(title)

    if resolution and temporal_extents:
        # Add to first temporal extent
        temporal_extents[0]["TemporalResolution"] = resolution
    elif resolution:
        # Create temporal extent with resolution
        metadata["TemporalExtents"] = [{"TemporalResolution": resolution}]


def _enrich_spatial_resolution(metadata: dict[str, Any]) -> None:
    """Enrich spatial resolution if missing, by parsing from title."""
    spatial = metadata.setdefault("SpatialExtent", {})
    horiz = spatial.setdefault("HorizontalSpatialDomain", {})
    res_sys = horiz.setdefault("ResolutionAndCoordinateSystem", {})
    horiz_res = res_sys.get("HorizontalDataResolution", {})

    # Check if resolution already exists
    has_resolution = (
        horiz_res.get("VariesResolution")
        or horiz_res.get("PointResolution")
        or horiz_res.get("GriddedResolutions")
        or horiz_res.get("NonGriddedResolutions")
    )

    if has_resolution:
        return

    # Try to extract from title
    title = metadata.get("EntryTitle", "")
    resolution = _parse_spatial_resolution_from_title(title)

    if resolution:
        res_sys["HorizontalDataResolution"] = {"GriddedResolutions": [resolution]}


def _parse_spatial_resolution_from_title(title: str) -> dict[str, Any] | None:
    """
    Parse spatial resolution from collection title.

    Returns UMM-C compliant GriddedResolution object.
    """
    # Patterns: "1km", "250m", "0.25 degree", "500 m", "1 km"
    patterns = [
        (r"\b(\d+(?:\.\d+)?)\s*km\b", "Kilometers"),
        (r"\b(\d+(?:\.\d+)?)\s*m\b", "Meters"),
        (r"\b(\d+(?:\.\d+)?)\s*(?:deg|degree)s?\b", "Decimal Degrees"),
    ]

    for pattern, unit in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            # Skip if it looks like a version number or year
            if 1900 < value < 2100:
                continue
            return {
                "XDimension": value,
                "YDimension": value,
                "Unit": unit,
            }

    return None
