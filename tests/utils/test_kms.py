"""Tests for KMS (Keyword Management System) client."""

from unittest.mock import MagicMock, patch

import pytest

from models.cmr import KMSTerm
from util.kms import clear_cache, lookup_terms
from util.kms.client import _ensure_scheme_cached, warm_scheme


@pytest.fixture
def mock_cache():
    """Create a mock cache client that simulates Redis HASH operations."""
    cache = MagicMock()
    cache.is_available.return_value = True
    cache._hash_store = {}  # key -> {field -> value}
    cache._locks = {}  # key -> value (for setnx simulation)

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

    def mock_setnx(key, value, ttl):  # pylint: disable=unused-argument
        if key in cache._locks:
            return False
        cache._locks[key] = value
        return True

    def mock_delete(key):
        removed = key in cache._locks
        cache._locks.pop(key, None)
        cache._hash_store.pop(key, None)
        return removed

    cache.hexists.side_effect = mock_hexists
    cache.hget.side_effect = mock_hget
    cache.hmget.side_effect = mock_hmget
    cache.hmset.side_effect = mock_hmset
    cache.setnx.side_effect = mock_setnx
    cache.delete.side_effect = mock_delete
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
            "hits": 2,
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
            ],
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
            # Only one API call — all terms fit on one page
            assert mock_get.call_count == 1

    def test_handles_multiple_schemes(self, mock_cache):
        """Should handle terms from different schemes."""
        instruments_response = {
            "hits": 1,
            "concepts": [
                {"prefLabel": "MODIS", "uuid": "modis-uuid", "definitions": []},
            ],
        }
        platforms_response = {
            "hits": 1,
            "concepts": [
                {"prefLabel": "TERRA", "uuid": "terra-uuid", "definitions": []},
            ],
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
            "hits": 1,
            "concepts": [
                {"prefLabel": "MODIS", "uuid": "modis-uuid", "definitions": []},
            ],
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

    def test_paginates_through_all_pages(self, mock_cache):
        """Should fetch all pages when scheme has more terms than page size."""
        page1 = {
            "hits": 3,
            "concepts": [
                {"prefLabel": "ALPHA", "uuid": "alpha-uuid", "definitions": []},
                {"prefLabel": "BETA", "uuid": "beta-uuid", "definitions": []},
            ],
        }
        page2 = {
            "hits": 3,
            "concepts": [
                {"prefLabel": "GAMMA", "uuid": "gamma-uuid", "definitions": []},
            ],
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
            patch("util.kms.client._KMS_PAGE_SIZE", 2),
        ):
            responses = [MagicMock(status_code=200), MagicMock(status_code=200)]
            responses[0].json.return_value = page1
            responses[0].raise_for_status = MagicMock()
            responses[1].json.return_value = page2
            responses[1].raise_for_status = MagicMock()
            mock_get.side_effect = responses

            results = lookup_terms(
                [
                    ("ALPHA", "sciencekeywords"),
                    ("GAMMA", "sciencekeywords"),
                ]
            )

            assert results[("ALPHA", "sciencekeywords")].uuid == "alpha-uuid"
            assert results[("GAMMA", "sciencekeywords")].uuid == "gamma-uuid"
            # Two API calls — one per page
            assert mock_get.call_count == 2

    def test_returns_fetched_data_when_caching_fails(self):
        """Should return fetched data even when cache.hmset fails."""
        cache = MagicMock()
        cache.hexists.return_value = False  # Not cached
        cache.hmset.return_value = False  # Caching fails

        scheme_response = {
            "hits": 1,
            "concepts": [
                {
                    "prefLabel": "MODIS",
                    "uuid": "fetched-modis",
                    "definitions": [{"text": "MODIS definition"}],
                },
            ],
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            results = lookup_terms([("MODIS", "instruments")])

            # Should still return the fetched data
            assert results[("MODIS", "instruments")] is not None
            assert results[("MODIS", "instruments")].uuid == "fetched-modis"
            assert results[("MODIS", "instruments")].term == "MODIS"
            # Verify caching was attempted
            cache.hmset.assert_called_once()


class TestWarmScheme:
    """Tests for the non-blocking warm_scheme() function."""

    def test_returns_cached_when_scheme_exists(self, mock_cache):
        """Should return 'cached' immediately when scheme is already in Redis."""
        mock_cache._hash_store["kms:scheme:instruments"] = {"MODIS": {}}

        with patch("util.kms.client.get_cache_client", return_value=mock_cache):
            result = warm_scheme("instruments")

            assert result == "cached"
            mock_cache.setnx.assert_not_called()

    def test_returns_fetched_when_lock_acquired(self, mock_cache):
        """Lock holder should fetch, cache, release lock, and return 'fetched'."""
        scheme_response = {
            "hits": 1,
            "concepts": [
                {
                    "prefLabel": "MODIS",
                    "uuid": "modis-uuid",
                    "definitions": [{"text": "MODIS def"}],
                },
            ],
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            result = warm_scheme("instruments")

            assert result == "fetched"
            mock_get.assert_called_once()
            mock_cache.setnx.assert_called_once()
            mock_cache.delete.assert_called_once_with("kms:lock:instruments")
            assert "kms:scheme:instruments" in mock_cache._hash_store

    def test_returns_locked_when_another_lambda_holds_lock(self, mock_cache):
        """Should return 'locked' immediately when lock is held by another Lambda."""
        mock_cache._locks["kms:lock:instruments"] = "1"

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            result = warm_scheme("instruments")

            assert result == "locked"
            mock_get.assert_not_called()

    def test_returns_locked_on_fetch_failure(self, mock_cache):
        """Should release lock and return 'locked' when fetch raises."""
        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            import requests

            mock_get.side_effect = requests.RequestException("timeout")

            result = warm_scheme("instruments")

            assert result == "locked"
            mock_cache.delete.assert_called_once_with("kms:lock:instruments")

    def test_returns_locked_on_empty_terms(self, mock_cache):
        """Should release lock and return 'locked' when scheme has no terms."""
        scheme_response = {"hits": 0, "concepts": []}

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            result = warm_scheme("instruments")

            assert result == "locked"
            mock_cache.delete.assert_called_once_with("kms:lock:instruments")


class TestEnsureSchemeCached:
    """Tests for the simplified _ensure_scheme_cached (no lock, no polling)."""

    def test_returns_empty_dict_on_cache_hit(self, mock_cache):
        """Should return {} immediately when scheme is cached."""
        mock_cache._hash_store["kms:scheme:instruments"] = {"MODIS": {}}

        with patch("util.kms.client.get_cache_client", return_value=mock_cache):
            result = _ensure_scheme_cached("instruments")
            assert result == {}

    def test_fetches_on_cache_miss(self, mock_cache):
        """Should fetch from API and cache when scheme is not cached."""
        scheme_response = {
            "hits": 1,
            "concepts": [
                {
                    "prefLabel": "MODIS",
                    "uuid": "modis-uuid",
                    "definitions": [{"text": "MODIS def"}],
                },
            ],
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            response_mock = MagicMock(status_code=200)
            response_mock.json.return_value = scheme_response
            response_mock.raise_for_status = MagicMock()
            mock_get.return_value = response_mock

            result = _ensure_scheme_cached("instruments")

            assert result is not None
            assert "MODIS" in result
            assert result["MODIS"]["uuid"] == "modis-uuid"
            mock_get.assert_called_once()

    def test_retries_on_failure(self, mock_cache):
        """Should retry on fetch failure and succeed on next attempt."""
        import requests as req

        scheme_response = {
            "hits": 1,
            "concepts": [
                {
                    "prefLabel": "MODIS",
                    "uuid": "modis-uuid",
                    "definitions": [{"text": "MODIS def"}],
                },
            ],
        }

        with (
            patch("util.kms.client.get_cache_client", return_value=mock_cache),
            patch("util.kms.client.requests.get") as mock_get,
        ):
            success = MagicMock(status_code=200)
            success.json.return_value = scheme_response
            success.raise_for_status = MagicMock()
            mock_get.side_effect = [req.RequestException("timeout"), success]

            result = _ensure_scheme_cached("instruments")

            assert result is not None
            assert "MODIS" in result
            assert mock_get.call_count == 2


class TestClearCache:
    """Tests for clear_cache function."""

    def test_clear_cache_does_not_error(self):
        """clear_cache should not raise errors."""
        # Just verify it doesn't crash - Redis entries expire via TTL
        clear_cache()
