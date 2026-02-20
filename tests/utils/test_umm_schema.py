"""Tests for UMM schema validation module."""

from unittest.mock import MagicMock, patch

from lambdas.enrichment.models import ValidationError
from lambdas.enrichment.umm.schema import (
    _extract_kms_terms_for_validation,
    get_science_keyword_levels,
    validate_kms_keywords,
    validate_metadata,
    validate_schema,
)

_COMMON_SCHEMA = {
    "definitions": {
        "ScienceKeywordType": {
            "properties": {
                "Category": {"type": "string"},
                "Topic": {"type": "string"},
                "Term": {"type": "string"},
                "VariableLevel1": {"type": "string"},
                "VariableLevel2": {"type": "string"},
                "VariableLevel3": {"type": "string"},
            }
        }
    }
}

_METADATA_WITH_SPEC = {
    "MetadataSpecification": {"URL": "https://cdn.earthdata.nasa.gov/umm/collection/v1.18.2"},
}

_PATCH_FETCH_COMMON = "lambdas.enrichment.umm.schema._fetch_common_schema"


class TestValidationError:
    """Tests for ValidationError dataclass."""

    def test_creates_with_all_fields(self):
        """Should create error with all fields."""
        error = ValidationError(
            path="$.Platforms[0].Type",
            message="'invalid' is not a valid enum value",
            error_type="enum",
            value="invalid",
            allowed_values=["A", "B", "C"],
            schema_fragment={"enum": ["A", "B", "C"]},
        )
        assert error.path == "$.Platforms[0].Type"
        assert error.error_type == "enum"
        assert error.allowed_values == ["A", "B", "C"]

    def test_creates_with_minimal_fields(self):
        """Should create error with minimal required fields."""
        error = ValidationError(
            path="$.DOI.DOI",
            message="Does not match pattern",
            error_type="pattern",
        )
        assert error.value is None
        assert error.allowed_values is None


class TestGetScienceKeywordLevels:
    """Tests for get_science_keyword_levels."""

    @patch(_PATCH_FETCH_COMMON, return_value=_COMMON_SCHEMA)
    def test_returns_property_names_in_order(self, _mock_fetch):
        """Should return all property names from ScienceKeywordType."""
        metadata = {**_METADATA_WITH_SPEC}
        levels = get_science_keyword_levels(metadata)
        assert levels == [
            "Category",
            "Topic",
            "Term",
            "VariableLevel1",
            "VariableLevel2",
            "VariableLevel3",
        ]

    def test_returns_none_when_no_metadata_specification(self):
        """Should return None when MetadataSpecification is missing."""
        levels = get_science_keyword_levels({"EntryTitle": "Test"})
        assert levels is None

    @patch(_PATCH_FETCH_COMMON, return_value=None)
    def test_returns_none_when_fetch_fails(self, _mock_fetch):
        """Should return None when _fetch_common_schema returns None."""
        levels = get_science_keyword_levels({**_METADATA_WITH_SPEC})
        assert levels is None

    @patch(_PATCH_FETCH_COMMON, return_value={"definitions": {}})
    def test_returns_none_when_missing_science_keyword_type(self, _mock_fetch):
        """Should return None when schema lacks ScienceKeywordType definition."""
        levels = get_science_keyword_levels({**_METADATA_WITH_SPEC})
        assert levels is None

    @patch(_PATCH_FETCH_COMMON)
    def test_handles_future_schema_with_new_levels(self, mock_fetch):
        """Should return whatever properties the schema defines, including new ones."""
        future_schema = {
            "definitions": {
                "ScienceKeywordType": {
                    "properties": {
                        "Category": {"type": "string"},
                        "Topic": {"type": "string"},
                        "Term": {"type": "string"},
                        "VariableLevel1": {"type": "string"},
                        "VariableLevel2": {"type": "string"},
                        "VariableLevel3": {"type": "string"},
                        "DetailedVariable": {"type": "string"},
                    }
                }
            }
        }
        mock_fetch.return_value = future_schema
        levels = get_science_keyword_levels({**_METADATA_WITH_SPEC})
        assert levels == [
            "Category",
            "Topic",
            "Term",
            "VariableLevel1",
            "VariableLevel2",
            "VariableLevel3",
            "DetailedVariable",
        ]


class TestExtractKmsTermsForValidation:
    """Tests for _extract_kms_terms_for_validation."""

    @patch(_PATCH_FETCH_COMMON, return_value=_COMMON_SCHEMA)
    def test_extracts_science_keywords(self, _mock_fetch):
        """Should extract all levels of science keywords."""
        metadata = {
            **_METADATA_WITH_SPEC,
            "ScienceKeywords": [
                {
                    "Category": "EARTH SCIENCE",
                    "Topic": "ATMOSPHERE",
                    "Term": "PRECIPITATION",
                    "VariableLevel1": "RAIN",
                }
            ],
        }
        terms = _extract_kms_terms_for_validation(metadata)

        assert ("EARTH SCIENCE", "sciencekeywords") in terms
        assert ("ATMOSPHERE", "sciencekeywords") in terms
        assert ("PRECIPITATION", "sciencekeywords") in terms
        assert ("RAIN", "sciencekeywords") in terms

    def test_extracts_platforms_and_instruments(self):
        """Should extract platform and instrument short names."""
        metadata = {
            "Platforms": [
                {
                    "ShortName": "TERRA",
                    "Instruments": [
                        {"ShortName": "MODIS"},
                        {"ShortName": "ASTER"},
                    ],
                }
            ]
        }
        terms = _extract_kms_terms_for_validation(metadata)

        assert ("TERRA", "platforms") in terms
        assert ("MODIS", "instruments") in terms
        assert ("ASTER", "instruments") in terms

    def test_handles_empty_metadata(self):
        """Should handle metadata with no keywords."""
        metadata = {"EntryTitle": "Test Collection"}
        terms = _extract_kms_terms_for_validation(metadata)
        assert not terms


class TestValidateKmsKeywords:
    """Tests for validate_kms_keywords."""

    @patch(_PATCH_FETCH_COMMON, return_value=_COMMON_SCHEMA)
    def test_returns_errors_for_invalid_keywords(self, _mock_fetch):
        """Should return one grouped error per ScienceKeyword entry with invalid levels."""
        metadata = {
            **_METADATA_WITH_SPEC,
            "ScienceKeywords": [{"Category": "INVALID_CATEGORY", "Topic": "ATMOSPHERE"}],
        }

        with patch("lambdas.enrichment.umm.schema.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {
                ("INVALID_CATEGORY", "sciencekeywords"): None,
                ("ATMOSPHERE", "sciencekeywords"): MagicMock(uuid="atm-uuid"),
            }

            errors = validate_kms_keywords(metadata)

        assert len(errors) == 1
        assert errors[0].error_type == "kms_invalid"
        assert errors[0].path == "$.ScienceKeywords[0]"
        # Leaf value is the most specific populated level (Topic)
        assert errors[0].value == "ATMOSPHERE"
        assert errors[0].schema_fragment["leaf_level"] == "Topic"
        assert errors[0].schema_fragment["invalid_levels"] == {"Category": "INVALID_CATEGORY"}

    def test_returns_empty_for_valid_keywords(self):
        """Should return empty list for all valid keywords."""
        metadata = {"Platforms": [{"ShortName": "TERRA"}]}

        with patch("lambdas.enrichment.umm.schema.lookup_terms") as mock_lookup:
            mock_lookup.return_value = {
                ("TERRA", "platforms"): MagicMock(uuid="terra-uuid"),
            }

            errors = validate_kms_keywords(metadata)

        assert not errors

    @patch(_PATCH_FETCH_COMMON, return_value=_COMMON_SCHEMA)
    def test_kms_invalid_error_has_correct_path_and_value(self, _mock_fetch):
        """Should produce one grouped ScienceKeyword error with leaf_level and invalid_levels."""
        metadata = {
            **_METADATA_WITH_SPEC,
            "ScienceKeywords": [
                {"Category": "EARTH SCIENCE", "Topic": "ATMOSPHERE", "Term": "BOGUS_TERM"},
            ],
            "Platforms": [
                {
                    "ShortName": "TERRA",
                    "Instruments": [{"ShortName": "NOT_A_REAL_INSTRUMENT"}],
                }
            ],
        }

        with patch("lambdas.enrichment.umm.schema.lookup_terms") as mock_lookup:
            # Simulate KMS returning None for invalid terms, valid KMSTerm for others
            mock_lookup.return_value = {
                ("EARTH SCIENCE", "sciencekeywords"): MagicMock(uuid="es-uuid"),
                ("ATMOSPHERE", "sciencekeywords"): MagicMock(uuid="atm-uuid"),
                ("BOGUS_TERM", "sciencekeywords"): None,  # Invalid
                ("TERRA", "platforms"): MagicMock(uuid="terra-uuid"),
                ("NOT_A_REAL_INSTRUMENT", "instruments"): None,  # Invalid
            }

            errors = validate_kms_keywords(metadata)

        assert len(errors) == 2

        # ScienceKeyword error is grouped at entry level
        sk_error = next(
            e for e in errors if "sciencekeywords" in (e.schema_fragment or {}).get("scheme", "")
        )
        instrument_error = next(e for e in errors if e.value == "NOT_A_REAL_INSTRUMENT")

        # Verify kms_invalid error type (what fix step checks)
        assert sk_error.error_type == "kms_invalid"
        assert instrument_error.error_type == "kms_invalid"

        # ScienceKeyword: grouped path at entry level, leaf value
        assert sk_error.path == "$.ScienceKeywords[0]"
        assert sk_error.value == "BOGUS_TERM"  # leaf = Term (most specific populated level)
        assert sk_error.schema_fragment["scheme"] == "sciencekeywords"
        assert sk_error.schema_fragment["leaf_level"] == "Term"
        assert sk_error.schema_fragment["invalid_levels"] == {"Term": "BOGUS_TERM"}

        # Instrument error unchanged: field-level path
        assert instrument_error.path == "$.Platforms[0].Instruments[0].ShortName"
        assert instrument_error.schema_fragment == {"scheme": "instruments"}


class TestValidateSchema:
    """Tests for validate_schema against JSON Schema."""

    def test_returns_errors_for_invalid_metadata(self):
        """Should return validation errors for invalid metadata."""
        # Simple schema requiring a string field
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
        }
        metadata = {"name": ""}  # Empty string violates minLength

        errors = validate_schema(metadata, schema)

        assert len(errors) > 0
        assert any(e.error_type == "minLength" for e in errors)

    def test_returns_empty_for_valid_metadata(self):
        """Should return empty list for valid metadata."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        metadata = {"name": "Valid Name"}

        errors = validate_schema(metadata, schema)

        assert not errors

    def test_extracts_enum_allowed_values(self):
        """Should extract allowed values for enum errors."""
        schema = {"type": "object", "properties": {"status": {"enum": ["active", "inactive"]}}}
        metadata = {"status": "unknown"}

        errors = validate_schema(metadata, schema)

        assert len(errors) == 1
        assert errors[0].error_type == "enum"
        assert errors[0].allowed_values == ["active", "inactive"]


class TestValidateMetadata:
    """Tests for the main validate_metadata function."""

    def test_combines_schema_and_kms_validation(self):
        """Should run both schema and KMS validation."""
        metadata = {
            "EntryTitle": "Test",
            "Platforms": [{"ShortName": "INVALID_PLATFORM"}],
            "MetadataSpecification": {
                "URL": "https://cdn.earthdata.nasa.gov/umm/collection/v1.18.2"
            },
        }

        with (
            patch("lambdas.enrichment.umm.schema.fetch_schema") as mock_fetch,
            patch("lambdas.enrichment.umm.schema.validate_schema") as mock_schema_validate,
            patch("lambdas.enrichment.umm.schema.validate_kms_keywords") as mock_kms_validate,
        ):
            mock_fetch.return_value = {"type": "object"}
            mock_schema_validate.return_value = []
            mock_kms_validate.return_value = [
                ValidationError(
                    path="$.Platforms[0].ShortName",
                    message="Invalid platform",
                    error_type="kms_invalid",
                )
            ]

            result = validate_metadata(metadata)

        assert not result.is_valid
        assert len(result.errors) == 1
        mock_schema_validate.assert_called_once()
        mock_kms_validate.assert_called_once()

    def test_skips_kms_validation_when_disabled(self):
        """Should skip KMS validation when validate_kms=False."""
        metadata = {
            "EntryTitle": "Test",
            "MetadataSpecification": {
                "URL": "https://cdn.earthdata.nasa.gov/umm/collection/v1.18.2"
            },
        }

        with (
            patch("lambdas.enrichment.umm.schema.fetch_schema") as mock_fetch,
            patch("lambdas.enrichment.umm.schema.validate_schema") as mock_schema_validate,
            patch("lambdas.enrichment.umm.schema.validate_kms_keywords") as mock_kms_validate,
        ):
            mock_fetch.return_value = {"type": "object"}
            mock_schema_validate.return_value = []

            result = validate_metadata(metadata, validate_kms=False)

        assert result.is_valid
        mock_kms_validate.assert_not_called()

    def test_returns_valid_for_clean_metadata(self):
        """Should return is_valid=True for valid metadata."""
        metadata = {
            "EntryTitle": "Valid Collection",
            "MetadataSpecification": {
                "URL": "https://cdn.earthdata.nasa.gov/umm/collection/v1.18.2"
            },
        }

        with (
            patch("lambdas.enrichment.umm.schema.fetch_schema") as mock_fetch,
            patch("lambdas.enrichment.umm.schema.validate_schema", return_value=[]),
            patch("lambdas.enrichment.umm.schema.validate_kms_keywords", return_value=[]),
        ):
            mock_fetch.return_value = {"type": "object"}

            result = validate_metadata(metadata)

        assert result.is_valid
        assert not result.errors
