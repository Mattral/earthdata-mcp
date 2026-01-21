"""Tests for KMS (Keyword Management System) client."""

from unittest.mock import MagicMock, patch

import pytest

from util.kms import clear_cache, lookup_terms
from util.models import KMSTerm


@pytest.fixture
def mock_cache():
    """Create a mock cache client that simulates Redis HASH operations."""
    cache = MagicMock()
    cache.is_available.return_value = True
    cache._hash_store = {}  # key -> {field -> value}

    def mock_hexists(key):
        return key in cache._hash_store and len(cache._hash_store[key]) > 0

    def mock_hget(key, field):
        if key in cache._hash_store:
            return cache._hash_store[key].get(field)
        return None

    def mock_hmget(key, fields):
        result = {}
        if key in cache._hash_store:
            for field in fields:
                result[field] = cache._hash_store[key].get(field)
        else:
            for field in fields:
                result[field] = None
        return result

    def mock_hmset(key, mapping, ttl=None):  # pylint: disable=unused-argument
        if key not in cache._hash_store:
            cache._hash_store[key] = {}
        cache._hash_store[key].update(mapping)
        return True

    cache.hexists.side_effect = mock_hexists
    cache.hget.side_effect = mock_hget
    cache.hmget.side_effect = mock_hmget
    cache.hmset.side_effect = mock_hmset
    return cache


@pytest.fixture
def mock_cache_unavailable():
    """Create a mock cache client that is unavailable."""
    cache = MagicMock()
    cache.is_available.return_value = False
    cache.hexists.return_value = False
    cache.hmset.return_value = False
    return cache


class TestKMSTerm:
    """Tests for KMSTerm dataclass."""

    def test_creates_term_with_all_fields(self):
        """Should create term with all required fields."""
        term = KMSTerm(
            uuid="abc-123",
            scheme="sciencekeywords",
            term="PRECIPITATION",
            definition="Water falling from clouds",
        )
        assert term.uuid == "abc-123"
        assert term.scheme == "sciencekeywords"
        assert term.term == "PRECIPITATION"
        assert term.definition == "Water falling from clouds"

    def test_allows_none_definition(self):
        """Should allow None definition."""
        term = KMSTerm(
            uuid="abc-123",
            scheme="platforms",
            term="TERRA",
            definition=None,
        )
        assert term.definition is None


class TestLookupTerms:
    """Tests for lookup_terms batch function."""

    def test_returns_results_for_multiple_terms(self, mock_cache):
        """Should return results for multiple terms from same scheme."""
        scheme_response = {
            "concepts": [
                {
                    "prefLabel": "MODIS",
                    "uuid": "modis-uuid",
                    "definitions": [{"text": "MODIS def"}],
                },
                {
                    "prefLabel": "ASTER",
                    "uuid": "aster-uuid",
                    "definitions": [{"text": "ASTER def"}],
                },
            ]
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            results = lookup_terms([("MODIS", "instruments"), ("ASTER", "instruments")])

            assert len(results) == 2
            assert results[("MODIS", "instruments")].uuid == "modis-uuid"
            assert results[("ASTER", "instruments")].uuid == "aster-uuid"
            # Only one API call for the whole scheme
            assert mock_get.call_count == 1

    def test_handles_multiple_schemes(self, mock_cache):
        """Should handle terms from different schemes."""
        instruments_response = {
            "concepts": [
                {"prefLabel": "MODIS", "uuid": "modis-uuid", "definitions": []},
            ]
        }
        platforms_response = {
            "concepts": [
                {"prefLabel": "TERRA", "uuid": "terra-uuid", "definitions": []},
            ]
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):

            def mock_response(url, **_kwargs):
                mock = MagicMock(status_code=200)
                mock.raise_for_status = MagicMock()
                if "instruments" in url:
                    mock.json.return_value = instruments_response
                else:
                    mock.json.return_value = platforms_response
                return mock

            mock_get.side_effect = mock_response

            results = lookup_terms(
                [
                    ("MODIS", "instruments"),
                    ("TERRA", "platforms"),
                ]
            )

            assert results[("MODIS", "instruments")].uuid == "modis-uuid"
            assert results[("TERRA", "platforms")].uuid == "terra-uuid"
            # Two API calls - one per scheme
            assert mock_get.call_count == 2

    def test_returns_none_for_not_found_terms(self, mock_cache):
        """Should return None for terms not found."""
        scheme_response = {
            "concepts": [
                {"prefLabel": "MODIS", "uuid": "modis-uuid", "definitions": []},
            ]
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            results = lookup_terms(
                [
                    ("MODIS", "instruments"),
                    ("NONEXISTENT", "instruments"),
                ]
            )

            assert results[("MODIS", "instruments")] is not None
            assert results[("NONEXISTENT", "instruments")] is None

    def test_returns_empty_dict_for_empty_input(self, mock_cache):
        """Should return empty dict for empty input."""
        with patch("util.kms.client.get_cache_client", return_value=mock_cache):
            results = lookup_terms([])
            assert not results

    def test_uses_cached_scheme(self, mock_cache):
        """Should use cached scheme for batch lookups."""
        # Pre-populate cache
        mock_cache._hash_store["kms:scheme:instruments"] = {
            "MODIS": {"uuid": "cached-modis", "term": "MODIS", "definition": "Cached"},
            "ASTER": {"uuid": "cached-aster", "term": "ASTER", "definition": "Cached"},
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            results = lookup_terms(
                [
                    ("MODIS", "instruments"),
                    ("ASTER", "instruments"),
                ]
            )

            assert results[("MODIS", "instruments")].uuid == "cached-modis"
            assert results[("ASTER", "instruments")].uuid == "cached-aster"
            mock_get.assert_not_called()


class TestClearCache:
    """Tests for clear_cache function."""

    def test_clear_cache_does_not_error(self):
        """clear_cache should not raise errors."""
        # Just verify it doesn't crash - Redis entries expire via TTL
        clear_cache()
