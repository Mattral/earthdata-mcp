"""Tests for UMM fix step."""

from lambdas.enrichment.fixer import fix_one_error
from lambdas.enrichment.models import ValidationError


class TestFixOneErrorSkipsNonKmsErrors:
    """Tests that fix_one_error skips non-kms_invalid error types."""

    def test_skips_required_errors(self):
        """Should skip required errors."""
        metadata = {"EntryTitle": "Test"}
        errors = [
            ValidationError(
                path="$.EntryTitle",
                message="Required field",
                error_type="required",
            )
        ]

        _, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.success is False
        assert result.action == "skip"

    def test_skips_enum_errors(self):
        """Should skip enum errors."""
        metadata = {"SomeField": "value"}
        errors = [
            ValidationError(
                path="$.SomeField",
                message="Invalid value",
                error_type="enum",
            )
        ]

        _, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.success is False
        assert result.action == "skip"

    def test_skips_pattern_errors(self):
        """Should skip pattern errors."""
        metadata = {"SomeField": "value"}
        errors = [
            ValidationError(
                path="$.SomeField",
                message="Pattern mismatch",
                error_type="pattern",
            )
        ]

        _, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.success is False
        assert result.action == "skip"

    def test_skips_additional_properties_errors(self):
        """Should skip additionalProperties errors."""
        metadata = {"SomeField": "value"}
        errors = [
            ValidationError(
                path="$.SomeField",
                message="Additional property not allowed",
                error_type="additionalProperties",
            )
        ]

        _, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.success is False
        assert result.action == "skip"


class TestFixOneError:
    """Tests for fix_one_error main function."""

    def test_returns_none_result_for_empty_errors(self):
        """Should return None result for empty error list."""
        metadata = {"EntryTitle": "Test"}

        fixed_metadata, result = fix_one_error(metadata, [])

        assert result is None
        assert fixed_metadata == metadata

    def test_skips_unfixable_errors(self):
        """Should skip errors that are not kms_invalid."""
        metadata = {"InvalidField": "value", "ValidField": "good"}
        errors = [
            ValidationError(
                path="$.InvalidField",
                message="Not allowed",
                error_type="additionalProperties",
            )
        ]

        fixed_metadata, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.success is False
        assert result.action == "skip"
        assert "InvalidField" in fixed_metadata

    def test_does_not_modify_original_metadata(self):
        """Should not modify the original metadata dict."""
        metadata = {"InvalidField": "value", "ValidField": "good"}
        errors = [
            ValidationError(
                path="$.InvalidField",
                message="Not allowed",
                error_type="additionalProperties",
            )
        ]

        fix_one_error(metadata, errors)

        assert "InvalidField" in metadata

    def test_skips_type_errors(self):
        """Should skip type errors."""
        metadata = {"SomeField": "value"}
        errors = [
            ValidationError(
                path="$.SomeField",
                message="Wrong type",
                error_type="type",
            )
        ]

        fixed_metadata, result = fix_one_error(metadata, errors)

        assert result is not None
        assert result.action == "skip"
        assert fixed_metadata is not None
