"""UMM (Unified Metadata Model) utilities for validation and fixing."""

from lambdas.enrichment.models import (
    FixResult,
    RecommendationResult,
    ValidationError,
    ValidationResult,
)
from lambdas.enrichment.umm.recommend_keywords import (
    recommend_keyword,
    recommend_keywords_batch,
)
from lambdas.enrichment.umm.schema import (
    fetch_schema,
    get_science_keyword_levels,
    validate_metadata,
)

__all__ = [
    # Schema validation
    "ValidationError",
    "ValidationResult",
    "fetch_schema",
    "get_science_keyword_levels",
    "validate_metadata",
    # Fixer models
    "FixResult",
    # Keyword recommendations
    "RecommendationResult",
    "recommend_keyword",
    "recommend_keywords_batch",
]
