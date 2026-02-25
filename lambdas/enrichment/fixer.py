"""
Enrichment step: fix — Fixes invalid KMS keywords via embedding similarity.

Fixes ONE validation error per invocation. The Step Function
handles the iterative loop: validate -> fix -> validate -> fix...

Only fixes kms_invalid errors (invalid KMS keywords). For these errors,
it finds the closest valid term via embedding similarity and replaces it,
or removes the invalid term when no suitable replacement exists.
"""

import copy
import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.models import FixResult, ValidationError
from lambdas.enrichment.payload import dehydrate_event, prepare_event
from lambdas.enrichment.umm.json_path import (
    get_value_at_path,
    remove_value_at_path,
    set_value_at_path,
)
from lambdas.enrichment.umm.recommend_keywords import recommend_keyword
from lambdas.enrichment.umm.schema import get_science_keyword_levels

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def _deserialize_validation_errors(errors_data: list[dict]) -> list[ValidationError]:
    """Convert serialized error dicts back to ValidationError objects."""
    errors = []
    for e in errors_data:
        errors.append(
            ValidationError(
                path=e["path"],
                message=e["message"],
                error_type=e["error_type"],
                value=e.get("value"),
                allowed_values=e.get("allowed_values"),
                schema_fragment=e.get("schema_fragment"),
            )
        )
    return errors


def fix_one_error(
    metadata: dict[str, Any],
    errors: list[ValidationError],
) -> tuple[dict[str, Any], FixResult | None]:
    """
    Fix ONE validation error in the metadata.

    Scans errors for the first fixable one (kms_invalid) and applies the fix.
    Skips error types we cannot fix. If no errors are fixable, returns a skip
    result so the caller can stop the loop.

    Args:
        metadata: UMM metadata dict (will be copied)
        errors: List of validation errors

    Returns:
        Tuple of (modified_metadata, fix_result)
        fix_result is None if no errors to process
    """
    if not errors:
        return metadata, None

    # Find the first fixable error
    error = None
    for e in errors:
        if e.error_type == "kms_invalid":
            error = e
            break

    if error is None:
        # No fixable errors remain — report the first one and signal done
        first = errors[0]
        unfixable_types = {e.error_type for e in errors}
        logger.info(
            "No fixable errors remain (%d errors, types: %s), stopping fix loop",
            len(errors),
            ", ".join(sorted(unfixable_types)),
        )
        return metadata, FixResult(
            success=False,
            action="skip",
            field_path=first.path,
            error=f"No fixable errors remain (types: {', '.join(sorted(unfixable_types))})",
        )

    # Make a copy to avoid modifying the original
    metadata = copy.deepcopy(metadata)

    logger.info(
        "Fixing KMS keyword error at %s (type: %s)",
        error.path,
        error.error_type,
    )

    try:
        result = _apply_keyword_recommendation(metadata, error)
    except Exception as e:
        logger.exception("Keyword recommendation failed for %s", error.path)
        result = FixResult(
            success=False,
            action="recommend_keyword",
            field_path=error.path,
            error=str(e),
        )

    if result.success:
        logger.info(
            "Fixed %s: %s -> %s",
            error.path,
            result.old_value,
            result.new_value,
        )
    else:
        logger.warning("Failed to fix %s: %s", error.path, result.error)

    return metadata, result


def _parse_array_index(path: str) -> int | None:
    """Extract the first array index from a path like ``$.ScienceKeywords[2]``
    or ``$.ScienceKeywords[2].Category``."""
    start = path.find("[")
    if start == -1:
        return None
    end = path.find("]", start)
    if end == -1:
        return None
    try:
        return int(path[start + 1 : end])
    except ValueError:
        return None


def _apply_keyword_recommendation(  # pylint: disable=too-many-return-statements,too-many-branches
    metadata: dict[str, Any], error: ValidationError
) -> FixResult:
    """
    Apply keyword recommendation for invalid KMS terms.

    Uses embedding similarity to find the closest valid replacement.
    If no close match exists, removes the term only for science keywords.
    """
    term = error.value
    scheme = None
    if error.schema_fragment and "scheme" in error.schema_fragment:
        scheme = error.schema_fragment["scheme"]
    else:
        # Fallback: infer KMS scheme from the JSON path when schema_fragment
        # doesn't include it. Check "instrument" before "platform" because
        # instrument paths are nested under platforms in UMM-C
        # (e.g. $.Platforms[0].Instruments[0].ShortName).
        path_lower = error.path.lower()
        if "sciencekeyword" in path_lower:
            scheme = "sciencekeywords"
        elif "instrument" in path_lower:
            scheme = "instruments"
        elif "platform" in path_lower:
            scheme = "platforms"

    if not term or not scheme:
        return FixResult(
            success=False,
            action="recommend_keyword",
            field_path=error.path,
            error="Could not extract term or scheme from error",
        )

    keyword_context = None
    keyword_levels = None
    if scheme == "sciencekeywords":
        idx = _parse_array_index(error.path)
        keywords = metadata.get("ScienceKeywords")
        if idx is not None and keywords is not None and idx < len(keywords):
            keyword_context = keywords[idx]
        keyword_levels = get_science_keyword_levels(metadata)

    recommendation = recommend_keyword(
        term, scheme, keyword_context=keyword_context, keyword_levels=keyword_levels
    )

    # If the recommendation is the same term we started with, the term is
    # likely valid but wasn't found due to a stale KMS cache. Replacing a
    # term with itself is a no-op that wastes a fix iteration, so skip it.
    if (
        recommendation.action == "replace"
        and recommendation.recommended_term
        and recommendation.recommended_term.upper() == term.upper()
    ):
        logger.info(
            "Skipping no-op fix at %s: recommended term '%s' matches original (similarity: %.3f)",
            error.path,
            term,
            recommendation.similarity,
        )
        return FixResult(
            success=True,
            action="recommend_keyword",
            field_path=error.path,
            old_value=term,
            new_value=term,
            notes=f"Skipped no-op replacement (term matches, similarity: {recommendation.similarity:.3f})",
        )

    # ScienceKeyword handling: replace targets the leaf level and remove
    # pops the whole entry from the array.
    if scheme == "sciencekeywords":
        if recommendation.action == "replace" and recommendation.recommended_term:
            # Determine the field path to replace: use leaf_level from the
            # grouped validation error when available, otherwise the error
            # path already points at the specific field.
            leaf_level = (error.schema_fragment or {}).get("leaf_level")
            leaf_path = f"{error.path}.{leaf_level}" if leaf_level else error.path
            old_value = get_value_at_path(metadata, leaf_path)

            if set_value_at_path(metadata, leaf_path, recommendation.recommended_term):
                return FixResult(
                    success=True,
                    action="recommend_keyword",
                    field_path=error.path,
                    old_value=old_value,
                    new_value=recommendation.recommended_term,
                    notes=f"Replaced with similar term (similarity: {recommendation.similarity:.3f})",
                )
            return FixResult(
                success=False,
                action="recommend_keyword",
                field_path=error.path,
                old_value=old_value,
                error="Failed to set replacement value",
            )

        # Remove the entire keyword entry from the array. We use .pop()
        # instead of remove_value_at_path because we need to drop the whole
        # array element, not just clear a field within it.
        idx = _parse_array_index(error.path)
        keywords = metadata.get("ScienceKeywords")
        if idx is not None and keywords is not None and idx < len(keywords):
            # ScienceKeywords is required in UMM-C — never remove the last one.
            # Use the best candidate as a fallback replacement instead.
            if len(keywords) == 1:
                best = recommendation.best_candidate
                if best:
                    leaf_level = (error.schema_fragment or {}).get("leaf_level")
                    leaf_path = f"{error.path}.{leaf_level}" if leaf_level else error.path
                    old_value = get_value_at_path(metadata, leaf_path)
                    set_value_at_path(metadata, leaf_path, best)
                    return FixResult(
                        success=True,
                        action="recommend_keyword",
                        field_path=error.path,
                        old_value=old_value,
                        new_value=best,
                        notes=(
                            f"Replaced with best candidate '{best}' (similarity: "
                            f"{recommendation.similarity:.3f}) to preserve required ScienceKeywords"
                        ),
                    )
                logger.warning(
                    "Cannot remove last ScienceKeyword at %s and no candidate available",
                    error.path,
                )
                return FixResult(
                    success=False,
                    action="recommend_keyword",
                    field_path=error.path,
                    error="Cannot remove last ScienceKeyword (required field) and no replacement available",
                )

            old_value = keywords.pop(idx)
            return FixResult(
                success=True,
                action="recommend_keyword",
                field_path=error.path,
                old_value=old_value,
                new_value=None,
                notes=(
                    f"Removed invalid {scheme} entry "
                    f"(best candidate: '{recommendation.best_candidate}', "
                    f"similarity: {recommendation.similarity:.3f} below threshold)"
                ),
            )
        return FixResult(
            success=False,
            action="recommend_keyword",
            field_path=error.path,
            error=f"Failed to remove invalid {scheme} entry at index {idx}",
        )

    # Field-level handling (platforms, instruments)
    if recommendation.action == "replace" and recommendation.recommended_term:
        old_value = get_value_at_path(metadata, error.path)

        if set_value_at_path(metadata, error.path, recommendation.recommended_term):
            return FixResult(
                success=True,
                action="recommend_keyword",
                field_path=error.path,
                old_value=old_value,
                new_value=recommendation.recommended_term,
                notes=f"Replaced with similar term (similarity: {recommendation.similarity:.3f})",
            )
        return FixResult(
            success=False,
            action="recommend_keyword",
            field_path=error.path,
            old_value=old_value,
            error="Failed to set replacement value",
        )

    # No close replacement found — remove the invalid term from enriched metadata.
    # The raw metadata is preserved, so we can revisit later. Enriched metadata
    # must be clean for embedding generation; invalid KMS terms have no definition
    # to embed and would waste tokens.
    success, old_value = remove_value_at_path(metadata, error.path)

    if success:
        return FixResult(
            success=True,
            action="recommend_keyword",
            field_path=error.path,
            old_value=old_value,
            new_value=None,
            notes=(
                f"Removed invalid {scheme} term "
                f"(best candidate: '{recommendation.best_candidate}', "
                f"similarity: {recommendation.similarity:.3f} below threshold)"
            ),
        )
    return FixResult(
        success=False,
        action="recommend_keyword",
        field_path=error.path,
        error=f"Failed to remove invalid {scheme} term",
    )


def fix_one(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Fix ONE validation error.

    Note: metadata and enriched_metadata are offloaded to Redis between
    steps (see payload.py). The shapes below show the hydrated view.

    Input:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection",
            "metadata": { ... raw metadata ... },
            "enriched_metadata": { ... current working copy ... },
            "validation": {
                "is_valid": false,
                "errors": [...]
            },
            "fix_attempt": 0,
            "fix_history": []
        }

    Output:
        {
            ... pass through all fields ...,
            "enriched_metadata": { ... with one fix applied ... },
            "fix_attempt": 1,
            "fix_history": [ ... with new fix result ... ],
            "last_fix": {
                "success": bool,
                "action": str,
                "field_path": str,
                ...
            }
        }
    """
    event, concept_id, metadata = prepare_event(event)
    fix_attempt = event.get("fix_attempt", 0)
    fix_history = event.get("fix_history", [])

    # Get validation errors
    validation = event.get("validation", {})
    errors_data = validation.get("errors", [])

    if not errors_data:
        logger.info("No errors to fix for %s", concept_id)
        return dehydrate_event(
            {
                **event,
                "enriched_metadata": metadata,
                "fix_attempt": fix_attempt,
                "last_fix": None,
            }
        )

    errors = _deserialize_validation_errors(errors_data)
    logger.info(
        "Attempting to fix error %d/%d for %s (attempt %d)",
        1,
        len(errors),
        concept_id,
        fix_attempt + 1,
    )

    try:
        fixed_metadata, result = fix_one_error(
            metadata=metadata,
            errors=errors,
        )

        # Serialize the fix result
        fix_result = None
        if result:
            fix_result = {
                "success": result.success,
                "action": result.action,
                "field_path": result.field_path,
                "old_value": result.old_value,
                "new_value": result.new_value,
                "error": result.error,
                "notes": result.notes,
            }
            fix_history = fix_history + [fix_result]

            if result.success:
                logger.info(
                    "Successfully fixed %s using %s",
                    result.field_path,
                    result.action,
                )
            else:
                logger.warning(
                    "Failed to fix %s: %s",
                    result.field_path,
                    result.error,
                )

        return dehydrate_event(
            {
                **event,
                "enriched_metadata": fixed_metadata,
                "fix_attempt": fix_attempt + 1,
                "fix_history": fix_history,
                "last_fix": fix_result,
            }
        )

    except Exception as e:
        logger.exception("Fixer agent failed for %s", concept_id)
        return dehydrate_event(
            {
                **event,
                "enriched_metadata": metadata,
                "fix_attempt": fix_attempt + 1,
                "fix_history": fix_history,
                "last_fix": {
                    "success": False,
                    "action": "unknown",
                    "field_path": None,
                    "error": str(e),
                },
            }
        )


@observe(name="enrichment:fixer")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the fixer step."""
    return fix_one(event, context)
