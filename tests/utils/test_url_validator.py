"""Tests for URL validation utility."""

from unittest.mock import patch

from lambdas.enrichment.url_validator import (
    URLValidationResult,
    extract_urls_from_metadata,
    validate_metadata_urls,
)


class TestExtractUrlsFromMetadata:
    """Tests for extract_urls_from_metadata."""

    def test_extracts_related_urls(self):
        """Should extract URLs from RelatedUrls."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://example.com/data1"},
                {"URL": "http://example.com/data2"},
            ]
        }

        urls = extract_urls_from_metadata(metadata)

        assert len(urls) == 2
        assert urls[0]["url"] == "http://example.com/data1"
        assert urls[0]["path"] == "$.RelatedUrls[0].URL"
        assert urls[1]["path"] == "$.RelatedUrls[1].URL"

    def test_extracts_data_center_urls(self):
        """Should extract URLs from DataCenters."""
        metadata = {
            "DataCenters": [
                {"ContactInformation": {"RelatedUrls": [{"URL": "http://datacenter.example.com"}]}}
            ]
        }

        urls = extract_urls_from_metadata(metadata)

        assert len(urls) == 1
        assert urls[0]["url"] == "http://datacenter.example.com"
        assert "DataCenters[0]" in urls[0]["path"]

    def test_extracts_citation_urls(self):
        """Should extract URLs from CollectionCitations."""
        metadata = {
            "CollectionCitations": [{"OnlineResource": {"Linkage": "http://citation.example.com"}}]
        }

        urls = extract_urls_from_metadata(metadata)

        assert len(urls) == 1
        assert urls[0]["url"] == "http://citation.example.com"

    def test_extracts_license_url(self):
        """Should extract LicenseURL."""
        metadata = {"UseConstraints": {"LicenseURL": {"Linkage": "http://license.example.com"}}}

        urls = extract_urls_from_metadata(metadata)

        assert len(urls) == 1
        assert urls[0]["url"] == "http://license.example.com"

    def test_handles_empty_metadata(self):
        """Should return empty list for metadata without URLs."""
        metadata = {"EntryTitle": "Test Collection"}

        urls = extract_urls_from_metadata(metadata)

        assert not urls


class TestURLValidationResult:
    """Tests for URLValidationResult dataclass."""

    def test_creates_valid_result(self):
        """Should create result for valid URL."""
        result = URLValidationResult(
            url="http://example.com",
            is_valid=True,
            status_code=200,
        )
        assert result.is_valid is True
        assert result.status_code == 200
        assert result.error is None

    def test_creates_invalid_result(self):
        """Should create result for invalid URL."""
        result = URLValidationResult(
            url="http://broken.example.com",
            is_valid=False,
            error="Connection refused",
        )
        assert result.is_valid is False
        assert result.error == "Connection refused"


class TestValidateMetadataUrls:
    """Tests for validate_metadata_urls."""

    def test_validates_all_urls(self):
        """Should validate all URLs in metadata."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://example.com/valid", "Description": "Exists"},
                {"URL": "http://example.com/also-valid", "Description": "Exists"},
            ]
        }

        with patch("lambdas.enrichment.url_validator.validate_urls") as mock_validate:
            mock_validate.return_value = [
                URLValidationResult(url="http://example.com/valid", is_valid=True, status_code=200),
                URLValidationResult(
                    url="http://example.com/also-valid", is_valid=True, status_code=200
                ),
            ]

            _, summary = validate_metadata_urls(metadata)

        assert summary.total_urls == 2
        assert summary.valid_urls == 2
        assert summary.invalid_urls == 0

    def test_counts_invalid_urls(self):
        """Should count invalid URLs correctly."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://valid.com", "Description": "Exists"},
                {"URL": "http://invalid.com", "Description": "Exists"},
            ]
        }

        with patch("lambdas.enrichment.url_validator.validate_urls") as mock_validate:
            mock_validate.return_value = [
                URLValidationResult(url="http://valid.com", is_valid=True, status_code=200),
                URLValidationResult(url="http://invalid.com", is_valid=False, error="404"),
            ]

            _, summary = validate_metadata_urls(metadata)

        assert summary.total_urls == 2
        assert summary.valid_urls == 1
        assert summary.invalid_urls == 1

    def test_upgrades_http_to_https(self):
        """Should upgrade HTTP URLs to HTTPS in metadata."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://example.com", "Description": "Exists"},
            ]
        }

        with patch("lambdas.enrichment.url_validator.validate_urls") as mock_validate:
            mock_validate.return_value = [
                URLValidationResult(
                    url="http://example.com",
                    is_valid=True,
                    status_code=200,
                    upgraded_to_https=True,
                ),
            ]

            modified, summary = validate_metadata_urls(metadata)

        assert len(summary.fixed_urls) == 1
        assert modified["RelatedUrls"][0]["URL"] == "https://example.com"

    def test_removes_dead_urls(self):
        """Should remove dead URLs from metadata."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://alive.com", "Description": "Exists"},
                {"URL": "http://dead.com", "Description": "Exists"},
            ]
        }

        with patch("lambdas.enrichment.url_validator.validate_urls") as mock_validate:
            mock_validate.return_value = [
                URLValidationResult(url="http://alive.com", is_valid=True, status_code=200),
                URLValidationResult(
                    url="http://dead.com", is_valid=False, error="Connection refused"
                ),
            ]

            modified, summary = validate_metadata_urls(metadata)

        assert len(modified["RelatedUrls"]) == 1
        assert modified["RelatedUrls"][0]["URL"] == "http://alive.com"
        assert "http://dead.com" in summary.removed_urls

    def test_does_not_modify_original_metadata(self):
        """Should not modify the original metadata dict."""
        metadata = {
            "RelatedUrls": [
                {"URL": "http://dead.com", "Description": "Exists"},
            ]
        }
        original_url = metadata["RelatedUrls"][0]["URL"]

        with patch("lambdas.enrichment.url_validator.validate_urls") as mock_validate:
            mock_validate.return_value = [
                URLValidationResult(url="http://dead.com", is_valid=False, error="404"),
            ]

            validate_metadata_urls(metadata)

        assert metadata["RelatedUrls"][0]["URL"] == original_url

    def test_handles_metadata_without_urls(self):
        """Should handle metadata with no URLs."""
        metadata = {"EntryTitle": "Test"}

        _, summary = validate_metadata_urls(metadata)

        assert summary.total_urls == 0
        assert summary.valid_urls == 0
        assert summary.invalid_urls == 0
