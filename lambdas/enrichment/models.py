"""Data models for UMM (Unified Metadata Model) validation and fixing."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationError:
    """A single validation error with context for fixing."""

    path: str  # JSON path like "$.Platforms[0].Type"
    message: str  # Human-readable error message
    error_type: str  # "enum", "required", "type", "pattern", "kms_invalid", etc.
    value: Any = None  # The invalid value (if available)
    allowed_values: list | None = None  # For enum errors
    schema_fragment: dict | None = None  # Relevant schema portion


@dataclass
class ValidationResult:
    """Result of validating UMM metadata."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    schema_url: str | None = None


@dataclass
class FixResult:
    """Result of applying a fix to metadata."""

    success: bool
    action: str  # Tool name that was used
    field_path: str  # JSON path that was modified
    old_value: Any = None
    new_value: Any = None
    error: str | None = None
    notes: str | None = None


@dataclass
class RecommendationResult:
    """Result of a keyword recommendation."""

    recommended_term: str | None
    similarity: float
    action: str  # "replace" or "remove"
    original_term: str
    scheme: str
    best_candidate: str | None = None  # Best match even when below threshold
    alternatives: list[dict[str, Any]] | None = None
