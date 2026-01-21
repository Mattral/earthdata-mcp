"""
Extraction functions for CMR concept metadata.

Extracts text chunks and KMS term references from UMM metadata
for collections, variables, and citations.
"""

from typing import Any

from util.cmr.client import CMRError
from util.models import ConceptMessage, EmbeddingChunk, ExtractionResult, KMSTerm

# Field mappings: UMM field names -> attribute names for each concept type
COLLECTION_FIELDS = {"EntryTitle": "title", "Abstract": "abstract"}

VARIABLE_FIELDS = {
    "Definition": "definition",
}

CITATION_FIELDS = {
    "Name": "title",
    "Abstract": "abstract",
}


MIN_TEXT_LENGTH = 200  # Skip text too short to embed meaningfully


def extract_text_chunks(
    concept_type: str,
    concept_id: str,
    metadata: dict[str, Any],
    field_map: dict[str, str],
) -> list[EmbeddingChunk]:
    """
    Extract text fields from metadata based on field mapping.

    Skips fields with text shorter than MIN_TEXT_LENGTH characters.

    Args:
        concept_type: Type of concept (collection, variable, citation)
        concept_id: CMR concept ID
        metadata: Raw UMM metadata from CMR
        field_map: Maps UMM field names -> attribute names

    Returns:
        List of EmbeddingChunk for each field with sufficient text
    """
    chunks = []
    for umm_field, attribute in field_map.items():
        text = metadata.get(umm_field)
        if text and len(text.strip()) >= MIN_TEXT_LENGTH:
            chunks.append(
                EmbeddingChunk(
                    concept_type=concept_type,
                    concept_id=concept_id,
                    attribute=attribute,
                    text_content=text,
                )
            )
    return chunks


def extract_science_keywords(metadata: dict[str, Any]) -> list[KMSTerm]:
    """
    Extract science keyword terms from UMM metadata.

    Science keywords are hierarchical (Category > Topic > Term > VariableLevel1-3).
    We extract the most specific level available for each keyword.
    """
    terms = []
    for kw in metadata.get("ScienceKeywords") or []:
        term = (
            kw.get("VariableLevel3")
            or kw.get("VariableLevel2")
            or kw.get("VariableLevel1")
            or kw.get("Term")
        )
        if term:
            terms.append(KMSTerm(term=term, scheme="sciencekeywords"))
    return terms


def extract_platforms_and_instruments(metadata: dict[str, Any]) -> list[KMSTerm]:
    """Extract platform and instrument terms from collection metadata."""
    terms = []
    for platform in metadata.get("Platforms") or []:
        if name := platform.get("ShortName"):
            terms.append(KMSTerm(term=name, scheme="platforms"))

        for instrument in platform.get("Instruments") or []:
            if name := instrument.get("ShortName"):
                terms.append(KMSTerm(term=name, scheme="instruments"))
    return terms


def extract_from_collection(concept_id: str, metadata: dict[str, Any]) -> ExtractionResult:
    """Extract embeddable data from a collection's UMM-C metadata."""
    chunks = extract_text_chunks("collection", concept_id, metadata, COLLECTION_FIELDS)
    kms_terms = extract_science_keywords(metadata) + extract_platforms_and_instruments(metadata)
    return ExtractionResult(chunks=chunks, kms_terms=kms_terms)


def extract_from_variable(concept_id: str, metadata: dict[str, Any]) -> ExtractionResult:
    """Extract embeddable data from a variable's UMM-Var metadata."""
    chunks = extract_text_chunks("variable", concept_id, metadata, VARIABLE_FIELDS)
    kms_terms = extract_science_keywords(metadata)
    return ExtractionResult(chunks=chunks, kms_terms=kms_terms)


def extract_from_citation(concept_id: str, metadata: dict[str, Any]) -> ExtractionResult:
    """Extract embeddable data from a citation (name and abstract only)."""
    chunks = extract_text_chunks("citation", concept_id, metadata, CITATION_FIELDS)
    return ExtractionResult(chunks=chunks, kms_terms=[])


def extract_data(message: ConceptMessage, metadata: dict[str, Any]) -> ExtractionResult:
    """Route to the appropriate extractor based on concept type."""
    extractors = {
        "collection": extract_from_collection,
        "variable": extract_from_variable,
        "citation": extract_from_citation,
    }
    extractor = extractors[message.concept_type]
    return extractor(message.concept_id, metadata)


def extract_concept_info(concept_type: str, item: dict[str, Any]) -> dict[str, Any]:
    """
    Extract concept ID and revision ID from a CMR search result item.

    Args:
        concept_type: Type of concept
        item: CMR item from search results

    Returns:
        Dictionary with concept-type, concept-id, revision-id, action

    Raises:
        CMRError: If concept-id or revision-id is missing.
    """
    meta = item.get("meta", {})
    concept_id = meta.get("concept-id")
    revision_id = meta.get("revision-id")

    if not concept_id or not revision_id:
        raise CMRError(f"Missing concept-id or revision-id in item: {meta}")

    return {
        "concept-type": concept_type,
        "concept-id": concept_id,
        "revision-id": revision_id,
        "action": "concept-update",
    }
