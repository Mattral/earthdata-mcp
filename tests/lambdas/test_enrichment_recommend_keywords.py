"""Tests for the enrichment keyword recommendation module."""

from unittest.mock import MagicMock, patch

from lambdas.enrichment.umm.recommend_keywords import (
    recommend_keyword,
    recommend_keywords_batch,
)
from models.cmr import KMSTerm

# All tests patch lookup_term to avoid hitting Redis/KMS.
_PATCH_LOOKUP = "lambdas.enrichment.umm.recommend_keywords.lookup_term"


def _mock_datastore(results=None):
    """Create a mock datastore with configurable search results."""
    ds = MagicMock()
    ds.search_similar.return_value = results or []
    return ds


def _mock_embedder(embedding=None):
    """Create a mock embedding generator with configurable output."""
    emb = MagicMock()
    emb.generate.return_value = embedding or [0.1] * 1024
    return emb


class TestRecommendKeyword:
    """Tests for recommend_keyword."""

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_replace_when_above_threshold(self, _mock_lookup):
        """Should recommend replacement when similarity exceeds threshold."""
        ds = _mock_datastore(
            results=[
                {"text_content": "OCEAN TEMPERATURE", "similarity": 0.92},
            ]
        )
        result = recommend_keyword(
            "OCEAN TEMP",
            "sciencekeywords",
            threshold=0.85,
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.action == "replace"
        assert result.recommended_term == "OCEAN TEMPERATURE"
        assert result.similarity == 0.92

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_remove_when_below_threshold(self, _mock_lookup):
        """Should recommend removal when similarity is below threshold."""
        ds = _mock_datastore(
            results=[
                {"text_content": "SOMETHING ELSE", "similarity": 0.6},
            ]
        )
        result = recommend_keyword(
            "GARBAGE TERM",
            "sciencekeywords",
            threshold=0.85,
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.action == "remove"
        assert result.recommended_term is None
        assert result.similarity == 0.6

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_remove_when_no_results(self, _mock_lookup):
        """Should recommend removal when no similar terms are found."""
        ds = _mock_datastore(results=[])
        result = recommend_keyword(
            "TOTALLY UNKNOWN",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.action == "remove"
        assert result.recommended_term is None
        assert result.similarity == 0.0

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_remove_when_embedding_fails(self, _mock_lookup):
        """Should recommend removal when embedding generation fails."""
        embedder = MagicMock()
        embedder.generate.side_effect = Exception("Bedrock timeout")
        result = recommend_keyword(
            "BAD",
            "sciencekeywords",
            datastore=_mock_datastore(),
            embedding_generator=embedder,
        )
        assert result.action == "remove"
        assert result.recommended_term is None
        assert result.similarity == 0.0

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_extracts_term_from_text_content_with_definition(self, _mock_lookup):
        """text_content may be 'TERM: definition text' — only extract the term."""
        ds = _mock_datastore(
            results=[
                {
                    "text_content": "SEA SURFACE TEMPERATURE: Mean temp of the ocean surface",
                    "similarity": 0.93,
                },
            ]
        )
        result = recommend_keyword(
            "SST",
            "sciencekeywords",
            threshold=0.85,
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.action == "replace"
        assert result.recommended_term == "SEA SURFACE TEMPERATURE"

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_always_includes_alternatives(self, _mock_lookup):
        """Should always include alternative terms for diagnostics."""
        ds = _mock_datastore(
            results=[
                {"text_content": "BEST MATCH", "similarity": 0.95},
                {"text_content": "SECOND MATCH: def", "similarity": 0.88},
                {"text_content": "THIRD MATCH", "similarity": 0.82},
            ]
        )
        result = recommend_keyword(
            "TERM",
            "sciencekeywords",
            threshold=0.85,
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.action == "replace"
        assert result.alternatives is not None
        assert len(result.alternatives) == 2
        assert result.alternatives[0]["term"] == "SECOND MATCH"
        assert result.alternatives[1]["term"] == "THIRD MATCH"

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_passes_plain_term_when_no_kms_definition(self, _mock_lookup):
        """Should embed just the term when KMS lookup returns no definition."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        recommend_keyword(
            "MY TERM",
            "platforms",
            datastore=ds,
            embedding_generator=embedder,
        )
        embedder.generate.assert_called_once_with(
            "MY TERM",
            concept_type="kms_term",
            attribute="platforms",
            metadata={
                "embedding_type": "keyword_recommendation",
                "original_term": "MY TERM",
                "scheme": "platforms",
            },
        )

    @patch(_PATCH_LOOKUP)
    def test_enriches_query_with_kms_definition(self, mock_lookup):
        """Should enrich query text with KMS definition to match stored embeddings."""
        mock_lookup.return_value = KMSTerm(
            uuid="abc-123",
            scheme="sciencekeywords",
            term="GEOLOCATION",
            definition="The science of measuring positions on the earth surface",
        )
        embedder = _mock_embedder()
        ds = _mock_datastore()
        recommend_keyword(
            "GEOLOCATION",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
        )
        # Should embed enriched text, not just the term
        call_args = embedder.generate.call_args
        assert (
            call_args[0][0]
            == "GEOLOCATION: The science of measuring positions on the earth surface"
        )

    def test_uses_hierarchy_as_query_when_keyword_context_and_levels_provided(self):
        """Should use hierarchy string as query text when keyword_context and keyword_levels are provided."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        keyword_context = {
            "Category": "EARTH SCIENCE",
            "Topic": "CRYOSPHERE",
            "Term": "ICE DEPTH/THICKNESS",
        }
        keyword_levels = [
            "Category",
            "Topic",
            "Term",
            "VariableLevel1",
            "VariableLevel2",
            "VariableLevel3",
        ]
        recommend_keyword(
            "ICE DEPTH/THICKNESS",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
            keyword_context=keyword_context,
            keyword_levels=keyword_levels,
        )
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "EARTH SCIENCE > CRYOSPHERE > ICE DEPTH/THICKNESS"

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_kms_definition_used_for_platforms_without_context(self, mock_lookup):
        """Should still use KMS definition lookup for platforms (no keyword_context)."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        recommend_keyword(
            "TERRA",
            "platforms",
            datastore=ds,
            embedding_generator=embedder,
        )
        # KMS lookup should be called when no keyword_context
        mock_lookup.assert_called_once_with("TERRA", "platforms")

    def test_single_level_hierarchy_does_not_enrich(self):
        """Single-level hierarchy should fall back to bare term (no ' > ' join)."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        keyword_context = {"Category": "EARTH SCIENCE"}
        keyword_levels = ["Category", "Topic", "Term"]
        recommend_keyword(
            "EARTH SCIENCE",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
            keyword_context=keyword_context,
            keyword_levels=keyword_levels,
        )
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "EARTH SCIENCE"

    def test_keyword_context_without_levels_uses_bare_term(self):
        """Should use bare term when keyword_context is provided but keyword_levels is None."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        keyword_context = {
            "Category": "EARTH SCIENCE",
            "Topic": "CRYOSPHERE",
            "Term": "ICE DEPTH/THICKNESS",
        }
        recommend_keyword(
            "ICE DEPTH/THICKNESS",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
            keyword_context=keyword_context,
            keyword_levels=None,
        )
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "ICE DEPTH/THICKNESS"

    def test_custom_keyword_levels_with_detailed_variable(self):
        """Should use custom keyword_levels (e.g. including DetailedVariable) in hierarchy building."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        keyword_context = {
            "Category": "EARTH SCIENCE",
            "Topic": "CRYOSPHERE",
            "Term": "ICE DEPTH/THICKNESS",
            "DetailedVariable": "FAST ICE",
        }
        keyword_levels = [
            "Category",
            "Topic",
            "Term",
            "VariableLevel1",
            "VariableLevel2",
            "VariableLevel3",
            "DetailedVariable",
        ]
        recommend_keyword(
            "FAST ICE",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
            keyword_context=keyword_context,
            keyword_levels=keyword_levels,
        )
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "EARTH SCIENCE > CRYOSPHERE > ICE DEPTH/THICKNESS > FAST ICE"

    @patch(_PATCH_LOOKUP, side_effect=Exception("Redis down"))
    def test_falls_back_to_plain_term_on_kms_error(self, _mock_lookup):
        """Should use plain term when KMS lookup raises an exception."""
        embedder = _mock_embedder()
        ds = _mock_datastore()
        recommend_keyword(
            "MY TERM",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=embedder,
        )
        call_args = embedder.generate.call_args
        assert call_args[0][0] == "MY TERM"

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_searches_correct_entity_type(self, _mock_lookup):
        """Should search with correct entity type in datastore."""
        ds = _mock_datastore()
        embedder = _mock_embedder([0.5] * 1024)
        recommend_keyword(
            "TERM",
            "instruments",
            datastore=ds,
            embedding_generator=embedder,
        )
        ds.search_similar.assert_called_once_with(
            embedding=[0.5] * 1024,
            limit=5,
            entity_type="instruments",
        )

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_preserves_original_term_and_scheme(self, _mock_lookup):
        """Should preserve original term and scheme in result."""
        ds = _mock_datastore()
        result = recommend_keyword(
            "ORIG",
            "sciencekeywords",
            datastore=ds,
            embedding_generator=_mock_embedder(),
        )
        assert result.original_term == "ORIG"
        assert result.scheme == "sciencekeywords"


class TestRecommendKeywordsBatch:
    """Tests for recommend_keywords_batch."""

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_processes_multiple_terms(self, _mock_lookup):
        """Should process and return results for all terms in batch."""
        ds = _mock_datastore(results=[{"text_content": "REPLACEMENT", "similarity": 0.95}])
        embedder = _mock_embedder()

        terms = [("BAD1", "sciencekeywords"), ("BAD2", "platforms")]
        results = recommend_keywords_batch(
            terms,
            datastore=ds,
            embedding_generator=embedder,
        )

        assert len(results) == 2
        assert ("BAD1", "sciencekeywords") in results
        assert ("BAD2", "platforms") in results

    @patch(_PATCH_LOOKUP, return_value=None)
    def test_returns_empty_dict_for_no_terms(self, _mock_lookup):
        """Should return empty dict when no terms are provided."""
        results = recommend_keywords_batch(
            [],
            datastore=_mock_datastore(),
            embedding_generator=_mock_embedder(),
        )
        assert not results
