# pylint: disable=too-many-lines
"""Tests for the embedding lambda handler."""

import json
from unittest.mock import MagicMock, patch

import pytest
import responses
from pydantic import ValidationError

from lambdas.embedding.handler import (
    handle_delete,
    handle_update,
    handler,
    process_kms_terms,
    process_message,
)
from tests.conftest import GLOBAL_BOUNDING_BOX
from util.cmr import CMRError, extract_data, fetch_concept
from util.cmr.extraction import (
    extract_from_citation,
    extract_from_collection,
    extract_from_variable,
)
from util.datastores.postgres import PostgresEmbeddingDatastore
from util.embeddings import BedrockEmbeddingGenerator
from util.models import CollectionData, ConceptMessage, KMSTerm


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv("CMR_URL", "https://cmr.earthdata.nasa.gov")
    monkeypatch.setenv("DATABASE_SECRET_ID", "test-secret")
    monkeypatch.setenv("EMBEDDINGS_TABLE", "concept_embeddings")


# Test text that meets the 200 character minimum for embedding
LONG_TITLE = (
    "Global Sea Surface Temperature Analysis from Multiple Satellite Sensors "
    "Providing Comprehensive Coverage of Ocean Temperature Patterns and Variability "
    "Across All Major Ocean Basins for Climate Research Applications"
)
LONG_ABSTRACT = (
    "This dataset provides comprehensive measurements of sea surface temperature "
    "collected from multiple satellite-based sensors. The data enables researchers "
    "to study ocean temperature patterns, climate variability, and long-term trends "
    "in global ocean heat content with high spatial and temporal resolution."
)
LONG_DEFINITION = (
    "The temperature of the ocean surface water measured by satellite-based infrared "
    "and microwave sensors. This variable represents the skin temperature of the ocean "
    "within the top few millimeters and is used for climate monitoring and weather prediction."
)


class TestExtractCollectionData:
    """Tests for extract_from_collection function."""

    def test_extracts_title(self):
        """Test that title is extracted as a chunk when >= 200 chars."""
        collection = {"EntryTitle": LONG_TITLE}

        result = extract_from_collection("C1234-PROV", collection)

        assert len(result.chunks) == 1
        assert result.chunks[0].attribute == "title"

    def test_extracts_abstract(self):
        """Test that abstract is extracted as a chunk when >= 200 chars."""
        collection = {"Abstract": LONG_ABSTRACT}

        result = extract_from_collection("C1234-PROV", collection)

        assert len(result.chunks) == 1
        assert result.chunks[0].attribute == "abstract"

    def test_skips_short_text(self):
        """Test that short text is skipped."""
        collection = {"EntryTitle": "Short Title", "Abstract": "Short abstract."}

        result = extract_from_collection("C1234-PROV", collection)

        assert len(result.chunks) == 0

    def test_extracts_multiple_attributes(self):
        """Test that multiple attributes are extracted when both are long enough."""
        collection = {
            "EntryTitle": LONG_TITLE,
            "Abstract": LONG_ABSTRACT,
        }

        result = extract_from_collection("C1234-PROV", collection)

        assert len(result.chunks) == 2
        attributes = {c.attribute for c in result.chunks}
        assert attributes == {"title", "abstract"}

    def test_extracts_science_keywords_as_kms_terms(self):
        """Test that science keywords are extracted as KMS term references."""

        collection = {
            "ScienceKeywords": [
                {
                    "Category": "EARTH SCIENCE",
                    "Topic": "ATMOSPHERE",
                    "Term": "PRECIPITATION",
                }
            ]
        }

        result = extract_from_collection("C1234-PROV", collection)

        # Science keywords go to kms_terms, not chunks
        assert len(result.chunks) == 0
        assert len(result.kms_terms) == 1
        assert result.kms_terms[0].term == "PRECIPITATION"
        assert result.kms_terms[0].scheme == "sciencekeywords"

    def test_extracts_platforms_and_instruments_as_kms_terms(self):
        """Test that platforms and instruments are extracted as KMS term references."""

        collection = {
            "Platforms": [
                {
                    "ShortName": "TERRA",
                    "Instruments": [{"ShortName": "MODIS"}, {"ShortName": "ASTER"}],
                }
            ]
        }

        result = extract_from_collection("C1234-PROV", collection)

        # Platforms and instruments go to kms_terms
        assert len(result.chunks) == 0
        assert len(result.kms_terms) == 3

        terms = {(t.term, t.scheme) for t in result.kms_terms}
        assert ("TERRA", "platforms") in terms
        assert ("MODIS", "instruments") in terms
        assert ("ASTER", "instruments") in terms

    def test_empty_collection_returns_empty(self):
        """Test that empty collection returns empty result."""

        result = extract_from_collection("C1234-PROV", {})

        assert len(result.chunks) == 0
        assert len(result.kms_terms) == 0


class TestExtractVariableData:
    """Tests for extract_from_variable function."""

    def test_extracts_variable_definition(self):
        """Test that variable definition is extracted when >= 200 chars."""
        variable = {"Definition": LONG_DEFINITION}

        result = extract_from_variable("V1234-PROV", variable)

        assert len(result.chunks) == 1
        assert result.chunks[0].attribute == "definition"

    def test_skips_short_definition(self):
        """Test that short definition is skipped."""
        variable = {"Definition": "Temperature of the sea surface"}

        result = extract_from_variable("V1234-PROV", variable)

        assert len(result.chunks) == 0


class TestExtractCitationData:
    """Tests for extract_from_citation function."""

    def test_extracts_citation_attributes(self):
        """Test that citation title and abstract are extracted when >= 200 chars."""
        citation = {
            "Name": LONG_TITLE,
            "Abstract": LONG_ABSTRACT,
        }

        result = extract_from_citation("CIT1234-PROV", citation)

        assert len(result.chunks) == 2
        attributes = {c.attribute for c in result.chunks}
        assert attributes == {"title", "abstract"}

    def test_skips_short_citation_text(self):
        """Test that short citation text is skipped."""
        citation = {
            "Name": "Short Paper Title",
            "Abstract": "Brief abstract.",
        }

        result = extract_from_citation("CIT1234-PROV", citation)

        assert len(result.chunks) == 0


class TestExtractData:
    """Tests for extract_data routing function."""

    def test_dispatches_to_collection_extractor(self):
        """Test that collection type routes correctly."""
        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )
        collection = {"EntryTitle": LONG_TITLE}
        result = extract_data(message, collection)

        assert len(result.chunks) == 1
        assert result.chunks[0].concept_type == "collection"

    def test_dispatches_to_variable_extractor(self):
        """Test that variable type routes correctly."""
        message = ConceptMessage(
            action="concept-update",
            concept_type="variable",
            concept_id="V1234-PROV",
            revision_id="1",
        )
        variable = {"Definition": LONG_DEFINITION}
        result = extract_data(message, variable)

        assert len(result.chunks) == 1
        assert result.chunks[0].concept_type == "variable"
        assert result.chunks[0].attribute == "definition"

    def test_invalid_concept_type_raises_validation_error(self):
        """Test that invalid concept type raises ValidationError due to ConceptType enum."""
        with pytest.raises(ValidationError):
            ConceptMessage(
                action="concept-update",
                concept_type="unknown",
                concept_id="X1234-PROV",
                revision_id="1",
            )

    def test_citation_empty_metadata_returns_empty(self):
        """Test that citation with empty metadata returns empty result."""
        message = ConceptMessage(
            action="concept-update",
            concept_type="citation",
            concept_id="CIT1234-PROV",
            revision_id="1",
        )
        result = extract_data(message, {})

        assert len(result.chunks) == 0
        assert len(result.kms_terms) == 0


class TestFetchConcept:
    """Tests for fetch_concept function."""

    @responses.activate
    def test_fetches_collection(self):
        """Test fetching a collection from CMR."""

        responses.add(
            responses.GET,
            "https://cmr.earthdata.nasa.gov/search/concepts/C1234-PROV/1.umm_json",
            json={"EntryTitle": "Test Collection"},
            status=200,
        )

        result = fetch_concept("C1234-PROV", "1")

        assert result["EntryTitle"] == "Test Collection"

    @responses.activate
    def test_raises_on_http_error(self):
        """Test that HTTP errors raise CMRError."""

        responses.add(
            responses.GET,
            "https://cmr.earthdata.nasa.gov/search/concepts/C1234-PROV/1.umm_json",
            status=404,
        )

        with pytest.raises(CMRError):
            fetch_concept("C1234-PROV", "1")


class TestBedrockEmbeddingGenerator:
    """Tests for BedrockEmbeddingGenerator."""

    def test_generates_embedding(self):
        """Test that embeddings are generated via Bedrock."""

        mock_response = {
            "embedding": [0.1] * 1024,
            "inputTextTokenCount": 10,
        }
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "body": MagicMock(read=lambda: json.dumps(mock_response).encode())
        }

        generator = BedrockEmbeddingGenerator(client=mock_client)
        embedding = generator.generate("test text")

        assert len(embedding) == 1024

    def test_passes_concept_type_and_attribute_to_span(self):
        """Test that concept_type and attribute are passed to span for embedding tracking."""

        mock_response = {"embedding": [0.1] * 1024, "inputTextTokenCount": 10}
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "body": MagicMock(read=lambda: json.dumps(mock_response).encode())
        }

        mock_span = MagicMock()
        generator = BedrockEmbeddingGenerator(client=mock_client)
        generator.generate(
            "test text",
            concept_type="collection",
            attribute="abstract",
            span=mock_span,
        )

        # Span start_observation should have been called with as_type="embedding"
        mock_span.start_observation.assert_called_once()
        call_kwargs = mock_span.start_observation.call_args.kwargs
        assert call_kwargs["as_type"] == "embedding"
        assert call_kwargs["name"] == "embed-abstract"


class TestPostgresDatastore:
    """Tests for PostgresEmbeddingDatastore."""

    def test_upsert_chunks(self):
        """Test upserting embedding chunks."""

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
        mock_conn.transaction.return_value.__enter__ = MagicMock()
        mock_conn.transaction.return_value.__exit__ = MagicMock(return_value=None)

        with patch("util.datastores.postgres.get_db_connection", return_value=mock_conn):
            datastore = PostgresEmbeddingDatastore()
            chunks = [
                ("title", "Test Title", [0.1] * 1024),
                ("abstract", "Test Abstract", [0.2] * 1024),
            ]

            count = datastore.upsert_chunks("collection", "C1234-PROV", chunks)

            assert count == 2
            mock_conn.transaction.assert_called_once()

    def test_upsert_associations(self):
        """Test upserting concept associations."""

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1  # Each insert affects 1 row
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
        mock_conn.transaction.return_value.__enter__ = MagicMock()
        mock_conn.transaction.return_value.__exit__ = MagicMock(return_value=None)

        with patch("util.datastores.postgres.get_db_connection", return_value=mock_conn):
            datastore = PostgresEmbeddingDatastore()
            associations = {"variables": ["V1234-PROV", "V5678-PROV"]}

            count = datastore.upsert_associations("collection", "C1234-PROV", associations)

            assert count == 2
            mock_conn.transaction.assert_called_once()

    def test_empty_associations_returns_zero(self):
        """Test that empty associations returns 0."""

        with patch("util.datastores.postgres.get_db_connection"):
            datastore = PostgresEmbeddingDatastore()
            count = datastore.upsert_associations("collection", "C1234-PROV", {})

            assert count == 0


class TestHandleUpdate:
    """Tests for handle_update function."""

    def test_processes_collection_update(self):
        """Test processing a collection update message."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {
                "EntryTitle": "Test Collection",
                "Abstract": "Test abstract",
            }
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {"variables": ["V1234-PROV"]}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_langfuse.return_value = None

                    handle_update(message, mock_repo, mock_embedder)

        # Should have upserted chunks
        mock_repo.upsert_chunks.assert_called_once()
        # Should have upserted associations
        mock_repo.upsert_associations.assert_called_once()
        # Should have upserted collection metadata
        mock_repo.upsert_collection.assert_called_once()

    def test_upserts_collection_metadata(self):
        """Test that collection metadata is upserted with enriched data."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        collection_metadata = {
            "EntryTitle": "MODIS Daily 1km SST",
            "TemporalExtents": [
                {
                    "RangeDateTimes": [
                        {
                            "BeginningDateTime": "2000-02-24T00:00:00Z",
                            "EndingDateTime": "2020-12-31T23:59:59Z",
                        }
                    ]
                }
            ],
            "SpatialExtent": {
                "HorizontalSpatialDomain": {
                    "Geometry": {"BoundingRectangles": [GLOBAL_BOUNDING_BOX]}
                }
            },
        }

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = collection_metadata
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_langfuse.return_value = None

                    handle_update(message, mock_repo, mock_embedder)

        # Verify upsert_collection was called with correct arguments
        mock_repo.upsert_collection.assert_called_once()
        call_args = mock_repo.upsert_collection.call_args[0]

        assert call_args[0] == "C1234-PROV"  # concept_id
        collection_data = call_args[1]
        assert isinstance(collection_data, CollectionData)
        assert collection_data.metadata == collection_metadata
        assert collection_data.temporal_start is not None
        assert collection_data.temporal_end is not None
        assert collection_data.spatial_wkt is not None
        assert collection_data.is_global is True  # Full globe bounding box

    def test_does_not_upsert_collection_for_variables(self):
        """Test that collection metadata is not upserted for variable type."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="variable",
            concept_id="V1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"Definition": LONG_DEFINITION}
            with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                mock_langfuse.return_value = None

                handle_update(message, mock_repo, mock_embedder)

        # Should NOT have upserted collection metadata
        mock_repo.upsert_collection.assert_not_called()

    def test_embedder_called_for_each_chunk(self):
        """Test that embedder.generate is called for each extracted chunk."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {
                "EntryTitle": LONG_TITLE,
                "Abstract": LONG_ABSTRACT,
            }
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_langfuse.return_value = None

                    handle_update(message, mock_repo, mock_embedder)

        # Should have called generate 2 times (title, abstract)
        assert mock_embedder.generate.call_count == 2

        # Verify each call had the correct text
        call_texts = [call[0][0] for call in mock_embedder.generate.call_args_list]
        assert LONG_TITLE in call_texts
        assert LONG_ABSTRACT in call_texts

    def test_embedder_called_with_concept_type_and_attribute(self):
        """Test that embedder receives concept_type and attribute for routing."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"EntryTitle": LONG_TITLE}
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_langfuse.return_value = None

                    handle_update(message, mock_repo, mock_embedder)

        # Verify embedder was called with concept_type and attribute
        call_kwargs = mock_embedder.generate.call_args
        assert call_kwargs.kwargs.get("concept_type") == "collection"
        assert call_kwargs.kwargs.get("attribute") == "title"


class TestProcessKMSTerms:
    """Tests for process_kms_terms function."""

    def test_looks_up_kms_terms(self):
        """Test that KMS batch lookup is called for extracted terms."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        kms_terms = [
            KMSTerm(term="MODIS", scheme="instruments"),
            KMSTerm(term="TERRA", scheme="platforms"),
        ]

        mock_lookup_results = {
            ("MODIS", "instruments"): KMSTerm(
                uuid="modis-uuid",
                scheme="instruments",
                term="MODIS",
                definition="Imaging Spectroradiometer",
            ),
            ("TERRA", "platforms"): KMSTerm(
                uuid="terra-uuid",
                scheme="platforms",
                term="TERRA",
                definition="Satellite",
            ),
        }

        with patch("lambdas.embedding.handler.lookup_terms") as mock_lookup:
            mock_lookup.return_value = mock_lookup_results

            process_kms_terms(kms_terms, mock_repo, mock_embedder)

        # Should have called lookup_terms once with all terms
        mock_lookup.assert_called_once()
        call_args = mock_lookup.call_args[0][0]
        assert ("MODIS", "instruments") in call_args
        assert ("TERRA", "platforms") in call_args

    def test_embeds_new_kms_terms(self):
        """Test that new KMS terms are embedded and stored."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None  # Not in database yet
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.5] * 1024

        kms_terms = [KMSTerm(term="MODIS", scheme="instruments")]

        mock_kms_term = KMSTerm(
            uuid="modis-uuid",
            scheme="instruments",
            term="MODIS",
            definition="Moderate Resolution Imaging Spectroradiometer",
        )

        with patch("lambdas.embedding.handler.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {("MODIS", "instruments"): mock_kms_term}

            kms_refs = process_kms_terms(kms_terms, mock_repo, mock_embedder)

        # Should have generated embedding for the term + definition
        mock_embedder.generate.assert_called_once()
        call_text = mock_embedder.generate.call_args[0][0]
        assert "MODIS" in call_text
        assert "Moderate Resolution Imaging Spectroradiometer" in call_text

        # Should have stored the embedding
        mock_repo.upsert_kms_embedding.assert_called_once()

        # Should return the (uuid, scheme) tuple
        assert kms_refs == [("modis-uuid", "instruments")]

    def test_skips_existing_kms_embeddings(self):
        """Test that existing KMS embeddings are not re-generated."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = {"embedding": [0.1] * 1024}  # Already exists
        mock_embedder = MagicMock()

        kms_terms = [KMSTerm(term="MODIS", scheme="instruments")]

        mock_kms_term = KMSTerm(
            uuid="modis-uuid",
            scheme="instruments",
            term="MODIS",
            definition="Imaging Spectroradiometer",
        )

        with patch("lambdas.embedding.handler.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {("MODIS", "instruments"): mock_kms_term}

            kms_refs = process_kms_terms(kms_terms, mock_repo, mock_embedder)

        # Should NOT have generated embedding (already exists)
        mock_embedder.generate.assert_not_called()
        mock_repo.upsert_kms_embedding.assert_not_called()

        # Should still return the (uuid, scheme) tuple for association
        assert kms_refs == [("modis-uuid", "instruments")]

    def test_deduplicates_kms_terms(self):
        """Test that duplicate terms are only processed once."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        # Same term twice
        kms_terms = [
            KMSTerm(term="MODIS", scheme="instruments"),
            KMSTerm(term="MODIS", scheme="instruments"),
        ]

        mock_kms_term = KMSTerm(
            uuid="modis-uuid",
            scheme="instruments",
            term="MODIS",
            definition="Definition",
        )

        with patch("lambdas.embedding.handler.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {("MODIS", "instruments"): mock_kms_term}

            process_kms_terms(kms_terms, mock_repo, mock_embedder)

        # Should only pass one term to lookup_terms (deduped)
        mock_lookup.assert_called_once()
        call_args = mock_lookup.call_args[0][0]
        assert len(call_args) == 1


class TestFullEmbeddingFlow:
    """Integration tests for the full embedding generation flow."""

    def test_collection_with_kms_terms_full_flow(self):
        """Test full flow: collection metadata -> extraction -> embedding -> storage."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        # Realistic collection metadata with text >= 200 chars
        collection_metadata = {
            "EntryTitle": LONG_TITLE,
            "Abstract": LONG_ABSTRACT,
            "ScienceKeywords": [
                {
                    "Category": "EARTH SCIENCE",
                    "Topic": "OCEANS",
                    "Term": "OCEAN TEMPERATURE",
                    "VariableLevel1": "SEA SURFACE TEMPERATURE",
                }
            ],
            "Platforms": [
                {
                    "ShortName": "TERRA",
                    "Instruments": [{"ShortName": "MODIS"}],
                }
            ],
        }

        mock_kms_terms = {
            ("SEA SURFACE TEMPERATURE", "sciencekeywords"): KMSTerm(
                uuid="sst-uuid",
                scheme="sciencekeywords",
                term="SEA SURFACE TEMPERATURE",
                definition="Ocean temp",
            ),
            ("TERRA", "platforms"): KMSTerm(
                uuid="terra-uuid",
                scheme="platforms",
                term="TERRA",
                definition="Satellite",
            ),
            ("MODIS", "instruments"): KMSTerm(
                uuid="modis-uuid",
                scheme="instruments",
                term="MODIS",
                definition="Imager",
            ),
        }

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = collection_metadata
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.lookup_terms") as mock_lookup:
                    mock_lookup.return_value = mock_kms_terms
                    with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                        mock_langfuse.return_value = None

                        handle_update(message, mock_repo, mock_embedder)

        # Verify concept chunks were embedded (title + abstract = 2)
        chunk_embed_calls = [
            c
            for c in mock_embedder.generate.call_args_list
            if c.kwargs.get("concept_type") == "collection"
        ]
        assert len(chunk_embed_calls) == 2

        # Verify KMS batch lookup was called once
        mock_lookup.assert_called_once()

        # Verify KMS embeddings were generated (3 new terms)
        kms_embed_calls = [
            c
            for c in mock_embedder.generate.call_args_list
            if c.kwargs.get("concept_type") == "kms_term"
        ]
        assert len(kms_embed_calls) == 3

        # Verify concept chunks were stored
        mock_repo.upsert_chunks.assert_called_once()
        call_args = mock_repo.upsert_chunks.call_args
        assert call_args[0][0] == "collection"
        assert call_args[0][1] == "C1234-PROV"

        # Verify KMS associations were created
        mock_repo.upsert_kms_associations.assert_called_once()
        assoc_call = mock_repo.upsert_kms_associations.call_args
        assert set(assoc_call[0][2]) == {
            ("sst-uuid", "sciencekeywords"),
            ("terra-uuid", "platforms"),
            ("modis-uuid", "instruments"),
        }


class TestHandleDelete:
    """Tests for handle_delete function."""

    def test_deletes_embeddings_and_associations(self):
        """Test that delete removes chunks and associations."""

        mock_repo = MagicMock()
        mock_repo.delete_chunks.return_value = 3
        mock_repo.delete_associations.return_value = 2
        mock_repo.delete_kms_associations.return_value = 5
        mock_repo.delete_collection.return_value = True

        message = ConceptMessage(
            action="concept-delete",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        handle_delete(message, mock_repo)

        mock_repo.delete_chunks.assert_called_once_with("C1234-PROV")
        mock_repo.delete_associations.assert_called_once_with("C1234-PROV")
        mock_repo.delete_kms_associations.assert_called_once_with("C1234-PROV")
        mock_repo.delete_collection.assert_called_once_with("C1234-PROV")

    def test_delete_collection_only_for_collections(self):
        """Test that delete_collection is only called for collection type."""

        mock_repo = MagicMock()
        mock_repo.delete_chunks.return_value = 1
        mock_repo.delete_associations.return_value = 0
        mock_repo.delete_kms_associations.return_value = 0

        message = ConceptMessage(
            action="concept-delete",
            concept_type="variable",
            concept_id="V1234-PROV",
            revision_id="1",
        )

        handle_delete(message, mock_repo)

        mock_repo.delete_chunks.assert_called_once()
        mock_repo.delete_collection.assert_not_called()


class TestHandler:
    """Tests for the Lambda handler function."""

    def test_handler_processes_sqs_event(self):
        """Test that handler processes SQS messages."""

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C1234-PROV",
                            "revision-id": 1,
                        }
                    ),
                }
            ]
        }

        with patch("lambdas.embedding.handler.get_datastore") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.get_kms_embedding.return_value = None
            mock_get_repo.return_value = mock_repo
            with patch("lambdas.embedding.handler.get_embedding_generator") as mock_get_gen:
                mock_embedder = MagicMock()
                mock_embedder.generate.return_value = [0.1] * 1024
                mock_get_gen.return_value = mock_embedder
                with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
                    mock_fetch.return_value = {"EntryTitle": "Test"}
                    with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                        mock_assoc.return_value = {}
                        with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                            mock_langfuse.return_value = None
                            with patch("lambdas.embedding.handler.flush_langfuse"):
                                result = handler(event, None)

        assert not result["batchItemFailures"]

    def test_handler_reports_failures(self):
        """Test that handler reports message failures."""

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C1234-PROV",
                            "revision-id": 1,
                        }
                    ),
                }
            ]
        }

        with patch("lambdas.embedding.handler.get_datastore") as mock_get_repo:
            mock_repo = MagicMock()
            mock_get_repo.return_value = mock_repo
            with patch("lambdas.embedding.handler.get_embedding_generator") as mock_get_gen:
                mock_get_gen.return_value = MagicMock()
                with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
                    mock_fetch.side_effect = CMRError("CMR error")
                    with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                        mock_langfuse.return_value = None
                        with patch("lambdas.embedding.handler.flush_langfuse"):
                            result = handler(event, None)

        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"

    def test_handler_continues_on_partial_failure(self):
        """Test that handler continues processing after a failure."""

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C1234-PROV",
                            "revision-id": 1,
                        }
                    ),
                },
                {
                    "messageId": "msg-2",
                    "body": json.dumps(
                        {
                            "action": "concept-update",
                            "concept-type": "collection",
                            "concept-id": "C5678-PROV",
                            "revision-id": 1,
                        }
                    ),
                },
            ]
        }

        call_count = [0]

        def fetch_side_effect(concept_id, _revision_id):
            call_count[0] += 1
            if concept_id == "C1234-PROV":
                raise CMRError("CMR error")
            return {"EntryTitle": "Test"}

        with patch("lambdas.embedding.handler.get_datastore") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.get_kms_embedding.return_value = None
            mock_get_repo.return_value = mock_repo
            with patch("lambdas.embedding.handler.get_embedding_generator") as mock_get_gen:
                mock_embedder = MagicMock()
                mock_embedder.generate.return_value = [0.1] * 1024
                mock_get_gen.return_value = mock_embedder
                with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
                    mock_fetch.side_effect = fetch_side_effect
                    with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                        mock_assoc.return_value = {}
                        with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                            mock_langfuse.return_value = None
                            with patch("lambdas.embedding.handler.flush_langfuse"):
                                result = handler(event, None)

        # Both messages should have been attempted
        assert call_count[0] == 2
        # Only the first should have failed
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"


class TestLangfuseSessionTracking:
    """Tests for Langfuse session ID tracking from SQS messages."""

    def test_process_message_extracts_langfuse_session_id(self):
        """Test that process_message extracts LangfuseSessionId from message attributes."""

        record = {
            "messageId": "msg-1",
            "body": json.dumps(
                {
                    "action": "concept-update",
                    "concept-type": "collection",
                    "concept-id": "C1234-PROV",
                    "revision-id": 1,
                }
            ),
            "messageAttributes": {
                "LangfuseSessionId": {
                    "stringValue": "bootstrap-abc12345",
                    "dataType": "String",
                }
            },
        }

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"EntryTitle": "Test"}
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_lf_client = MagicMock()
                    mock_span = MagicMock()

                    # Context manager pattern with end_on_exit=False
                    mock_lf_client.start_as_current_span.return_value.__enter__ = MagicMock(
                        return_value=mock_span
                    )
                    mock_lf_client.start_as_current_span.return_value.__exit__ = MagicMock(
                        return_value=None
                    )
                    mock_langfuse.return_value = mock_lf_client

                    process_message(record, mock_repo, mock_embedder)

                    # Verify start_as_current_span was called with trace name
                    mock_lf_client.start_as_current_span.assert_called_once()
                    call_kwargs = mock_lf_client.start_as_current_span.call_args.kwargs
                    assert call_kwargs["name"] == "collection:C1234-PROV"

                    # Verify update_current_trace was called with session_id
                    mock_lf_client.update_current_trace.assert_called_once_with(
                        session_id="bootstrap-abc12345",
                    )

                    # Verify span.update was called with input
                    mock_span.update.assert_called()

    def test_process_message_handles_missing_session_id(self):
        """Test that process_message works when no LangfuseSessionId is present."""

        record = {
            "messageId": "msg-1",
            "body": json.dumps(
                {
                    "action": "concept-update",
                    "concept-type": "collection",
                    "concept-id": "C1234-PROV",
                    "revision-id": 1,
                }
            ),
            # No messageAttributes
        }

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"EntryTitle": "Test"}
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_lf_client = MagicMock()
                    mock_span = MagicMock()

                    mock_lf_client.start_as_current_span.return_value.__enter__ = MagicMock(
                        return_value=mock_span
                    )
                    mock_lf_client.start_as_current_span.return_value.__exit__ = MagicMock(
                        return_value=None
                    )
                    mock_langfuse.return_value = mock_lf_client

                    process_message(record, mock_repo, mock_embedder)

                    # Verify start_as_current_span was called with trace name
                    call_kwargs = mock_lf_client.start_as_current_span.call_args.kwargs
                    assert call_kwargs["name"] == "collection:C1234-PROV"

                    # Verify update_current_trace was called with session_id=None
                    mock_lf_client.update_current_trace.assert_called_once_with(
                        session_id=None,
                    )

                    # Verify span.update was called with input
                    mock_span.update.assert_called()

    def test_handle_update_uses_session_id_for_langfuse(self):
        """Test that handle_update correctly uses session_id with Langfuse."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"EntryTitle": "Test"}
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_lf_client = MagicMock()
                    mock_span = MagicMock()

                    mock_lf_client.start_as_current_span.return_value.__enter__ = MagicMock(
                        return_value=mock_span
                    )
                    mock_lf_client.start_as_current_span.return_value.__exit__ = MagicMock(
                        return_value=None
                    )
                    mock_langfuse.return_value = mock_lf_client

                    handle_update(message, mock_repo, mock_embedder, session_id="bootstrap-xyz789")

                    # Verify start_as_current_span was called with trace name
                    call_kwargs = mock_lf_client.start_as_current_span.call_args.kwargs
                    assert call_kwargs["name"] == "collection:C1234-PROV"

                    # Verify update_current_trace was called with session_id
                    mock_lf_client.update_current_trace.assert_called_once_with(
                        session_id="bootstrap-xyz789",
                    )

                    # Verify span.update was called
                    mock_span.update.assert_called()

    def test_handle_update_names_trace_without_session(self):
        """Test that handle_update names the trace even without session_id."""

        mock_repo = MagicMock()
        mock_repo.get_kms_embedding.return_value = None
        mock_embedder = MagicMock()
        mock_embedder.generate.return_value = [0.1] * 1024

        message = ConceptMessage(
            action="concept-update",
            concept_type="collection",
            concept_id="C1234-PROV",
            revision_id="1",
        )

        with patch("lambdas.embedding.handler.fetch_concept") as mock_fetch:
            mock_fetch.return_value = {"EntryTitle": "Test"}
            with patch("lambdas.embedding.handler.fetch_associations") as mock_assoc:
                mock_assoc.return_value = {}
                with patch("lambdas.embedding.handler.get_langfuse") as mock_langfuse:
                    mock_lf_client = MagicMock()
                    mock_span = MagicMock()

                    mock_lf_client.start_as_current_span.return_value.__enter__ = MagicMock(
                        return_value=mock_span
                    )
                    mock_lf_client.start_as_current_span.return_value.__exit__ = MagicMock(
                        return_value=None
                    )
                    mock_langfuse.return_value = mock_lf_client

                    # No session_id provided
                    handle_update(message, mock_repo, mock_embedder, session_id=None)

                    # Verify start_as_current_span was called with trace name
                    call_kwargs = mock_lf_client.start_as_current_span.call_args.kwargs
                    assert call_kwargs["name"] == "collection:C1234-PROV"

                    # Verify update_current_trace was called with session_id=None
                    mock_lf_client.update_current_trace.assert_called_once_with(
                        session_id=None,
                    )

                    # Verify span.update was called
                    mock_span.update.assert_called()
