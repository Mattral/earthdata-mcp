"""Tests for the enrichment resolution step."""

from unittest.mock import patch


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(metadata=None):
    """Build a minimal resolution event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": metadata or {"EntryTitle": "Test"},
    }


class TestEnrichResolution:
    """Tests for the resolution enrichment step."""

    @patch("lambdas.enrichment.resolution.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.resolution.prepare_event")
    def test_produces_enriched_metadata(self, mock_prepare, _mock_dehydrate):
        """Should produce enriched_metadata in the result."""
        from lambdas.enrichment.resolution import enrich_resolution

        metadata = {"EntryTitle": "MODIS Daily 1km SST"}
        event = _make_event(metadata)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        result = enrich_resolution(event, None)

        assert "enriched_metadata" in result

    @patch("lambdas.enrichment.resolution.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.resolution.prepare_event")
    def test_preserves_existing_resolution(self, mock_prepare, _mock_dehydrate):
        """Should preserve existing temporal resolution values."""
        from lambdas.enrichment.resolution import enrich_resolution

        metadata = {
            "EntryTitle": "MODIS Daily 1km SST",
            "TemporalExtents": [{"TemporalResolution": {"Unit": "Day", "Value": 1}}],
        }
        event = _make_event(metadata)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        result = enrich_resolution(event, None)

        # Existing resolution should be preserved, not overwritten
        enriched = result["enriched_metadata"]
        assert enriched["TemporalExtents"][0]["TemporalResolution"] == {
            "Unit": "Day",
            "Value": 1,
        }

    @patch("lambdas.enrichment.resolution.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.resolution.prepare_event")
    def test_passes_through_event_fields(self, mock_prepare, _mock_dehydrate):
        """Should pass through concept_id and revision_id in the result."""
        from lambdas.enrichment.resolution import enrich_resolution

        event = _make_event({"EntryTitle": "Test"})
        mock_prepare.return_value = (event, "C1234-PROV", event["metadata"])

        result = enrich_resolution(event, None)

        assert result["concept_id"] == "C1234-PROV"
        assert result["revision_id"] == 5
