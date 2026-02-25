"""Tests for the enrichment fix step — keyword recommendation and fix logic."""

from unittest.mock import patch

from lambdas.enrichment.fixer import (
    _apply_keyword_recommendation,
    _deserialize_validation_errors,
    fix_one,
    fix_one_error,
)
from lambdas.enrichment.models import (
    RecommendationResult,
    ValidationError,
)


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(metadata=None, enriched_metadata=None, errors=None, fix_attempt=0):
    """Build a minimal fixer agent event dict for testing."""
    event = {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": metadata or {"EntryTitle": "Raw"},
        "fix_attempt": fix_attempt,
        "fix_history": [],
    }
    if enriched_metadata is not None:
        event["enriched_metadata"] = enriched_metadata
    if errors is not None:
        event["validation"] = {"is_valid": False, "errors": errors}
    return event


def _kms_error(
    path="$.ScienceKeywords[0]",
    value="INVALID TERM",
    scheme="sciencekeywords",
    leaf_level="Category",
    invalid_levels=None,
):
    """Build a KMS validation error for testing.

    For sciencekeywords with whole-entry paths (ending with ``]``),
    includes ``leaf_level`` and ``invalid_levels`` in schema_fragment.
    """
    if scheme == "sciencekeywords" and path.endswith("]"):
        fragment = {
            "scheme": scheme,
            "leaf_level": leaf_level,
            "invalid_levels": invalid_levels or {leaf_level: value},
        }
    elif scheme:
        fragment = {"scheme": scheme}
    else:
        fragment = None

    return ValidationError(
        path=path,
        message=f"'{value}' is not a valid KMS keyword",
        error_type="kms_invalid",
        value=value,
        schema_fragment=fragment,
    )


class TestDeserializeValidationErrors:
    """Tests for _deserialize_validation_errors."""

    def test_deserializes_full_error(self):
        """Should deserialize a complete validation error dict."""
        data = [
            {
                "path": "$.Platforms[0].Type",
                "message": "Invalid",
                "error_type": "kms_invalid",
                "value": "BAD",
                "allowed_values": ["GOOD"],
                "schema_fragment": {"scheme": "platforms"},
            }
        ]
        errors = _deserialize_validation_errors(data)
        assert len(errors) == 1
        assert errors[0].path == "$.Platforms[0].Type"
        assert errors[0].value == "BAD"
        assert errors[0].schema_fragment == {"scheme": "platforms"}

    def test_handles_missing_optional_fields(self):
        """Should handle errors with missing optional fields."""
        data = [{"path": "$.X", "message": "err", "error_type": "enum"}]
        errors = _deserialize_validation_errors(data)
        assert errors[0].value is None
        assert errors[0].allowed_values is None
        assert errors[0].schema_fragment is None


class TestFixOneError:
    """Tests for fix_one_error."""

    def test_returns_unchanged_when_no_errors(self):
        """Should return unchanged metadata when error list is empty."""
        metadata = {"EntryTitle": "Test"}
        result_metadata, fix_result = fix_one_error(metadata, [])
        assert result_metadata == metadata
        assert fix_result is None

    def test_skips_non_kms_invalid_errors(self):
        """Should skip errors that are not kms_invalid type."""
        error = ValidationError(
            path="$.Platforms[0].Type",
            message="invalid enum",
            error_type="enum",
            value="BADVAL",
        )
        metadata = {"Platforms": [{"Type": "BADVAL"}]}
        _, fix_result = fix_one_error(metadata, [error])
        assert fix_result.success is False
        assert fix_result.action == "skip"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_applies_kms_keyword_replacement(self, mock_recommend):
        """Should replace leaf level of invalid ScienceKeyword with recommended term."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="EARTH SCIENCE",
            similarity=0.95,
            action="replace",
            original_term="EARTH SCI",
            scheme="sciencekeywords",
        )
        metadata = {"ScienceKeywords": [{"Category": "EARTH SCI"}]}
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="EARTH SCI",
            scheme="sciencekeywords",
            leaf_level="Category",
        )
        _, fix_result = fix_one_error(metadata, [error])
        assert fix_result.success is True
        assert fix_result.new_value == "EARTH SCIENCE"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_removes_entire_entry_when_no_close_match(self, mock_recommend):
        """Should remove entire ScienceKeyword entry when no close match is found."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.3,
            action="remove",
            original_term="GARBAGE",
            scheme="sciencekeywords",
        )
        # Two entries so removal of one is allowed (can't remove the last one)
        metadata = {
            "ScienceKeywords": [
                {"Category": "GARBAGE", "Topic": "JUNK"},
                {"Category": "EARTH SCIENCE", "Topic": "ATMOSPHERE"},
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="JUNK",
            scheme="sciencekeywords",
            leaf_level="Topic",
        )
        fixed_metadata, fix_result = fix_one_error(metadata, [error])
        assert fix_result.success is True
        assert fix_result.new_value is None
        assert "Removed" in fix_result.notes
        # Only the invalid entry is removed
        assert len(fixed_metadata["ScienceKeywords"]) == 1
        assert fixed_metadata["ScienceKeywords"][0]["Topic"] == "ATMOSPHERE"

    @patch("lambdas.enrichment.fixer.recommend_keyword", side_effect=Exception("DB down"))
    def test_handles_recommendation_exception(self, _mock_recommend):
        """Should return failure when recommendation raises an exception."""
        metadata = {"ScienceKeywords": [{"Category": "BAD"}]}
        error = _kms_error(value="BAD")
        _, fix_result = fix_one_error(metadata, [error])
        assert fix_result.success is False
        assert "DB down" in fix_result.error

    def test_does_not_mutate_original_metadata(self):
        """Should not modify the original metadata dict."""
        original = {"ScienceKeywords": [{"Category": "EARTH SCI"}]}
        import copy

        snapshot = copy.deepcopy(original)
        error = _kms_error(value="EARTH SCI")
        with patch("lambdas.enrichment.fixer.recommend_keyword") as mock_rec:
            mock_rec.return_value = RecommendationResult(
                recommended_term="EARTH SCIENCE",
                similarity=0.95,
                action="replace",
                original_term="EARTH SCI",
                scheme="sciencekeywords",
            )
            fix_one_error(original, [error])
        assert original == snapshot


class TestApplyKeywordRecommendation:
    """Tests for _apply_keyword_recommendation."""

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_successful_replacement(self, mock_recommend):
        """Should successfully replace invalid keyword with recommended term."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="OCEAN TEMPERATURE",
            similarity=0.92,
            action="replace",
            original_term="OCEAN TEMP",
            scheme="sciencekeywords",
        )
        metadata = {"ScienceKeywords": [{"Category": "OCEAN TEMP"}]}
        error = _kms_error(value="OCEAN TEMP")

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert result.new_value == "OCEAN TEMPERATURE"
        assert result.old_value == "OCEAN TEMP"
        assert "similarity: 0.920" in result.notes

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_removal_when_no_replacement(self, mock_recommend):
        """Should remove ScienceKeyword entry when no close replacement is found (not the last one)."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.4,
            action="remove",
            original_term="NONSENSE",
            scheme="sciencekeywords",
        )
        # Two entries so removal of one is allowed
        metadata = {
            "ScienceKeywords": [
                {"Category": "NONSENSE"},
                {"Category": "EARTH SCIENCE"},
            ]
        }
        error = _kms_error(value="NONSENSE")

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert result.new_value is None
        assert result.old_value == {"Category": "NONSENSE"}
        assert "below threshold" in result.notes
        assert len(metadata["ScienceKeywords"]) == 1

    @patch("lambdas.enrichment.fixer.get_science_keyword_levels", return_value=None)
    def test_infers_scheme_from_path_sciencekeywords(self, _mock_get_levels):
        """When schema_fragment lacks a scheme, infer from JSON path and still extract context."""
        error = ValidationError(
            path="$.ScienceKeywords[0].Category",
            message="invalid",
            error_type="kms_invalid",
            value="BAD",
            schema_fragment=None,
        )
        keyword_entry = {"Category": "BAD"}
        metadata = {"ScienceKeywords": [keyword_entry]}

        with patch("lambdas.enrichment.fixer.recommend_keyword") as mock_rec:
            mock_rec.return_value = RecommendationResult(
                recommended_term="GOOD",
                similarity=0.9,
                action="replace",
                original_term="BAD",
                scheme="sciencekeywords",
            )
            result = _apply_keyword_recommendation(metadata, error)

        mock_rec.assert_called_once_with(
            "BAD", "sciencekeywords", keyword_context=keyword_entry, keyword_levels=None
        )
        assert result.success is True

    def test_infers_scheme_from_path_platforms(self):
        """Should infer platforms scheme from JSON path."""
        error = ValidationError(
            path="$.Platforms[0].ShortName",
            message="invalid",
            error_type="kms_invalid",
            value="BAD",
            schema_fragment=None,
        )
        metadata = {"Platforms": [{"ShortName": "BAD"}]}

        with patch("lambdas.enrichment.fixer.recommend_keyword") as mock_rec:
            mock_rec.return_value = RecommendationResult(
                recommended_term=None,
                similarity=0.1,
                action="remove",
                original_term="BAD",
                scheme="platforms",
            )
            _apply_keyword_recommendation(metadata, error)

        mock_rec.assert_called_once_with(
            "BAD", "platforms", keyword_context=None, keyword_levels=None
        )

    def test_infers_scheme_from_path_instruments(self):
        """Should infer instruments scheme from JSON path."""
        error = ValidationError(
            path="$.Platforms[0].Instruments[0].ShortName",
            message="invalid",
            error_type="kms_invalid",
            value="BAD",
            schema_fragment=None,
        )
        metadata = {"Platforms": [{"Instruments": [{"ShortName": "BAD"}]}]}

        with patch("lambdas.enrichment.fixer.recommend_keyword") as mock_rec:
            mock_rec.return_value = RecommendationResult(
                recommended_term=None,
                similarity=0.1,
                action="remove",
                original_term="BAD",
                scheme="instruments",
            )
            _apply_keyword_recommendation(metadata, error)

        mock_rec.assert_called_once_with(
            "BAD", "instruments", keyword_context=None, keyword_levels=None
        )

    def test_fails_when_no_term(self):
        """Should fail when error has no value to look up."""
        error = ValidationError(
            path="$.ScienceKeywords[0].Category",
            message="invalid",
            error_type="kms_invalid",
            value=None,
            schema_fragment={"scheme": "sciencekeywords"},
        )
        result = _apply_keyword_recommendation({}, error)
        assert result.success is False
        assert "Could not extract term or scheme" in result.error

    def test_fails_when_no_scheme(self):
        """Should fail when scheme cannot be determined from error or path."""
        error = ValidationError(
            path="$.SomeUnknownField",
            message="invalid",
            error_type="kms_invalid",
            value="TERM",
            schema_fragment=None,
        )
        result = _apply_keyword_recommendation({}, error)
        assert result.success is False
        assert "Could not extract term or scheme" in result.error

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    @patch("lambdas.enrichment.fixer.set_value_at_path", return_value=False)
    def test_fails_when_set_value_fails(self, _mock_set, mock_recommend):
        """Should fail when set_value_at_path returns False."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="GOOD",
            similarity=0.95,
            action="replace",
            original_term="BAD",
            scheme="sciencekeywords",
        )
        error = _kms_error(value="BAD")
        result = _apply_keyword_recommendation({"ScienceKeywords": [{"Category": "BAD"}]}, error)
        assert result.success is False
        assert "Failed to set replacement value" in result.error

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    @patch("lambdas.enrichment.fixer.remove_value_at_path", return_value=(False, None))
    def test_fails_when_remove_value_fails(self, _mock_remove, mock_recommend):
        """Should fail when remove_value_at_path returns False (field-level path)."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.3,
            action="remove",
            original_term="BAD",
            scheme="platforms",
        )
        error = _kms_error(
            path="$.Platforms[0].ShortName",
            value="BAD",
            scheme="platforms",
        )
        result = _apply_keyword_recommendation({"Platforms": [{"ShortName": "BAD"}]}, error)
        assert result.success is False
        assert "Failed to remove" in result.error

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_skips_noop_replacement_same_term(self, mock_recommend):
        """Should skip when recommended term matches original (likely stale KMS cache)."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="GEOLOCATION",
            similarity=0.861,
            action="replace",
            original_term="GEOLOCATION",
            scheme="sciencekeywords",
        )
        metadata = {
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "SOLID EARTH", "Term": "GEOLOCATION"}
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="GEOLOCATION",
            scheme="sciencekeywords",
            leaf_level="Term",
            invalid_levels={"Term": "GEOLOCATION"},
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert "no-op" in result.notes.lower()
        # Metadata should be unchanged
        assert metadata["ScienceKeywords"][0]["Term"] == "GEOLOCATION"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_skips_noop_replacement_case_insensitive(self, mock_recommend):
        """Should detect no-op replacement even with different casing."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="Geolocation",
            similarity=0.88,
            action="replace",
            original_term="GEOLOCATION",
            scheme="sciencekeywords",
        )
        metadata = {"Platforms": [{"ShortName": "GEOLOCATION"}]}
        error = _kms_error(
            path="$.Platforms[0].ShortName",
            value="GEOLOCATION",
            scheme="platforms",
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert "no-op" in result.notes.lower()

    @patch("lambdas.enrichment.fixer.get_science_keyword_levels")
    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_passes_keyword_context_and_levels_for_science_keyword_errors(
        self, mock_recommend, mock_get_levels
    ):
        """Should pass keyword_context and keyword_levels for whole science keyword errors."""
        mock_get_levels.return_value = [
            "Category",
            "Topic",
            "Term",
            "VariableLevel1",
            "VariableLevel2",
            "VariableLevel3",
        ]
        mock_recommend.return_value = RecommendationResult(
            recommended_term="PRECIPITATION",
            similarity=0.90,
            action="replace",
            original_term="PRECIPITATOIN",
            scheme="sciencekeywords",
        )
        keyword_entry = {
            "Category": "EARTH SCIENCE",
            "Topic": "ATMOSPHERE",
            "Term": "PRECIPITATOIN",
        }
        metadata = {"ScienceKeywords": [keyword_entry]}
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="PRECIPITATOIN",
            scheme="sciencekeywords",
            leaf_level="Term",
            invalid_levels={"Term": "PRECIPITATOIN"},
        )

        _apply_keyword_recommendation(metadata, error)

        mock_recommend.assert_called_once_with(
            "PRECIPITATOIN",
            "sciencekeywords",
            keyword_context=keyword_entry,
            keyword_levels=mock_get_levels.return_value,
        )

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_replaces_leaf_level_of_whole_science_keyword(self, mock_recommend):
        """Should replace the leaf level value when fixing a whole ScienceKeyword entry."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term="PRECIPITATION",
            similarity=0.90,
            action="replace",
            original_term="PRECIPITATOIN",
            scheme="sciencekeywords",
        )
        metadata = {
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "ATMOSPHERE", "Term": "PRECIPITATOIN"}
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="PRECIPITATOIN",
            scheme="sciencekeywords",
            leaf_level="Term",
            invalid_levels={"Term": "PRECIPITATOIN"},
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert result.old_value == "PRECIPITATOIN"
        assert result.new_value == "PRECIPITATION"
        # Other levels should remain unchanged
        assert metadata["ScienceKeywords"][0]["Category"] == "EARTH SCIENCE"
        assert metadata["ScienceKeywords"][0]["Topic"] == "ATMOSPHERE"
        assert metadata["ScienceKeywords"][0]["Term"] == "PRECIPITATION"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_removes_entire_science_keyword_entry(self, mock_recommend):
        """Should pop the entire ScienceKeyword entry when others remain."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.2,
            action="remove",
            original_term="JUNK",
            scheme="sciencekeywords",
        )
        metadata = {
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "JUNK"},
                {"Category": "EARTH SCIENCE", "Topic": "ATMOSPHERE"},
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="JUNK",
            scheme="sciencekeywords",
            leaf_level="Topic",
            invalid_levels={"Topic": "JUNK"},
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert result.new_value is None
        assert result.old_value == {"Category": "EARTH SCIENCE", "Topic": "JUNK"}
        assert len(metadata["ScienceKeywords"]) == 1
        assert metadata["ScienceKeywords"][0]["Topic"] == "ATMOSPHERE"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_uses_best_candidate_for_last_science_keyword(self, mock_recommend):
        """Should replace with best candidate when removing would leave ScienceKeywords empty."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.7,
            action="remove",
            original_term="JUNK",
            scheme="sciencekeywords",
            best_candidate="ATMOSPHERE",
        )
        metadata = {
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "JUNK"},
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[0]",
            value="JUNK",
            scheme="sciencekeywords",
            leaf_level="Topic",
            invalid_levels={"Topic": "JUNK"},
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert result.new_value == "ATMOSPHERE"
        assert "preserve required ScienceKeywords" in result.notes
        assert metadata["ScienceKeywords"][0]["Topic"] == "ATMOSPHERE"

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_fails_when_last_keyword_has_no_candidate(self, mock_recommend):
        """Should fail when removing last keyword and no best candidate available."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.0,
            action="remove",
            original_term="GARBAGE",
            scheme="sciencekeywords",
            best_candidate=None,
        )
        metadata = {"ScienceKeywords": [{"Category": "GARBAGE"}]}
        error = _kms_error(value="GARBAGE")

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is False
        assert "Cannot remove last ScienceKeyword" in result.error
        # Keyword should still be there
        assert len(metadata["ScienceKeywords"]) == 1

    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_removes_science_keyword_preserves_other_entries(self, mock_recommend):
        """Should remove only the targeted entry, keeping other keywords intact."""
        mock_recommend.return_value = RecommendationResult(
            recommended_term=None,
            similarity=0.2,
            action="remove",
            original_term="GARBAGE",
            scheme="sciencekeywords",
        )
        metadata = {
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "ATMOSPHERE", "Term": "RAIN"},
                {"Category": "GARBAGE", "Topic": "JUNK"},
            ]
        }
        error = _kms_error(
            path="$.ScienceKeywords[1]",
            value="JUNK",
            scheme="sciencekeywords",
            leaf_level="Topic",
            invalid_levels={"Category": "GARBAGE", "Topic": "JUNK"},
        )

        result = _apply_keyword_recommendation(metadata, error)

        assert result.success is True
        assert len(metadata["ScienceKeywords"]) == 1
        assert metadata["ScienceKeywords"][0]["Term"] == "RAIN"


class TestFixOneHandler:
    """Tests for the fix_one handler function."""

    @patch("lambdas.enrichment.fixer.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fixer.prepare_event")
    def test_no_errors_returns_unchanged(self, mock_prepare, _mock_dehydrate):
        """Should return unchanged event when no validation errors exist."""
        metadata = {"EntryTitle": "Test"}
        event = _make_event(enriched_metadata=metadata, errors=[])
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        result = fix_one(event, None)

        assert result["fix_attempt"] == 0
        assert result["last_fix"] is None

    @patch("lambdas.enrichment.fixer.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fixer.prepare_event")
    @patch("lambdas.enrichment.fixer.recommend_keyword")
    def test_successful_fix_increments_attempt(self, mock_recommend, mock_prepare, _mock_dehydrate):
        """Should increment fix_attempt and record fix in history on success."""
        metadata = {"ScienceKeywords": [{"Category": "BAD"}]}
        errors = [
            {
                "path": "$.ScienceKeywords[0]",
                "message": "invalid",
                "error_type": "kms_invalid",
                "value": "BAD",
                "schema_fragment": {
                    "scheme": "sciencekeywords",
                    "leaf_level": "Category",
                    "invalid_levels": {"Category": "BAD"},
                },
            }
        ]
        event = _make_event(enriched_metadata=metadata, errors=errors)
        mock_prepare.return_value = (event, "C1234-PROV", metadata)

        mock_recommend.return_value = RecommendationResult(
            recommended_term="GOOD",
            similarity=0.95,
            action="replace",
            original_term="BAD",
            scheme="sciencekeywords",
        )

        result = fix_one(event, None)

        assert result["fix_attempt"] == 1
        assert result["last_fix"]["success"] is True
        assert len(result["fix_history"]) == 1

    @patch("lambdas.enrichment.fixer.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.fixer.prepare_event")
    @patch(
        "lambdas.enrichment.fixer.fix_one_error",
        side_effect=Exception("unexpected"),
    )
    def test_exception_returns_failure(self, _mock_fix, mock_prepare, _mock_dehydrate):
        """Should return failure result when fix_one_error raises an exception."""
        errors = [
            {
                "path": "$.X",
                "message": "err",
                "error_type": "kms_invalid",
                "value": "V",
            }
        ]
        event = _make_event(errors=errors)
        mock_prepare.return_value = (event, "C1234-PROV", {"EntryTitle": "Raw"})

        result = fix_one(event, None)

        assert result["fix_attempt"] == 1
        assert result["last_fix"]["success"] is False
        assert "unexpected" in result["last_fix"]["error"]
