"""Embedding-based keyword recommendation for invalid KMS terms.

This module provides a mechanism to find valid KMS keyword replacements
for invalid terms using embedding similarity search.

Designed to be replaceable with GKR (GCMD Keyword Recommender) in the future.
"""

import logging

from lambdas.enrichment.models import RecommendationResult
from util.datastores import EmbeddingDatastore, get_datastore
from util.embeddings import EmbeddingGenerator, get_embedding_generator
from util.kms import lookup_term

logger = logging.getLogger(__name__)

# Default similarity threshold for recommendations
DEFAULT_THRESHOLD = 0.85


def _extract_term_name(text_content: str) -> str:
    """Extract the term name from stored text_content (strips ': definition' suffix)."""
    if ":" in text_content:
        return text_content.split(":")[0].strip()
    return text_content


def recommend_keyword(  # pylint: disable=too-many-arguments
    term: str,
    scheme: str,
    threshold: float = DEFAULT_THRESHOLD,
    datastore: EmbeddingDatastore | None = None,
    embedding_generator: EmbeddingGenerator | None = None,
    keyword_context: dict[str, str] | None = None,
    keyword_levels: list[str] | None = None,
) -> RecommendationResult:
    """
    Find a replacement for an invalid KMS term using embedding similarity.

    This function searches for similar valid KMS terms that have already been
    embedded in the database. It generates an embedding for the invalid term
    and finds the most similar valid terms.

    Args:
        term: The invalid KMS term to find a replacement for
        scheme: KMS scheme ("sciencekeywords", "platforms", "instruments")
        threshold: Minimum similarity score to accept a replacement
        datastore: Optional datastore instance (uses default if not provided)
        embedding_generator: Optional embedding generator (uses default if not provided)
        keyword_context: Optional dict of science keyword hierarchy levels
            (e.g. {"Category": "EARTH SCIENCE", "Topic": "CRYOSPHERE", "Term": "ICE DEPTH/THICKNESS"}).
            When provided, the full hierarchy is used as query text instead of a KMS lookup.
        keyword_levels: Optional ordered list of hierarchy level names from the UMM
            schema (e.g. ``["Category", "Topic", "Term", ...]``).  When provided,
            these levels control which keys of *keyword_context* are joined into the
            enriched query.  When ``None``, hierarchy enrichment from *keyword_context*
            is skipped and the bare term is used.

    Returns:
        RecommendationResult with recommended replacement or "remove" action

    Note:
        This implementation uses embedding similarity search. When GKR
        (GCMD Keyword Recommender) is available, this function can be
        updated to call GKR instead while maintaining the same interface.
    """
    if datastore is None:
        datastore = get_datastore()

    if embedding_generator is None:
        embedding_generator = get_embedding_generator()

    # Enrich the query text so the embedding better matches stored KMS
    # embeddings (which use "TERM: definition").  For science keywords the
    # full hierarchy is available in keyword_context — use it directly
    # (the term is already known invalid so a KMS lookup would fail).
    # For platforms/instruments, fall back to the KMS definition lookup.
    query_text = term
    if keyword_context and keyword_levels:
        parts = [keyword_context[lvl] for lvl in keyword_levels if keyword_context.get(lvl)]
        if len(parts) > 1:
            query_text = " > ".join(parts)
            logger.debug("Enriched query for '%s' with hierarchy: %s", term, query_text)
    elif not keyword_context:
        try:
            kms_result = lookup_term(term, scheme)
            if kms_result and kms_result.definition:
                query_text = f"{term}: {kms_result.definition}"
                logger.debug("Enriched query for '%s' with KMS definition", term)
        except Exception as e:
            logger.debug("KMS lookup failed for '%s', using plain term: %s", term, e)

    # Generate embedding for the (possibly enriched) term
    try:
        embedding = embedding_generator.generate(
            query_text,
            concept_type="kms_term",
            attribute=scheme,
            metadata={
                "embedding_type": "keyword_recommendation",
                "original_term": term,
                "scheme": scheme,
            },
        )
    except Exception as e:
        logger.warning("Failed to generate embedding for term '%s': %s", term, e)
        return RecommendationResult(
            recommended_term=None,
            similarity=0.0,
            action="remove",
            original_term=term,
            scheme=scheme,
        )

    # Search for similar valid KMS terms
    # The entity_type filters to only search within the same KMS scheme
    results = datastore.search_similar(
        embedding=embedding,
        limit=5,
        entity_type=scheme,
    )

    if not results:
        logger.info("No similar terms found for '%s' in scheme '%s'", term, scheme)
        return RecommendationResult(
            recommended_term=None,
            similarity=0.0,
            action="remove",
            original_term=term,
            scheme=scheme,
        )

    # Log all candidates so we can diagnose bad matches
    best_match = results[0]
    similarity = best_match["similarity"]
    candidates = [
        {"term": _extract_term_name(r["text_content"]), "similarity": r["similarity"]}
        for r in results
    ]

    logger.info(
        "Candidates for '%s' in %s (threshold %.2f): %s",
        term,
        scheme,
        threshold,
        ", ".join(f"'{c['term']}' ({c['similarity']:.3f})" for c in candidates),
    )

    alternatives = candidates[1:] if len(candidates) > 1 else None
    recommended = _extract_term_name(best_match["text_content"])

    if similarity >= threshold:
        logger.info(
            "Recommending '%s' as replacement for '%s' (similarity: %.3f)",
            recommended,
            term,
            similarity,
        )

        return RecommendationResult(
            recommended_term=recommended,
            similarity=similarity,
            action="replace",
            original_term=term,
            scheme=scheme,
            alternatives=alternatives,
        )

    logger.info(
        "No candidate met threshold for '%s': best is '%s' (%.3f < %.2f)",
        term,
        recommended,
        similarity,
        threshold,
    )

    return RecommendationResult(
        recommended_term=None,
        similarity=similarity,
        action="remove",
        original_term=term,
        scheme=scheme,
        best_candidate=recommended,
        alternatives=alternatives,
    )


def recommend_keywords_batch(
    terms: list[tuple[str, str]],
    threshold: float = DEFAULT_THRESHOLD,
    datastore: EmbeddingDatastore | None = None,
    embedding_generator: EmbeddingGenerator | None = None,
) -> dict[tuple[str, str], RecommendationResult]:
    """
    Find replacements for multiple invalid KMS terms.

    Args:
        terms: List of (term, scheme) tuples
        threshold: Minimum similarity score to accept a replacement
        datastore: Optional datastore instance
        embedding_generator: Optional embedding generator

    Returns:
        Dict mapping (term, scheme) to RecommendationResult
    """
    if datastore is None:
        datastore = get_datastore()

    if embedding_generator is None:
        embedding_generator = get_embedding_generator()

    results = {}
    for term, scheme in terms:
        results[(term, scheme)] = recommend_keyword(
            term=term,
            scheme=scheme,
            threshold=threshold,
            datastore=datastore,
            embedding_generator=embedding_generator,
        )

    return results
