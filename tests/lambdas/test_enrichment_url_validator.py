"""Tests for URL validation internals — extraction, async validation, URL fixes."""

import asyncio
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from lambdas.enrichment.url_validator import (
    URLValidationResult,
    _apply_https_upgrades,
    _remove_dead_urls,
    _validate_url_async,
    extract_urls_from_metadata,
    validate_metadata_urls,
)


class TestExtractUrlsFromMetadata:
    """Tests for extract_urls_from_metadata."""

    def test_extracts_related_urls(self):
        """Should extract URLs from RelatedUrls with correct indices."""
        metadata = {
            "RelatedUrls": [
                {"URL": "https://example.com/data", "Type": "GET DATA"},
                {
                    "URL": "https://example.com/docs",
                    "Type": "VIEW RELATED INFORMATION",
                    "Description": "Docs",
                },
            ]
        }
        urls = extract_urls_from_metadata(metadata)
        assert len(urls) == 2
        assert urls[0]["url"] == "https://example.com/data"
        assert urls[0]["index"] == 0
        assert urls[1]["index"] == 1

    def test_ignores_datacenter_urls(self):
        """Should not extract URLs from DataCenters contact information."""
        metadata = {
            "DataCenters": [
                {"ContactInformation": {"RelatedUrls": [{"URL": "https://center.example.com"}]}}
            ]
        }
        assert not extract_urls_from_metadata(metadata)

    def test_ignores_collection_citations(self):
        """Should not extract URLs from CollectionCitations."""
        metadata = {
            "CollectionCitations": [{"OnlineResource": {"Linkage": "https://doi.org/10.1234"}}]
        }
        assert not extract_urls_from_metadata(metadata)

    def test_ignores_license_url(self):
        """Should not extract URL from UseConstraints LicenseURL."""
        metadata = {
            "UseConstraints": {
                "LicenseURL": {"Linkage": "https://creativecommons.org/licenses/by/4.0/"}
            }
        }
        assert not extract_urls_from_metadata(metadata)

    def test_empty_metadata(self):
        """Should return empty list for empty metadata."""
        assert not extract_urls_from_metadata({})

    def test_handles_none_related_urls(self):
        """Should handle None RelatedUrls gracefully."""
        metadata = {"RelatedUrls": None}
        assert not extract_urls_from_metadata(metadata)

    def test_skips_entries_without_url(self):
        """Should skip RelatedUrls entries that lack a URL field."""
        metadata = {"RelatedUrls": [{"Type": "GET DATA"}]}
        assert not extract_urls_from_metadata(metadata)


class TestRemoveDeadUrls:
    """Tests for _remove_dead_urls."""

    def test_removes_invalid_urls_from_array(self):
        """Should remove invalid URLs and keep valid ones."""
        metadata = {
            "RelatedUrls": [
                {"URL": "https://good.com"},
                {"URL": "https://dead.com"},
                {"URL": "https://also-good.com"},
            ]
        }
        url_info = [
            {"url": "https://good.com", "index": 0},
            {"url": "https://dead.com", "index": 1},
            {"url": "https://also-good.com", "index": 2},
        ]
        results = [
            URLValidationResult(url="https://good.com", is_valid=True),
            URLValidationResult(url="https://dead.com", is_valid=False, error="404"),
            URLValidationResult(url="https://also-good.com", is_valid=True),
        ]

        removed = _remove_dead_urls(metadata, url_info, results)

        assert removed == ["https://dead.com"]
        assert len(metadata["RelatedUrls"]) == 2
        assert metadata["RelatedUrls"][0]["URL"] == "https://good.com"
        assert metadata["RelatedUrls"][1]["URL"] == "https://also-good.com"

    def test_cleans_up_empty_related_urls_array(self):
        """Should remove RelatedUrls key when all URLs are dead."""
        metadata = {"RelatedUrls": [{"URL": "https://dead.com"}]}
        url_info = [{"url": "https://dead.com", "index": 0}]
        results = [URLValidationResult(url="https://dead.com", is_valid=False)]

        _remove_dead_urls(metadata, url_info, results)

        assert "RelatedUrls" not in metadata

    def test_handles_multiple_removals_in_reverse_order(self):
        """Should correctly remove multiple dead URLs preserving valid ones."""
        metadata = {
            "RelatedUrls": [
                {"URL": "https://dead1.com"},
                {"URL": "https://good.com"},
                {"URL": "https://dead2.com"},
            ]
        }
        url_info = [
            {"url": "https://dead1.com", "index": 0},
            {"url": "https://good.com", "index": 1},
            {"url": "https://dead2.com", "index": 2},
        ]
        results = [
            URLValidationResult(url="https://dead1.com", is_valid=False),
            URLValidationResult(url="https://good.com", is_valid=True),
            URLValidationResult(url="https://dead2.com", is_valid=False),
        ]

        removed = _remove_dead_urls(metadata, url_info, results)

        assert len(removed) == 2
        assert len(metadata["RelatedUrls"]) == 1
        assert metadata["RelatedUrls"][0]["URL"] == "https://good.com"

    def test_no_removals_when_all_valid(self):
        """Should not remove any URLs when all are valid."""
        metadata = {"RelatedUrls": [{"URL": "https://good.com"}]}
        url_info = [{"url": "https://good.com", "index": 0}]
        results = [URLValidationResult(url="https://good.com", is_valid=True)]

        removed = _remove_dead_urls(metadata, url_info, results)

        assert not removed
        assert len(metadata["RelatedUrls"]) == 1


class TestApplyHttpsUpgrades:
    """Tests for _apply_https_upgrades."""

    def test_upgrades_http_to_https(self):
        """Should upgrade HTTP URLs to HTTPS in metadata."""
        metadata = {"RelatedUrls": [{"URL": "http://example.com"}]}
        url_info = [{"url": "http://example.com", "index": 0}]
        results = [
            URLValidationResult(
                url="http://example.com",
                is_valid=True,
                upgraded_to_https=True,
            )
        ]

        fixed = _apply_https_upgrades(metadata, url_info, results)

        assert len(fixed) == 1
        assert fixed[0] == {"original": "http://example.com", "fixed": "https://example.com"}

    def test_skips_already_https(self):
        """Should not upgrade URLs that are already HTTPS."""
        metadata = {"RelatedUrls": [{"URL": "https://example.com"}]}
        url_info = [{"url": "https://example.com", "index": 0}]
        results = [
            URLValidationResult(
                url="https://example.com",
                is_valid=True,
                upgraded_to_https=False,
            )
        ]

        fixed = _apply_https_upgrades(metadata, url_info, results)

        assert not fixed

    def test_skips_invalid_urls(self):
        """Should not upgrade invalid URLs even if they were HTTP."""
        url_info = [{"url": "http://dead.com", "index": 0}]
        results = [
            URLValidationResult(
                url="http://dead.com",
                is_valid=False,
                upgraded_to_https=True,
            )
        ]

        fixed = _apply_https_upgrades({}, url_info, results)

        assert not fixed


class TestValidateMetadataUrls:
    """Tests for validate_metadata_urls (integration of extraction + validation + fixes)."""

    @patch("lambdas.enrichment.url_validator.validate_urls")
    def test_full_flow_with_mixed_results(self, mock_validate):
        """Should handle mixed valid, invalid, and upgraded URLs in one pass."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://upgrade.com", "Type": "GET DATA"},
                {"URL": "https://dead.com", "Type": "GET DATA"},
                {"URL": "https://good.com", "Type": "GET DATA", "Description": "Good"},
            ]
        }
        mock_validate.return_value = [
            URLValidationResult(url="http://upgrade.com", is_valid=True, upgraded_to_https=True),
            URLValidationResult(url="https://dead.com", is_valid=False, error="404"),
            URLValidationResult(url="https://good.com", is_valid=True),
        ]

        _, summary = validate_metadata_urls(metadata)

        assert summary.total_urls == 3
        assert summary.valid_urls == 2
        assert summary.invalid_urls == 1
        assert len(summary.fixed_urls) == 1
        assert len(summary.removed_urls) == 1

    def test_no_urls_returns_empty_summary(self):
        """Should return empty summary when metadata has no URLs."""
        metadata = {"EntryTitle": "No URLs here"}
        _, summary = validate_metadata_urls(metadata)
        assert summary.total_urls == 0
        assert summary.valid_urls == 0

    @patch("lambdas.enrichment.url_validator.validate_urls")
    def test_does_not_mutate_original(self, mock_validate):
        """Should not modify the original metadata dict."""
        metadata = {
            "RelatedUrls": [
                {"URL": "https://dead.com", "Type": "GET DATA"},
            ]
        }
        import copy

        original = copy.deepcopy(metadata)

        mock_validate.return_value = [
            URLValidationResult(url="https://dead.com", is_valid=False),
        ]

        validate_metadata_urls(metadata)

        assert metadata == original


class TestValidateUrlAsyncConnectionErrors:
    """Connection-level errors should be treated as inconclusive (valid)."""

    @pytest.fixture
    def session(self):
        """Create a mock aiohttp session."""
        return MagicMock(spec=aiohttp.ClientSession)

    def _run(self, coro):
        return asyncio.run(coro)

    def test_connection_reset_treated_as_inconclusive(self, session):
        """Connection reset by peer should keep the URL (is_valid=True)."""
        session.head = MagicMock(side_effect=aiohttp.ClientError("Connection reset by peer"))

        result = self._run(_validate_url_async(session, "https://nsidc.org/data.pdf", timeout=5))

        assert result.is_valid is True
        assert "Inconclusive" in result.error

    def test_timeout_treated_as_inconclusive(self, session):
        """Timeout should keep the URL (is_valid=True)."""
        session.head = MagicMock(side_effect=TimeoutError())

        result = self._run(
            _validate_url_async(session, "https://slow-server.example.com", timeout=5)
        )

        assert result.is_valid is True
        assert "Inconclusive" in result.error
        assert "Timeout" in result.error

    @patch("lambdas.enrichment.url_validator.validate_urls")
    def test_connection_error_urls_not_removed(self, mock_validate):
        """URLs with connection errors should not be removed from metadata."""
        metadata = {
            "RelatedUrls": [
                {"URL": "https://doi.org/10.1234", "Type": "VIEW RELATED INFORMATION"},
                {"URL": "https://good.com", "Type": "GET DATA"},
            ]
        }
        mock_validate.return_value = [
            URLValidationResult(
                url="https://doi.org/10.1234",
                is_valid=True,
                error="Inconclusive: Connection reset by peer",
            ),
            URLValidationResult(url="https://good.com", is_valid=True),
        ]

        modified, summary = validate_metadata_urls(metadata)

        assert summary.valid_urls == 2
        assert summary.invalid_urls == 0
        assert not summary.removed_urls
        assert len(modified["RelatedUrls"]) == 2
