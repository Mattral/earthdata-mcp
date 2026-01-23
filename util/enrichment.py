"""
Collection metadata enrichment utilities.

Derives enriched_metadata from raw CMR UMM-C metadata by:
1. Copying all existing fields
2. Filling in missing fields where we can compute them (e.g., resolution from title)
"""

import copy
import re
from datetime import datetime
from typing import Any


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


def _parse_iso_datetime(date_str: str) -> datetime | None:
    """Parse ISO datetime string, returning None on failure."""
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_temporal_extent(
    metadata: dict[str, Any],
) -> tuple[datetime | None, datetime | None, bool]:
    """
    Extract temporal start, end, and ongoing flag from UMM-C metadata.

    Args:
        metadata: UMM-C metadata

    Returns:
        Tuple of (start_date, end_date, is_ongoing)
    """
    start_date = None
    end_date = None
    is_ongoing = False

    temporal_extents = metadata.get("TemporalExtents", [])
    if not temporal_extents:
        return None, None, False

    for extent in temporal_extents:
        if extent.get("EndsAtPresentFlag"):
            is_ongoing = True

        # Handle SingleDateTimes
        for date_str in extent.get("SingleDateTimes", []):
            parsed = _parse_iso_datetime(date_str)
            if parsed:
                if start_date is None or parsed < start_date:
                    start_date = parsed
                if end_date is None or parsed > end_date:
                    end_date = parsed

        # Handle RangeDateTimes
        for range_dt in extent.get("RangeDateTimes", []):
            begin = _parse_iso_datetime(range_dt.get("BeginningDateTime", ""))
            end = _parse_iso_datetime(range_dt.get("EndingDateTime", ""))

            if begin and (start_date is None or begin < start_date):
                start_date = begin
            if end and (end_date is None or end > end_date):
                end_date = end

    # If no end date, consider ongoing
    if end_date is None:
        is_ongoing = True

    return start_date, end_date, is_ongoing


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
    resolution = _parse_temporal_resolution_from_title(title)

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


def _parse_temporal_resolution_from_title(title: str) -> dict[str, Any] | None:
    """
    Parse temporal resolution from collection title.

    Returns UMM-C compliant TemporalResolution object.
    """
    title_lower = title.lower()

    # Check for common patterns
    patterns = [
        (r"\bdaily\b", 1, "Day"),
        (r"\bhourly\b", 1, "Hour"),
        (r"\bmonthly\b", 1, "Month"),
        (r"\bweekly\b", 1, "Week"),
        (r"\byearly\b", 1, "Year"),
        (r"\bannual\b", 1, "Year"),
        (r"\b(\d+)[-\s]?day\b", None, "Day"),
        (r"\b(\d+)[-\s]?hour\b", None, "Hour"),
        (r"\b(\d+)[-\s]?month\b", None, "Month"),
        (r"\b(\d+)[-\s]?minute\b", None, "Minute"),
    ]

    for pattern, default_value, unit in patterns:
        match = re.search(pattern, title_lower)
        if match:
            value = default_value if default_value is not None else int(match.group(1))
            return {"Value": value, "Unit": unit}

    return None


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
