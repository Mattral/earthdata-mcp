"""Tests for the warm_kms_cache enrichment step."""

from unittest.mock import patch

import pytest

from lambdas.enrichment.warm_kms_cache import handle


@pytest.fixture
def base_event():
    """Minimal event payload that flows through the step function."""
    return {
        "concept_id": "C1234-PROVIDER",
        "metadata": {"ShortName": "test"},
    }


class TestWarmKMSCacheHandler:
    """Tests for the warm_kms_cache handle() function."""

    def test_all_cached_returns_ready(self, base_event):
        """When all schemes are cached, should return kms_cache_ready=True."""
        with patch("lambdas.enrichment.warm_kms_cache.warm_scheme") as mock_warm:
            mock_warm.return_value = "cached"

            result = handle(base_event, None)

            assert result["kms_cache_ready"] is True
            assert result["kms_warm_attempt"] == 1
            assert result["kms_warm_result"] == {
                "sciencekeywords": "cached",
                "platforms": "cached",
                "instruments": "cached",
            }

    def test_all_fetched_returns_ready(self, base_event):
        """When all schemes are freshly fetched, should return kms_cache_ready=True."""
        with patch("lambdas.enrichment.warm_kms_cache.warm_scheme") as mock_warm:
            mock_warm.return_value = "fetched"

            result = handle(base_event, None)

            assert result["kms_cache_ready"] is True
            assert result["kms_warm_attempt"] == 1

    def test_one_locked_returns_not_ready(self, base_event):
        """When any scheme is locked, should return kms_cache_ready=False."""
        with patch("lambdas.enrichment.warm_kms_cache.warm_scheme") as mock_warm:
            mock_warm.side_effect = ["cached", "locked", "cached"]

            result = handle(base_event, None)

            assert result["kms_cache_ready"] is False
            assert result["kms_warm_result"]["platforms"] == "locked"

    def test_increments_attempt_counter(self, base_event):
        """Should increment kms_warm_attempt from the event."""
        base_event["kms_warm_attempt"] = 5

        with patch("lambdas.enrichment.warm_kms_cache.warm_scheme") as mock_warm:
            mock_warm.return_value = "cached"

            result = handle(base_event, None)

            assert result["kms_warm_attempt"] == 6

    def test_preserves_payload(self, base_event):
        """Should pass through all existing event fields."""
        base_event["skip_validation"] = True
        base_event["enriched_metadata"] = {"some": "data"}

        with patch("lambdas.enrichment.warm_kms_cache.warm_scheme") as mock_warm:
            mock_warm.return_value = "cached"

            result = handle(base_event, None)

            assert result["concept_id"] == "C1234-PROVIDER"
            assert result["skip_validation"] is True
            assert result["enriched_metadata"] == {"some": "data"}
            assert result["kms_cache_ready"] is True
