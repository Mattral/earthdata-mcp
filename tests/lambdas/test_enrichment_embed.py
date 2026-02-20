"""Tests for the enrichment embed step."""

from unittest.mock import MagicMock, patch


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event():
    """Build a minimal enrichment event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": {"EntryTitle": "Raw"},
        "enriched_metadata": {"EntryTitle": "Enriched"},
    }


class TestEmbedStep:
    """Tests for the embed Lambda step."""

    @patch("lambdas.enrichment.embed.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.embed.prepare_event")
    @patch("lambdas.enrichment.embed.get_datastore")
    @patch("lambdas.enrichment.embed.get_embedding_generator")
    @patch("lambdas.enrichment.embed.extract_data")
    @patch("lambdas.enrichment.embed.fetch_associations", return_value={})
    @patch("lambdas.enrichment.embed.lookup_terms", return_value={})
    def test_successful_embedding(
        self,
        _mock_lookup,
        _mock_assoc,
        mock_extract,
        mock_get_embedder,
        mock_get_ds,
        mock_prepare,
        _mock_dehydrate,
    ):
        """Should embed chunks and store them in the datastore."""
        from lambdas.enrichment.embed import embed
        from models.cmr import EmbeddingChunk, ExtractionResult

        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        mock_ds = MagicMock()
        mock_ds.get_chunks_for_entity.return_value = {}
        mock_get_ds.return_value = mock_ds

        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024
        mock_get_embedder.return_value = mock_embedder

        mock_extract.return_value = ExtractionResult(
            chunks=[
                EmbeddingChunk(
                    concept_type="collection",
                    concept_id="C1234-PROV",
                    attribute="title",
                    text_content="Enriched",
                ),
            ],
            kms_terms=[],
        )

        result = embed(event, None)

        assert result["embed_result"]["success"] is True
        assert result["embed_result"]["chunks_stored"] == 1
        mock_ds.upsert_chunks.assert_called_once()

    @patch("lambdas.enrichment.embed.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.embed.prepare_event")
    @patch("lambdas.enrichment.embed.get_datastore")
    @patch("lambdas.enrichment.embed.get_embedding_generator")
    @patch("lambdas.enrichment.embed.extract_data", side_effect=Exception("Extract failed"))
    def test_returns_failure_on_error(
        self, _mock_extract, mock_get_embedder, mock_get_ds, mock_prepare, _mock_dehydrate
    ):
        """Should return failure result when an exception occurs during embedding."""
        from lambdas.enrichment.embed import embed

        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        mock_ds = MagicMock()
        mock_ds.get_chunks_for_entity.return_value = {}
        mock_get_ds.return_value = mock_ds
        mock_get_embedder.return_value = MagicMock()

        result = embed(event, None)

        assert result["embed_result"]["success"] is False
        assert "Extract failed" in result["embed_result"]["error"]
