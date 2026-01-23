"""Tests for the temporal utility module."""

from util.temporal import (
    TemporalResolution,
    check_temporal_disambiguation,
    extract_temporal_resolution,
    group_by_temporal_resolution,
)


class TestExtractTemporalResolution:
    """Tests for extract_temporal_resolution function."""

    def test_extracts_resolution_with_value_and_unit(self):
        """Test extraction from standard TemporalResolution."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Value": 8, "Unit": "Day"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.value == 8
        assert result.unit == "Day"
        assert result.hours == 8 * 24

    def test_extracts_daily_resolution(self):
        """Test extraction of daily resolution."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.value == 1
        assert result.unit == "Day"
        assert str(result) == "Day"

    def test_extracts_hourly_resolution(self):
        """Test extraction of hourly resolution."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Value": 3, "Unit": "Hour"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.value == 3
        assert result.unit == "Hour"
        assert result.hours == 3
        assert str(result) == "3-Hour"

    def test_extracts_monthly_resolution(self):
        """Test extraction of monthly resolution."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Month"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.value == 1
        assert result.unit == "Month"
        assert str(result) == "Month"

    def test_handles_varies_resolution(self):
        """Test handling of 'Varies' special value."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Unit": "Varies"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.unit == "Varies"
        assert result.hours == 0

    def test_handles_constant_resolution(self):
        """Test handling of 'Constant' special value."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Unit": "Constant"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.unit == "Constant"

    def test_returns_none_when_no_resolution(self):
        """Test returns None when no TemporalResolution field."""
        metadata = {
            "TemporalExtents": [{"RangeDateTimes": [{"BeginningDateTime": "2020-01-01T00:00:00Z"}]}]
        }

        result = extract_temporal_resolution(metadata)

        assert result is None

    def test_returns_none_when_no_temporal_extents(self):
        """Test returns None when no TemporalExtents."""
        metadata = {}

        result = extract_temporal_resolution(metadata)

        assert result is None

    def test_returns_none_when_empty_temporal_extents(self):
        """Test returns None when TemporalExtents is empty."""
        metadata = {"TemporalExtents": []}

        result = extract_temporal_resolution(metadata)

        assert result is None

    def test_defaults_value_to_one(self):
        """Test that Value defaults to 1 if not specified."""
        metadata = {"TemporalExtents": [{"TemporalResolution": {"Unit": "Day"}}]}

        result = extract_temporal_resolution(metadata)

        assert result is not None
        assert result.value == 1
        assert result.unit == "Day"


class TestTemporalResolutionStr:
    """Tests for TemporalResolution __str__ method."""

    def test_singular_unit_display(self):
        """Test display when value is 1."""
        resolution = TemporalResolution(value=1, unit="Day", hours=24)
        assert str(resolution) == "Day"

    def test_plural_value_display(self):
        """Test display when value is greater than 1."""
        resolution = TemporalResolution(value=8, unit="Day", hours=192)
        assert str(resolution) == "8-Day"

    def test_fractional_value_display(self):
        """Test display when value is fractional."""
        resolution = TemporalResolution(value=0.5, unit="Hour", hours=0.5)
        assert str(resolution) == "0.5-Hour"


class TestGroupByTemporalResolution:
    """Tests for group_by_temporal_resolution function."""

    def test_groups_by_resolution(self):
        """Test grouping collections by resolution."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 8, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
        ]

        groups = group_by_temporal_resolution(collections)

        assert len(groups) == 2
        assert len(groups["Day"]) == 2
        assert len(groups["8-Day"]) == 1

    def test_groups_none_for_missing_resolution(self):
        """Test that collections without resolution are grouped under None."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
            {"TemporalExtents": [{}]},
            {},
        ]

        groups = group_by_temporal_resolution(collections)

        assert len(groups) == 2
        assert len(groups["Day"]) == 1
        assert len(groups[None]) == 2


class TestCheckTemporalDisambiguation:
    """Tests for check_temporal_disambiguation function."""

    def test_no_disambiguation_when_same_resolution(self):
        """Test no disambiguation needed when all have same resolution."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
        ]

        needs_disambiguation, resolutions = check_temporal_disambiguation(collections)

        assert needs_disambiguation is False
        assert resolutions == ["Day"]

    def test_disambiguation_when_different_resolutions(self):
        """Test disambiguation needed when different resolutions."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 8, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Month"}}]},
        ]

        needs_disambiguation, resolutions = check_temporal_disambiguation(collections)

        assert needs_disambiguation is True
        assert len(resolutions) == 3
        # Should be sorted by duration
        assert resolutions[0] == "Day"
        assert resolutions[1] == "8-Day"
        assert resolutions[2] == "Month"

    def test_ignores_varies_resolution(self):
        """Test that 'Varies' resolution is ignored for disambiguation."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Unit": "Varies"}}]},
        ]

        needs_disambiguation, resolutions = check_temporal_disambiguation(collections)

        assert needs_disambiguation is False
        assert resolutions == ["Day"]

    def test_no_disambiguation_when_no_resolutions(self):
        """Test no disambiguation when no collections have resolution."""
        collections = [
            {"TemporalExtents": [{}]},
            {},
        ]

        needs_disambiguation, resolutions = check_temporal_disambiguation(collections)

        assert needs_disambiguation is False
        assert resolutions == []

    def test_sorts_resolutions_by_duration(self):
        """Test resolutions are sorted by duration (shortest first)."""
        collections = [
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Month"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Hour"}}]},
            {"TemporalExtents": [{"TemporalResolution": {"Value": 1, "Unit": "Day"}}]},
        ]

        _, resolutions = check_temporal_disambiguation(collections)

        assert resolutions == ["Hour", "Day", "Month"]
