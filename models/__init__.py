"""Centralized data models for the earthdata-mcp project."""

from models.cmr import (
    CollectionData,
    ConceptMessage,
    ConceptType,
    EmbeddingChunk,
    ExtractionResult,
    KMSTerm,
)
from models.tools.discover_data import (
    ClarifyingQuestion,
    CollectionMatch,
    DiscoverDataInput,
    DiscoverDataOutput,
    DiscoveryStatus,
    ExtractedConstraints,
    ParsedSpatialExtraction,
    ParsedTemporalExtraction,
    ResolutionInfo,
    SearchContext,
    SpatialConstraint,
    TemporalConstraint,
    TemporalCoverage,
)

__all__ = [
    # CMR models
    "CollectionData",
    "ConceptMessage",
    "ConceptType",
    "EmbeddingChunk",
    "ExtractionResult",
    "KMSTerm",
    # discover_data tool models
    "ClarifyingQuestion",
    "CollectionMatch",
    "DiscoverDataInput",
    "DiscoverDataOutput",
    "DiscoveryStatus",
    "ExtractedConstraints",
    "ParsedSpatialExtraction",
    "ParsedTemporalExtraction",
    "ResolutionInfo",
    "SearchContext",
    "SpatialConstraint",
    "TemporalConstraint",
    "TemporalCoverage",
]
