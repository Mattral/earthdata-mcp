"""Tests for the enrichment pipeline router handler."""

import types
from unittest.mock import MagicMock, patch

import pytest

from lambdas.enrichment.handler import _extract_trace_output, handler


class TestExtractTraceOutput:
    """Tests for _extract_trace_output helper."""

    def test_strips_large_fields(self):
        """Should remove metadata, enriched_metadata, and fix_history from trace output."""
        result = {
            "concept_id": "C1234-PROV",
            "metadata": {"big": "data"},
            "enriched_metadata": {"big": "enriched"},
            "fix_history": [{"action": "fix"}],
            "validation": {"is_valid": True},
        }

        output = _extract_trace_output(result)

        assert "metadata" not in output
        assert "enriched_metadata" not in output
        assert "fix_history" not in output
        assert output["concept_id"] == "C1234-PROV"
        assert output["validation"] == {"is_valid": True}


def _make_fake_module(return_value=None, side_effect=None):
    """Create a fake module with a handle function."""
    mod = types.ModuleType("fake_step")
    mod.handle = MagicMock(return_value=return_value or {}, side_effect=side_effect)
    return mod


class TestHandler:
    """Tests for the enrichment handler routing."""

    def test_routes_to_correct_action(self):
        """Should dynamically import the action module and call handle()."""
        fake_mod = _make_fake_module(return_value={"concept_id": "C1234-PROV"})

        with (
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse"),
        ):
            mock_importlib.import_module.return_value = fake_mod
            result = handler(
                {"action": "fetch", "payload": {"concept_id": "C1234-PROV"}},
                None,
            )

        mock_importlib.import_module.assert_called_once_with("lambdas.enrichment.fetch")
        fake_mod.handle.assert_called_once_with({"concept_id": "C1234-PROV"}, None)
        assert result["concept_id"] == "C1234-PROV"

    def test_raises_on_unknown_action(self):
        """Should raise ValueError for unknown action (module not found)."""
        with (
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse") as mock_flush,
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            pytest.raises(ValueError, match="Unknown action"),
        ):
            mock_importlib.import_module.side_effect = ModuleNotFoundError("No module")
            handler({"action": "nonexistent", "payload": {}}, None)

        mock_flush.assert_called_once()

    def test_raises_on_missing_action(self):
        """Should raise ValueError when action key is missing."""
        with (
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse") as mock_flush,
            pytest.raises(ValueError, match="Missing 'action'"),
        ):
            handler({"payload": {}}, None)

        mock_flush.assert_called_once()

    def test_raises_on_missing_payload(self):
        """Should raise ValueError and flush langfuse when payload is missing."""
        with (
            patch("lambdas.enrichment.handler.flush_langfuse") as mock_flush,
            pytest.raises(ValueError, match="Missing 'payload'"),
        ):
            handler({"action": "fetch"}, None)

        mock_flush.assert_called_once()

    def test_propagates_sub_handler_exception(self):
        """Should propagate exceptions from sub-handlers."""
        fake_mod = _make_fake_module(side_effect=RuntimeError("boom"))

        with (
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse"),
            pytest.raises(RuntimeError, match="boom"),
        ):
            mock_importlib.import_module.return_value = fake_mod
            handler({"action": "fetch", "payload": {}}, None)

    def test_flushes_langfuse_on_success(self):
        """Should flush langfuse after successful handler execution."""
        fake_mod = _make_fake_module()

        with (
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse") as mock_flush,
        ):
            mock_importlib.import_module.return_value = fake_mod
            handler({"action": "fetch", "payload": {}}, None)

        mock_flush.assert_called_once()

    def test_flushes_langfuse_on_failure(self):
        """Should flush langfuse even when handler raises an exception."""
        fake_mod = _make_fake_module(side_effect=RuntimeError("boom"))

        with (
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            patch("lambdas.enrichment.handler.trace_update"),
            patch("lambdas.enrichment.handler.flush_langfuse") as mock_flush,
            pytest.raises(RuntimeError),
        ):
            mock_importlib.import_module.return_value = fake_mod
            handler({"action": "fetch", "payload": {}}, None)

        mock_flush.assert_called_once()

    def test_sets_session_id_from_concept_id(self):
        """Should set Langfuse session_id to enrich-{concept_id}."""
        fake_mod = _make_fake_module()

        with (
            patch("lambdas.enrichment.handler.importlib") as mock_importlib,
            patch("lambdas.enrichment.handler.trace_update") as mock_trace_update,
            patch("lambdas.enrichment.handler.flush_langfuse"),
        ):
            mock_importlib.import_module.return_value = fake_mod
            handler({"action": "fetch", "payload": {"concept_id": "C1234-PROV"}}, None)

        mock_trace_update.assert_called_once_with(
            session_id="enrich-C1234-PROV",
            metadata={"concept_id": "C1234-PROV", "action": "fetch"},
        )
