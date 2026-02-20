"""Tests for the enrichment fetch step."""

from unittest.mock import patch

import pytest

from util.cmr import CMRError


def _make_event(concept_id="C1234-PROV", revision_id=5):
    """Build a minimal fetch event dict for testing."""
    return {
        "concept_id": concept_id,
        "revision_id": revision_id,
        "concept_type": "collection",
    }


def _noop_dehydrate(event):
    """Identity dehydrate for testing — returns event unchanged."""
    return event


class TestFetch:
    """Tests for the fetch step handler."""

    @patch("lambdas.enrichment.fetch.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fetch._get_existing_record", return_value=(False, None))
    @patch("lambdas.enrichment.fetch.fetch_concept")
    def test_fetches_metadata_from_cmr(self, mock_fetch_concept, _mock_existing, _mock_dehydrate):
        """Should fetch metadata from CMR and include it in the result."""
        from lambdas.enrichment.fetch import fetch

        mock_fetch_concept.return_value = {"EntryTitle": "Test Collection"}

        result = fetch(_make_event(), None)

        mock_fetch_concept.assert_called_once_with("C1234-PROV", "5")
        assert result["metadata"] == {"EntryTitle": "Test Collection"}
        assert result["concept_id"] == "C1234-PROV"

    @patch("lambdas.enrichment.fetch.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fetch._get_existing_record", return_value=(False, None))
    @patch("lambdas.enrichment.fetch.fetch_concept")
    def test_skip_validation_false_for_new_record(
        self, mock_fetch_concept, _mock_existing, _mock_dehydrate
    ):
        """Should set skip_validation to False for new records."""
        from lambdas.enrichment.fetch import fetch

        mock_fetch_concept.return_value = {"EntryTitle": "Test"}

        result = fetch(_make_event(), None)

        assert result["skip_validation"] is False

    @patch("lambdas.enrichment.fetch.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fetch._get_existing_record", return_value=(True, "1.18.2"))
    @patch("lambdas.enrichment.fetch.fetch_concept")
    def test_skip_validation_true_when_valid_same_schema(
        self, mock_fetch_concept, _mock_existing, _mock_dehydrate
    ):
        """Should set skip_validation to True when existing record uses same schema version."""
        from lambdas.enrichment.fetch import fetch

        mock_fetch_concept.return_value = {
            "EntryTitle": "Test",
            "MetadataSpecification": {"Version": "1.18.2"},
        }

        result = fetch(_make_event(), None)

        assert result["skip_validation"] is True

    @patch("lambdas.enrichment.fetch.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fetch._get_existing_record", return_value=(True, "1.17.0"))
    @patch("lambdas.enrichment.fetch.fetch_concept")
    def test_skip_validation_false_when_schema_changed(
        self, mock_fetch_concept, _mock_existing, _mock_dehydrate
    ):
        """Should set skip_validation to False when schema version has changed."""
        from lambdas.enrichment.fetch import fetch

        mock_fetch_concept.return_value = {
            "EntryTitle": "Test",
            "MetadataSpecification": {"Version": "1.18.2"},
        }

        result = fetch(_make_event(), None)

        assert result["skip_validation"] is False

    @patch("lambdas.enrichment.fetch.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fetch._get_existing_record", return_value=(False, None))
    @patch("lambdas.enrichment.fetch.fetch_concept", side_effect=CMRError("Not found"))
    def test_raises_cmr_error(self, _mock_fetch, _mock_existing, _mock_dehydrate):
        """Should propagate CMRError when fetch fails."""
        from lambdas.enrichment.fetch import fetch

        with pytest.raises(CMRError):
            fetch(_make_event(), None)
