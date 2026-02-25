"""
Enrichment step: embed — Creates embeddings from enriched metadata.

Final step in the enrichment pipeline. Generates embeddings from the
enriched_metadata so that corrected values are used for search.
"""

import logging
from typing import Any

from langfuse import observe

from lambdas.enrichment.payload import dehydrate_event, prepare_event
from models.cmr import ConceptMessage, EmbeddingChunk, KMSTerm
from util.cmr import extract_data, fetch_associations
from util.datastores import get_datastore
from util.embeddings import EmbeddingError, get_embedding_generator
from util.kms import lookup_terms

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def embed_chunks(
    chunks: list[EmbeddingChunk],
    embedder: Any,
    existing_chunks: dict[str, tuple[str, list[float]]] | None = None,
) -> list[tuple[str, str, list[float]]]:
    """
    Generate embeddings for text chunks, reusing existing embeddings when text is unchanged.

    Args:
        chunks: List of EmbeddingChunk objects to embed.
        embedder: Embedding generator.
        existing_chunks: Dict mapping attribute to (text_content, embedding) from the datastore.
            If an attribute's text_content matches, the existing embedding is reused.

    Returns list of (attribute, text_content, embedding) tuples ready for storage.
    """
    if existing_chunks is None:
        existing_chunks = {}

    results = []
    for chunk in chunks:
        # Reuse existing embedding if text content hasn't changed
        if chunk.attribute in existing_chunks:
            existing_text, existing_embedding = existing_chunks[chunk.attribute]
            if existing_text == chunk.text_content:
                results.append((chunk.attribute, chunk.text_content, existing_embedding))
                continue

        embedding = embedder.generate(
            chunk.text_content,
            concept_type=chunk.concept_type,
            attribute=chunk.attribute,
            metadata={
                "embedding_type": "cmr_concept",
                "concept_id": chunk.concept_id,
                "concept_type": chunk.concept_type.value,
                "attribute": chunk.attribute,
            },
        )
        results.append((chunk.attribute, chunk.text_content, embedding))
    return results


def process_kms_terms(
    kms_terms: list[KMSTerm],
    datastore: Any,
    embedder: Any,
) -> list[tuple[str, str]]:
    """
    Look up KMS terms, embed new ones, return refs for associations.

    Returns:
        List of (kms_uuid, scheme) tuples to link to the concept
    """
    # Dedupe terms
    unique_terms = {}
    for ref in kms_terms:
        key = (ref.term, ref.scheme)
        if key not in unique_terms:
            unique_terms[key] = ref

    if not unique_terms:
        return []

    # Batch lookup all terms
    lookup_results = lookup_terms(list(unique_terms.keys()))

    # Process results
    kms_refs = []
    for kms_term in lookup_results.values():
        if kms_term is None:
            continue

        # If embedding already exists, add to associations list
        if datastore.get_kms_embedding(kms_term.uuid):
            kms_refs.append((kms_term.uuid, kms_term.scheme))
            continue

        # Embed the term
        text = f"{kms_term.term}: {kms_term.definition}" if kms_term.definition else kms_term.term

        try:
            embedding = embedder.generate(
                text,
                concept_type="kms_term",
                attribute=kms_term.scheme,
                metadata={
                    "embedding_type": "kms_term",
                    "kms_uuid": kms_term.uuid,
                    "kms_term": kms_term.term,
                    "kms_scheme": kms_term.scheme,
                },
            )
        except EmbeddingError as e:
            logger.warning("Failed to embed KMS term %s: %s", kms_term.term, e)
            continue

        inserted = datastore.upsert_kms_embedding(
            kms_uuid=kms_term.uuid,
            scheme=kms_term.scheme,
            term=kms_term.term,
            definition=kms_term.definition,
            embedding=embedding,
        )

        if inserted or datastore.get_kms_embedding(kms_term.uuid):
            kms_refs.append((kms_term.uuid, kms_term.scheme))

    return kms_refs


def embed(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Create embeddings from enriched metadata.

    Final step in the enrichment pipeline. Both valid and invalid records
    get embedded using their enriched_metadata so that any corrections
    are reflected in search results.

    Note: metadata and enriched_metadata are offloaded to Redis between
    steps (see payload.py). The shapes below show the hydrated view.

    Input:
        {
            "concept_id": "C1234-PROVIDER",
            "revision_id": 5,
            "concept_type": "collection",
            "metadata": { ... raw metadata ... },
            "enriched_metadata": { ... enriched metadata ... },
            "validation": {...},
            "store_result": {...}
        }

    Output:
        {
            ... pass through all fields ...,
            "embed_result": {
                "success": bool,
                "chunks_stored": int,
                "kms_terms_linked": int
            }
        }
    """
    event, concept_id, enriched_metadata = prepare_event(event)
    revision_id = event["revision_id"]
    concept_type = event.get("concept_type", "collection")

    logger.info("Creating embeddings for %s (using enriched metadata)", concept_id)

    datastore = get_datastore()
    embedder = get_embedding_generator()

    try:
        # Build a ConceptMessage for the extraction function
        message = ConceptMessage(
            action="concept-update",
            concept_type=concept_type,
            concept_id=concept_id,
            revision_id=revision_id,
        )

        # Extract chunks and KMS terms from enriched metadata
        extraction = extract_data(message, enriched_metadata)
        logger.info(
            "Extracted %d chunks, %d KMS terms from enriched %s",
            len(extraction.chunks),
            len(extraction.kms_terms),
            concept_id,
        )

        # Fetch existing chunks to enable diff-based upsert and skip unchanged embeddings
        existing_chunks = datastore.get_chunks_for_entity(concept_id, concept_type)

        # Generate and store embeddings (reuses existing embeddings for unchanged text)
        embedded = embed_chunks(extraction.chunks, embedder, existing_chunks=existing_chunks)

        datastore.upsert_chunks(concept_type, concept_id, embedded)

        # Process KMS terms
        kms_refs = process_kms_terms(extraction.kms_terms, datastore, embedder)

        # Always call upsert so stale associations get cleaned up
        datastore.upsert_kms_associations(concept_type, concept_id, kms_refs)

        # For collections, also store links to associated variables/citations
        if concept_type == "collection":
            associations = fetch_associations(concept_id)

            # Always call upsert so stale associations get cleaned up
            datastore.upsert_associations(concept_type, concept_id, associations)

        logger.info(
            "Embedded %s: %d chunks, %d KMS terms",
            concept_id,
            len(embedded),
            len(kms_refs),
        )

        return dehydrate_event(
            {
                **event,
                "embed_result": {
                    "success": True,
                    "chunks_stored": len(embedded),
                    "kms_terms_linked": len(kms_refs),
                },
            }
        )

    except Exception as e:
        logger.exception("Failed to embed %s", concept_id)

        return dehydrate_event(
            {
                **event,
                "embed_result": {
                    "success": False,
                    "chunks_stored": 0,
                    "kms_terms_linked": 0,
                    "error": str(e),
                },
            }
        )


@observe(name="enrichment:embed")
def handle(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the embed step."""
    return embed(event, context)
