"""Tests for the enrichment validate step."""

from unittest.mock import patch

from lambdas.enrichment.models import ValidationError, ValidationResult
from lambdas.enrichment.validate import _compute_max_fix_attempts, validate


def _noop_hydrate(event):
    """Identity hydrate for testing -- returns event unchanged."""
    return event


def _noop_dehydrate(event):
    """Identity dehydrate for testing -- returns event unchanged."""
    return event


def _make_event(metadata=None, fix_attempt=0):
    """Build a minimal validate event dict for testing."""
    return {
        "concept_id": "C1234-PROV",
        "revision_id": 5,
        "concept_type": "collection",
        "metadata": metadata or {"EntryTitle": "Test"},
        "fix_attempt": fix_attempt,
    }


class TestComputeMaxFixAttempts:
    """Tests for _compute_max_fix_attempts."""

    def test_counts_kms_fields_when_kms_errors_present(self):
        """Should count KMS fields when there are kms_invalid errors to fix."""
        metadata = {
            "ScienceKeywords": [{"Category": "A"}, {"Category": "B"}],
            "Platforms": [
                {"ShortName": "TERRA", "Instruments": [{"ShortName": "MODIS"}]},
            ],
        }
        errors = [
            ValidationError(
                path="$.ScienceKeywords[0].Category", message="bad", error_type="kms_invalid"
            ),
        ]

        result = _compute_max_fix_attempts(metadata, errors)

        # 2 science keywords + 1 platform + 1 instrument = 4
        assert result == 4

    def test_caps_at_max(self):
        """Should cap max fix attempts at 30."""
        metadata = {
            "ScienceKeywords": [{"Category": f"KW{i}"} for i in range(50)],
            "Platforms": [],
        }
        errors = [
            ValidationError(
                path="$.ScienceKeywords[0].Category", message="bad", error_type="kms_invalid"
            ),
        ]

        result = _compute_max_fix_attempts(metadata, errors)

        assert result == 30

    def test_returns_zero_for_only_schema_errors(self):
        """Should return 0 when only unfixable schema errors remain."""
        metadata = {"ScienceKeywords": [], "Platforms": []}
        errors = [
            ValidationError(path="$.DOI", message="missing", error_type="required"),
            ValidationError(path="$.DOI", message="missing", error_type="required"),
        ]

        result = _compute_max_fix_attempts(metadata, errors)

        assert result == 0

    def test_returns_zero_with_no_errors(self):
        """Should return 0 when there are no errors at all."""
        metadata = {
            "ScienceKeywords": [{"Category": "A"}],
            "Platforms": [{"ShortName": "TERRA"}],
        }

        result = _compute_max_fix_attempts(metadata, [])

        assert result == 0


class TestValidateStep:
    """Tests for the validate Lambda handler."""

    @patch("lambdas.enrichment.validate.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.validate.prepare_event")
    @patch("lambdas.enrichment.validate.validate_metadata")
    def test_returns_valid_result(self, mock_validate_metadata, mock_prepare, _mock_dehydrate):
        """Should return valid result when metadata passes validation."""
        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["metadata"])
        mock_validate_metadata.return_value = ValidationResult(is_valid=True, errors=[])

        result = validate(event, None)

        assert result["validation"]["is_valid"] is True
        assert result["validation"]["errors"] == []

    @patch("lambdas.enrichment.validate.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.validate.prepare_event")
    @patch("lambdas.enrichment.validate.validate_metadata")
    def test_returns_invalid_with_errors(
        self, mock_validate_metadata, mock_prepare, _mock_dehydrate
    ):
        """Should return invalid result with errors when validation fails."""
        event = _make_event()
        mock_prepare.return_value = (event, "C1234-PROV", event["metadata"])

        errors = [
            ValidationError(
                path="$.Platforms[0].ShortName",
                message="Invalid KMS term",
                error_type="kms_invalid",
                value="BAD_PLATFORM",
                schema_fragment={"scheme": "platforms"},
            ),
        ]
        mock_validate_metadata.return_value = ValidationResult(
            is_valid=False, errors=errors, schema_url="https://example.com/schema"
        )

        result = validate(event, None)

        assert result["validation"]["is_valid"] is False
        assert len(result["validation"]["errors"]) == 1
        assert result["validation"]["schema_url"] == "https://example.com/schema"

    @patch("lambdas.enrichment.validate.dehydrate_event", side_effect=_noop_dehydrate)
    @patch("lambdas.enrichment.validate.prepare_event")
    @patch("lambdas.enrichment.validate.validate_metadata")
    def test_exceeded_max_attempts(self, mock_validate_metadata, mock_prepare, _mock_dehydrate):
        """Should set exceeded_max_attempts when fix_attempt exceeds limit."""
        event = _make_event(fix_attempt=50)
        mock_prepare.return_value = (event, "C1234-PROV", event["metadata"])
        mock_validate_metadata.return_value = ValidationResult(
            is_valid=False,
            errors=[
                ValidationError(path="$.X", message="bad", error_type="kms_invalid"),
            ],
        )

        result = validate(event, None)

        assert result["validation"]["exceeded_max_attempts"] is True
