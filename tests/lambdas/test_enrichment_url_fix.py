"""Tests for the enrichment URL fix step."""

from unittest.mock import patch

from lambdas.enrichment.url_validator import URLValidationSummary


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(metadata=None):
    """Build a minimal URL fix event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": metadata or {"EntryTitle": "Test", "RelatedUrls": []},
    }


class TestFixUrls:
    """Tests for the URL fix step handler."""

    @patch("lambdas.enrichment.url_fix.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.url_fix.prepare_event")
    @patch("lambdas.enrichment.url_fix.validate_metadata_urls")
    def test_returns_enriched_metadata_with_summary(
        self, mock_validate_urls, mock_prepare, _mock_dehydrate
    ):
        """Should return enriched metadata with URL validation summary."""
        from lambdas.enrichment.url_fix import fix_urls

        metadata = {"EntryTitle": "Test", "RelatedUrls": [{"URL": "https://example.com"}]}
        event = _make_event(metadata)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        fixed_metadata = {**metadata}
        summary = URLValidationSummary(
            total_urls=1,
            valid_urls=1,
            invalid_urls=0,
            fixed_urls=[],
            removed_urls=[],
        )
        mock_validate_urls.return_value = (fixed_metadata, summary)

        result = fix_urls(event, None)

        assert result["enriched_metadata"] == fixed_metadata
        assert result["url_fix"]["total_urls"] == 1
        assert result["url_fix"]["valid_urls"] == 1

    @patch("lambdas.enrichment.url_fix.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.url_fix.prepare_event")
    @patch("lambdas.enrichment.url_fix.validate_metadata_urls")
    def test_reports_https_upgrades(self, mock_validate_urls, mock_prepare, _mock_dehydrate):
        """Should report HTTP to HTTPS upgrades in the result."""
        from lambdas.enrichment.url_fix import fix_urls

        metadata = {"EntryTitle": "Test"}
        event = _make_event(metadata)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        summary = URLValidationSummary(
            total_urls=2,
            valid_urls=2,
            invalid_urls=0,
            fixed_urls=[{"original": "http://x.com", "fixed": "https://x.com"}],
            removed_urls=[],
        )
        mock_validate_urls.return_value = (metadata, summary)

        result = fix_urls(event, None)

        assert len(result["url_fix"]["https_upgrades"]) == 1

    @patch("lambdas.enrichment.url_fix.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.url_fix.prepare_event")
    @patch("lambdas.enrichment.url_fix.validate_metadata_urls")
    def test_reports_removed_urls(self, mock_validate_urls, mock_prepare, _mock_dehydrate):
        """Should report removed dead URLs in the result."""
        from lambdas.enrichment.url_fix import fix_urls

        metadata = {"EntryTitle": "Test"}
        event = _make_event(metadata)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        summary = URLValidationSummary(
            total_urls=2,
            valid_urls=1,
            invalid_urls=1,
            fixed_urls=[],
            removed_urls=["http://dead.example.com"],
        )
        mock_validate_urls.return_value = (metadata, summary)

        result = fix_urls(event, None)

        assert result["url_fix"]["urls_removed"] == ["http://dead.example.com"]
