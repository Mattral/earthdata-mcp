"""Tests for payload offloading utilities."""

# pylint: disable=missing-function-docstring

from unittest.mock import MagicMock, patch

from lambdas.enrichment.payload import (
    _generate_key,
    _is_claim_check,
    dehydrate_event,
    hydrate_event,
)


def _mock_cache(get_return=None, set_return=True):
    """Create a mock cache client."""
    cache = MagicMock()
    cache.get.return_value = get_return
    cache.set.return_value = set_return
    return cache


class TestMakeKey:
    """Tests for _generate_key helper."""

    def test_builds_expected_key(self):
        assert _generate_key("C1234-PROV", 5, "metadata") == "sfn:C1234-PROV:5:metadata"

    def test_string_revision_id(self):
        assert _generate_key("C1234-PROV", "5", "metadata") == "sfn:C1234-PROV:5:metadata"


class TestIsClaimCheck:
    """Tests for _is_claim_check helper."""

    def test_valid_claim_check(self):
        assert _is_claim_check({"__redis_key": "sfn:C1234:5:metadata"}) is True

    def test_not_claim_check_extra_keys(self):
        assert _is_claim_check({"__redis_key": "k", "extra": 1}) is False

    def test_not_claim_check_regular_dict(self):
        assert _is_claim_check({"EntryTitle": "foo"}) is False

    def test_not_claim_check_string(self):
        assert _is_claim_check("hello") is False

    def test_not_claim_check_none(self):
        assert _is_claim_check(None) is False


class TestHydrateEvent:
    """Tests for hydrate_event."""

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_replaces_claim_check_with_redis_data(self, mock_get_client):
        real_metadata = {"EntryTitle": "Test Collection"}
        cache = _mock_cache(get_return=real_metadata)
        mock_get_client.return_value = cache

        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"__redis_key": "sfn:C1234-PROV:5:metadata"},
        }

        result = hydrate_event(event)

        assert result["metadata"] == real_metadata
        cache.get.assert_called_once_with("sfn:C1234-PROV:5:metadata")

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_leaves_inline_data_unchanged(self, mock_get_client):
        cache = _mock_cache()
        mock_get_client.return_value = cache

        inline_metadata = {"EntryTitle": "Test"}
        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": inline_metadata,
        }

        result = hydrate_event(event)

        assert result["metadata"] == inline_metadata
        cache.get.assert_not_called()

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_leaves_claim_check_on_redis_miss(self, mock_get_client):
        cache = _mock_cache(get_return=None)
        mock_get_client.return_value = cache

        claim_check = {"__redis_key": "sfn:C1234-PROV:5:metadata"}
        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": claim_check,
        }

        result = hydrate_event(event)

        assert result["metadata"] == claim_check

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_hydrates_multiple_fields(self, mock_get_client):
        raw = {"EntryTitle": "Raw"}
        enriched = {"EntryTitle": "Enriched"}

        cache = MagicMock()
        cache.get.side_effect = lambda key: (raw if key.endswith(":metadata") else enriched)
        mock_get_client.return_value = cache

        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"__redis_key": "sfn:C1234-PROV:5:metadata"},
            "enriched_metadata": {"__redis_key": "sfn:C1234-PROV:5:enriched_metadata"},
        }

        result = hydrate_event(event)

        assert result["metadata"] == raw
        assert result["enriched_metadata"] == enriched

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_does_not_modify_original_event(self, mock_get_client):
        cache = _mock_cache(get_return={"EntryTitle": "Loaded"})
        mock_get_client.return_value = cache

        claim_check = {"__redis_key": "sfn:C1234-PROV:5:metadata"}
        event = {"concept_id": "C1234-PROV", "revision_id": 5, "metadata": claim_check}

        hydrate_event(event)

        # Original event should be unmodified
        assert event["metadata"] == claim_check


class TestDehydrateEvent:
    """Tests for dehydrate_event."""

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_offloads_fields_to_redis(self, mock_get_client):
        cache = _mock_cache(set_return=True)
        mock_get_client.return_value = cache

        metadata = {"EntryTitle": "Big Collection", "Abstract": "x" * 10000}
        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": metadata,
        }

        result = dehydrate_event(event)

        assert _is_claim_check(result["metadata"])
        assert result["metadata"]["__redis_key"] == "sfn:C1234-PROV:5:metadata"
        cache.set.assert_called_once_with("sfn:C1234-PROV:5:metadata", metadata, ttl=14400)

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_falls_back_to_inline_on_redis_failure(self, mock_get_client):
        cache = _mock_cache(set_return=False)
        mock_get_client.return_value = cache

        metadata = {"EntryTitle": "Test"}
        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": metadata,
        }

        result = dehydrate_event(event)

        # Data stays inline when Redis fails
        assert result["metadata"] == metadata
        assert not _is_claim_check(result["metadata"])

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_skips_already_offloaded_fields(self, mock_get_client):
        cache = _mock_cache()
        mock_get_client.return_value = cache

        claim_check = {"__redis_key": "sfn:C1234-PROV:5:metadata"}
        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": claim_check,
        }

        result = dehydrate_event(event)

        assert result["metadata"] == claim_check
        cache.set.assert_not_called()

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_skips_missing_fields(self, mock_get_client):
        cache = _mock_cache()
        mock_get_client.return_value = cache

        event = {"concept_id": "C1234-PROV", "revision_id": 5}

        result = dehydrate_event(event)

        assert "metadata" not in result
        cache.set.assert_not_called()

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_dehydrates_multiple_fields(self, mock_get_client):
        cache = _mock_cache(set_return=True)
        mock_get_client.return_value = cache

        event = {
            "concept_id": "C1234-PROV",
            "revision_id": 5,
            "metadata": {"EntryTitle": "Raw"},
            "enriched_metadata": {"EntryTitle": "Enriched"},
        }

        result = dehydrate_event(event)

        assert _is_claim_check(result["metadata"])
        assert _is_claim_check(result["enriched_metadata"])
        assert cache.set.call_count == 2

    @patch("lambdas.enrichment.payload.get_cache_client")
    def test_does_not_modify_original_event(self, mock_get_client):
        cache = _mock_cache(set_return=True)
        mock_get_client.return_value = cache

        metadata = {"EntryTitle": "Test"}
        event = {"concept_id": "C1234-PROV", "revision_id": 5, "metadata": metadata}

        dehydrate_event(event)

        # Original event should be unmodified
        assert event["metadata"] == metadata
