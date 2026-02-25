"""Detailed tests for the enrichment embed module — embed_chunks, process_kms_terms."""

from unittest.mock import MagicMock, patch

from lambdas.enrichment.embed import embed, embed_chunks, process_kms_terms
from models.cmr import EmbeddingChunk, ExtractionResult, KMSTerm
from util.embeddings import EmbeddingError


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(concept_type="collection"):
    """Build a minimal enrichment event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": concept_type,
        "metadata": {"EntryTitle": "Raw"},
        "enriched_metadata": {"EntryTitle": "Enriched"},
    }


def _make_chunk(attribute="title", text="Some text", concept_id="C1234-PROV"):
    """Build a test EmbeddingChunk."""
    return EmbeddingChunk(
        concept_type="collection",
        concept_id=concept_id,
        attribute=attribute,
        text_content=text,
    )


class TestEmbedChunks:
    """Tests for embed_chunks."""

    def test_generates_new_embeddings(self):
        """Should generate embeddings for all chunks when no existing data."""
        embedder = MagicMock()
        embedder.generate.return_value = [0.1] * 1024

        chunks = [_make_chunk("title", "Title text"), _make_chunk("abstract", "Abstract text")]
        result = embed_chunks(chunks, embedder)

        assert len(result) == 2
        assert result[0] == ("title", "Title text", [0.1] * 1024)
        assert embedder.generate.call_count == 2

    def test_reuses_existing_embedding_for_unchanged_text(self):
        """Should reuse existing embedding when text content has not changed."""
        embedder = MagicMock()
        embedder.generate.return_value = [0.2] * 1024

        existing = {"title": ("Title text", [0.9] * 1024)}
        chunks = [_make_chunk("title", "Title text")]
        result = embed_chunks(chunks, embedder, existing_chunks=existing)

        assert len(result) == 1
        assert result[0] == ("title", "Title text", [0.9] * 1024)
        embedder.generate.assert_not_called()

    def test_regenerates_embedding_for_changed_text(self):
        """Should regenerate embedding when text content has changed."""
        embedder = MagicMock()
        embedder.generate.return_value = [0.3] * 1024

        existing = {"title": ("Old title", [0.9] * 1024)}
        chunks = [_make_chunk("title", "New title")]
        result = embed_chunks(chunks, embedder, existing_chunks=existing)

        assert len(result) == 1
        assert result[0] == ("title", "New title", [0.3] * 1024)
        embedder.generate.assert_called_once()

    def test_mixed_reuse_and_new(self):
        """Should reuse unchanged embeddings and generate new ones in the same batch."""
        embedder = MagicMock()
        embedder.generate.return_value = [0.4] * 1024

        existing = {"title": ("Same title", [0.9] * 1024)}
        chunks = [
            _make_chunk("title", "Same title"),
            _make_chunk("abstract", "New abstract"),
        ]
        result = embed_chunks(chunks, embedder, existing_chunks=existing)

        assert len(result) == 2
        # Title reused
        assert result[0][2] == [0.9] * 1024
        # Abstract newly generated
        assert result[1][2] == [0.4] * 1024
        assert embedder.generate.call_count == 1

    def test_empty_chunks(self):
        """Should return empty list for empty chunks input."""
        result = embed_chunks([], MagicMock())
        assert not result


class TestProcessKmsTerms:
    """Tests for process_kms_terms."""

    def test_returns_refs_for_existing_embeddings(self):
        """Should return refs without re-embedding when KMS embedding already exists."""
        ds = MagicMock()
        ds.get_kms_embedding.return_value = True
        embedder = MagicMock()

        kms_terms = [KMSTerm(term="OCEAN", scheme="sciencekeywords")]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {
                ("OCEAN", "sciencekeywords"): MagicMock(
                    uuid="uuid-1", term="OCEAN", scheme="sciencekeywords", definition="The ocean"
                )
            }
            refs = process_kms_terms(kms_terms, ds, embedder)

        assert refs == [("uuid-1", "sciencekeywords")]
        embedder.generate.assert_not_called()

    def test_embeds_new_terms(self):
        """Should embed and store new KMS terms that are not yet in the datastore."""
        ds = MagicMock()
        ds.get_kms_embedding.return_value = None
        ds.upsert_kms_embedding.return_value = True
        embedder = MagicMock()
        embedder.generate.return_value = [0.1] * 1024

        kms_terms = [KMSTerm(term="MODIS", scheme="instruments")]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            kms_result = MagicMock(
                uuid="uuid-2", term="MODIS", scheme="instruments", definition="An instrument"
            )
            mock_lookup.return_value = {("MODIS", "instruments"): kms_result}
            refs = process_kms_terms(kms_terms, ds, embedder)

        assert refs == [("uuid-2", "instruments")]
        embedder.generate.assert_called_once()
        ds.upsert_kms_embedding.assert_called_once()

    def test_handles_embedding_failure(self):
        """Should return empty refs when embedding generation fails."""
        ds = MagicMock()
        ds.get_kms_embedding.return_value = None
        embedder = MagicMock()
        embedder.generate.side_effect = EmbeddingError("fail")

        kms_terms = [KMSTerm(term="BAD", scheme="sciencekeywords")]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {
                ("BAD", "sciencekeywords"): MagicMock(
                    uuid="uuid-3", term="BAD", scheme="sciencekeywords", definition=None
                )
            }
            refs = process_kms_terms(kms_terms, ds, embedder)

        assert not refs

    def test_deduplicates_terms(self):
        """Should deduplicate KMS terms before lookup."""
        ds = MagicMock()
        ds.get_kms_embedding.return_value = True
        embedder = MagicMock()

        kms_terms = [
            KMSTerm(term="OCEAN", scheme="sciencekeywords"),
            KMSTerm(term="OCEAN", scheme="sciencekeywords"),
        ]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {
                ("OCEAN", "sciencekeywords"): MagicMock(
                    uuid="uuid-1", term="OCEAN", scheme="sciencekeywords", definition=""
                )
            }
            process_kms_terms(kms_terms, ds, embedder)

        # lookup_terms called with deduplicated list
        call_args = mock_lookup.call_args[0][0]
        assert len(call_args) == 1

    def test_skips_terms_not_found_in_lookup(self):
        """Should return empty refs when lookup returns None for a term."""
        ds = MagicMock()
        embedder = MagicMock()

        kms_terms = [KMSTerm(term="UNKNOWN", scheme="sciencekeywords")]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {("UNKNOWN", "sciencekeywords"): None}
            refs = process_kms_terms(kms_terms, ds, embedder)

        assert not refs

    def test_embeds_term_without_definition(self):
        """Should embed using only the term name when definition is None."""
        ds = MagicMock()
        ds.get_kms_embedding.return_value = None
        ds.upsert_kms_embedding.return_value = True
        embedder = MagicMock()
        embedder.generate.return_value = [0.1] * 1024

        kms_terms = [KMSTerm(term="MODIS", scheme="instruments")]
        with patch("lambdas.enrichment.embed.lookup_terms") as mock_lookup:
            kms_result = MagicMock(
                uuid="uuid-2", term="MODIS", scheme="instruments", definition=None
            )
            mock_lookup.return_value = {("MODIS", "instruments"): kms_result}
            process_kms_terms(kms_terms, ds, embedder)

        # When definition is None, only the term is passed as text
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "MODIS"


class TestEmbedHandler:
    """Additional tests for the embed() handler."""

    @patch("lambdas.enrichment.embed.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.embed.prepare_event")
    @patch("lambdas.enrichment.embed.get_datastore")
    @patch("lambdas.enrichment.embed.get_embedding_generator")
    @patch("lambdas.enrichment.embed.extract_data")
    @patch("lambdas.enrichment.embed.fetch_associations", return_value={})
    @patch("lambdas.enrichment.embed.lookup_terms", return_value={})
    def test_skips_fetch_associations_for_non_collection(
        self, _lookup, mock_assoc, mock_extract, mock_embedder, mock_ds, mock_prepare, _dehydrate
    ):
        """Should not call fetch_associations for non-collection concept types."""
        event = _make_event(concept_type="variable")
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        ds = MagicMock()
        ds.get_chunks_for_entity.return_value = {}
        mock_ds.return_value = ds

        embedder_inst = MagicMock()
        embedder_inst.generate.return_value = [0.1] * 1024
        mock_embedder.return_value = embedder_inst

        mock_extract.return_value = ExtractionResult(chunks=[], kms_terms=[])

        result = embed(event, None)

        assert result["embed_result"]["success"] is True
        mock_assoc.assert_not_called()

    @patch("lambdas.enrichment.embed.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.embed.prepare_event")
    @patch("lambdas.enrichment.embed.get_datastore")
    @patch("lambdas.enrichment.embed.get_embedding_generator")
    @patch("lambdas.enrichment.embed.extract_data")
    @patch("lambdas.enrichment.embed.fetch_associations", return_value={})
    @patch("lambdas.enrichment.embed.lookup_terms", return_value={})
    def test_calls_upsert_associations_for_collection(
        self, _lookup, mock_assoc, mock_extract, mock_embedder, mock_ds, mock_prepare, _dehydrate
    ):
        """Should fetch and upsert associations for collection concept type."""
        event = _make_event(concept_type="collection")
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        ds = MagicMock()
        ds.get_chunks_for_entity.return_value = {}
        mock_ds.return_value = ds

        mock_embedder.return_value = MagicMock()
        mock_extract.return_value = ExtractionResult(chunks=[], kms_terms=[])

        embed(event, None)

        mock_assoc.assert_called_once_with("C1234-PROV")
        ds.upsert_associations.assert_called_once()

    @patch("lambdas.enrichment.embed.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.embed.prepare_event")
    @patch("lambdas.enrichment.embed.get_datastore")
    @patch("lambdas.enrichment.embed.get_embedding_generator")
    @patch("lambdas.enrichment.embed.extract_data")
    @patch("lambdas.enrichment.embed.fetch_associations", return_value={})
    @patch("lambdas.enrichment.embed.lookup_terms", return_value={})
    def test_passes_existing_chunks_for_diff_upsert(
        self, _lookup, _assoc, mock_extract, mock_embedder, mock_ds, mock_prepare, _dehydrate
    ):
        """Should pass existing chunks to embed_chunks for diff-based upsert."""
        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["enriched_metadata"])

        existing = {"title": ("Old text", [0.9] * 1024)}
        ds = MagicMock()
        ds.get_chunks_for_entity.return_value = existing
        mock_ds.return_value = ds

        embedder_inst = MagicMock()
        embedder_inst.generate.return_value = [0.1] * 1024
        mock_embedder.return_value = embedder_inst

        mock_extract.return_value = ExtractionResult(
            chunks=[_make_chunk("title", "Old text")],
            kms_terms=[],
        )

        result = embed(event, None)

        assert result["embed_result"]["success"] is True
        # embed_chunks should have received existing_chunks, so embedder.generate not called
        embedder_inst.generate.assert_not_called()
