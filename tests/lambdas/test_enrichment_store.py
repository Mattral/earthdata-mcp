"""Tests for the enrichment store step."""

from unittest.mock import MagicMock, patch


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(is_valid=True, errors=None, fix_history=None):
    """Build a minimal store event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": {"EntryTitle": "Raw"},
        "enriched_metadata": {
            "EntryTitle": "Enriched",
            "MetadataSpecification": {"Version": "1.18.2"},
        },
        "validation": {
            "is_valid": is_valid,
            "errors": errors or [],
        },
        "fix_history": fix_history or [],
    }


class TestStore:
    """Tests for the store step handler."""

    @patch("lambdas.enrichment.store.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.store.prepare_event")
    @patch("lambdas.enrichment.store.extract_temporal_extent", return_value=(None, None, False))
    @patch("lambdas.enrichment.store.extract_spatial_extent", return_value=(None, False))
    @patch("lambdas.enrichment.store.get_db_connection")
    def test_stores_valid_record(
        self, mock_db, _mock_spatial, _mock_temporal, mock_prepare, _mock_dehydrate
    ):
        """Should store a valid record in the database."""
        from lambdas.enrichment.store import store

        event = _make_event(is_valid=True)
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        result = store(event, None)

        assert result["store_result"]["success"] is True
        assert result["store_result"]["is_valid"] is True
        mock_cursor.execute.assert_called_once()

    @patch("lambdas.enrichment.store.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.store.prepare_event")
    @patch("lambdas.enrichment.store.extract_temporal_extent", return_value=(None, None, False))
    @patch("lambdas.enrichment.store.extract_spatial_extent", return_value=(None, False))
    @patch("lambdas.enrichment.store.get_db_connection")
    def test_returns_failure_on_db_error(
        self, mock_db, _mock_spatial, _mock_temporal, mock_prepare, _mock_dehydrate
    ):
        """Should return failure result when database operation raises."""
        from lambdas.enrichment.store import store

        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute.side_effect = Exception("DB connection lost")
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        result = store(event, None)

        assert result["store_result"]["success"] is False
        assert "DB connection lost" in result["store_result"]["error"]

    @patch("lambdas.enrichment.store.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.store.prepare_event")
    @patch("lambdas.enrichment.store.extract_temporal_extent", return_value=(None, None, False))
    @patch("lambdas.enrichment.store.extract_spatial_extent", return_value=(None, False))
    @patch("lambdas.enrichment.store.get_db_connection")
    @patch("lambdas.enrichment.store.log_enrichment_changes")
    def test_logs_fix_history(
        self,
        mock_log_changes,
        mock_db,
        _mock_spatial,
        _mock_temporal,
        mock_prepare,
        _mock_dehydrate,
    ):
        """Should log enrichment changes when fix_history is present."""
        from lambdas.enrichment.store import store

        fix_history = [{"action": "recommend_keyword", "field_path": "$.X", "success": True}]
        event = _make_event(fix_history=fix_history)
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        store(event, None)

        mock_log_changes.assert_called_once_with("C1234-PROV", "collection", fix_history)
