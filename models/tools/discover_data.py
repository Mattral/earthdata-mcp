"""
Models for the discover_data orchestrator tool.

Includes constraint models, input/output schemas, extraction models,
and all supporting types for the discovery pipeline.
"""

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, computed_field


class TemporalConstraint(BaseModel):
    """Extracted or user-provided temporal constraint."""

    start_date: datetime | None = Field(None, description="Start of temporal range (inclusive)")
    end_date: datetime | None = Field(None, description="End of temporal range (inclusive)")
    reasoning: str | None = Field(
        None, description="Explanation of how the constraint was extracted"
    )


class SpatialConstraint(BaseModel):
    """Extracted or user-provided spatial constraint."""

    location: str | None = Field(None, description="Original location text from user query")
    wkt_geometry: str | None = Field(None, description="WKT representation of the spatial area")
    reasoning: str | None = Field(
        None, description="Explanation of how the constraint was extracted"
    )


class SearchContext(BaseModel):
    """
    Preserved context from previous search iteration.

    Allows the orchestrator to continue from where it left off
    without re-extracting constraints.
    """

    temporal: TemporalConstraint | None = Field(
        None, description="Previously extracted temporal constraint"
    )
    spatial: SpatialConstraint | None = Field(
        None, description="Previously extracted spatial constraint"
    )
    previous_collection_ids: list[str] = Field(
        default_factory=list, description="Collection IDs from previous search"
    )
    user_refinements: dict[str, str] = Field(
        default_factory=dict,
        description="User's answers to clarifying questions (question_id -> selected option)",
    )
    search_iteration: int = Field(default=0, description="Number of search iterations performed")


class DiscoverDataInput(BaseModel):
    """
    Input model for the discover_data orchestrator.

    Accepts natural language queries about earth science data,
    with optional explicit constraints and context for iterative refinement.

    **Constraint Priority:** Explicit constraints (temporal_constraint, spatial_constraint)
    take precedence over extraction from the query text.
    """

    query: str = Field(
        ...,
        description="Natural language query describing the earth science data needed. "
        "Can include topic, location, time period, instrument, or phenomenon.",
        examples=[
            "Sea surface temperature data for the Pacific Ocean during El Nino 2015-2016",
            "MODIS vegetation index for California fire season 2020",
            "High resolution precipitation data for monsoon season in India",
        ],
    )

    temporal_constraint: TemporalConstraint | None = Field(
        None,
        description="Explicit temporal constraint. If provided, skips temporal extraction from query.",
    )

    spatial_constraint: SpatialConstraint | None = Field(
        None,
        description="Explicit spatial constraint. If provided, skips spatial extraction from query.",
    )

    search_context: SearchContext | None = Field(
        None,
        description=(
            "For follow-up queries: Pass back the 'search_context' object from the prior response. "
            "Callers may modify specific fields: set temporal or spatial to null to force re-extraction "
            "when the user changes those constraints, or update user_refinements to answer clarifying questions. "
            "Otherwise, treat this as an opaque object - do not stringify or manually reconstruct it."
        ),
    )

    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of collections to return.",
    )

    similarity_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum combined score for collection matches. "
        "Collections are scored based on direct similarity and indirect signals "
        "from related entities (variables, instruments, citations, etc.).",
    )


class DiscoveryStatus(str, Enum):
    """Status of the discovery operation."""

    COLLECTIONS_FOUND = "collections_found"
    DISAMBIGUATION_NEEDED = "disambiguation_needed"
    INDIRECT_MATCHES = "indirect_matches"
    REFINEMENT_SUGGESTED = "refinement_suggested"
    NO_RESULTS = "no_results"
    NO_GRANULES_IN_CONSTRAINTS = "no_granules_in_constraints"
    ERROR = "error"


class ResolutionInfo(BaseModel):
    """Temporal and spatial resolution information for a collection."""

    temporal_resolution: str | None = Field(
        None, description="Temporal resolution (e.g., 'Daily', 'Monthly', '8 Day')"
    )
    temporal_resolution_value: float | None = Field(
        None, description="Temporal resolution as numeric value in days for comparison"
    )
    spatial_resolution: str | None = Field(
        None, description="Spatial resolution (e.g., '250m', '1km', '0.25 degree')"
    )
    spatial_resolution_value: float | None = Field(
        None, description="Spatial resolution as numeric value in meters for comparison"
    )


class TemporalCoverage(BaseModel):
    """Temporal coverage of a collection."""

    start_date: datetime | None = Field(None, description="Start of data coverage")
    end_date: datetime | None = Field(None, description="End of data coverage (None if ongoing)")
    is_ongoing: bool = Field(
        default=False, description="Whether the collection is still being updated"
    )


class CollectionMatch(BaseModel):
    """A matched collection with metadata and relevance info."""

    concept_id: str = Field(..., description="CMR concept ID")
    title: str = Field(..., description="Collection title")
    abstract: str | None = Field(None, description="Collection description/abstract")

    similarity_score: float = Field(
        ..., ge=0.0, le=1.0, description="Semantic similarity score (0-1)"
    )
    match_type: str = Field(
        ...,
        description="How this collection was found: 'direct', 'via_citation', "
        "'via_variable', 'via_science_keyword', 'via_instrument', 'via_platform'",
    )
    matched_attribute: str | None = Field(
        None, description="Which attribute matched (title, abstract, etc.)"
    )

    resolution: ResolutionInfo | None = Field(
        None, description="Resolution information for disambiguation"
    )
    temporal_coverage: TemporalCoverage | None = Field(
        None, description="Temporal coverage of the collection"
    )

    platforms: list[str] = Field(
        default_factory=list, description="Platform names (e.g., Terra, Aqua)"
    )
    instruments: list[str] = Field(
        default_factory=list, description="Instrument names (e.g., MODIS, ASTER)"
    )

    related_entity_id: str | None = Field(
        None,
        description="If indirect match, the ID of the entity that linked to this collection",
    )
    related_entity_text: str | None = Field(
        None, description="The text content of the related entity"
    )
    is_ongoing: bool = Field(
        default=False,
        description="Whether the collection is still actively collecting data",
    )
    granule_count: int | None = Field(
        None,
        description="Number of granules matching spatio-temporal constraints (None if not validated)",
    )


class ClarifyingQuestion(BaseModel):
    """A question to help the user refine their search.

    To respond: Copy search_context from this response, add the user's choice to
    search_context.user_refinements as {question_id: selected_option}, then call
    the tool again with the same query and updated search_context.
    """

    question_id: str | None = Field(
        None, description="Unique identifier for this question. Use as key in user_refinements."
    )
    question_text: str | None = Field(None, description="The question to present to the user")
    question_type: str | None = Field(
        None,
        description="Type: 'resolution_preference', 'temporal_scope', "
        "'spatial_scope', 'platform_preference', 'source_explanation', "
        "'data_type_preference', 'instrument_preference', 'variable_preference'",
    )
    options: list[str] = Field(
        default_factory=list,
        description="Valid choices for user_refinements. User picks one of these values.",
    )
    explanations: dict[str, str] | None = Field(
        None,
        description="Optional explanations for each option (from KMS definitions). "
        "Keys are option values, values are explanation text.",
    )
    recommendation: str | None = Field(
        None,
        description="Suggested choice based on user's query context, if applicable",
    )
    related_collection_ids: list[str] = Field(
        default_factory=list,
        description="Collection IDs this question helps disambiguate",
    )


class ExtractedConstraints(BaseModel):
    """Constraints extracted from the natural language query."""

    temporal_start: datetime | None = Field(None, description="Extracted start date")
    temporal_end: datetime | None = Field(None, description="Extracted end date")
    temporal_reasoning: str | None = Field(None, description="Explanation of temporal extraction")

    spatial_location: str | None = Field(None, description="Original location text from query")
    spatial_wkt: str | None = Field(None, description="WKT geometry for the spatial area")
    spatial_reasoning: str | None = Field(None, description="Explanation of spatial extraction")


class DiscoverDataOutput(BaseModel):
    """
    Output model for the discover_data orchestrator.

    Provides discovery results with support for clarifying questions
    and iterative refinement.
    """

    status: DiscoveryStatus = Field(..., description="Status of the discovery operation")

    collections: list[CollectionMatch] = Field(
        default_factory=list, description="Matched collections"
    )
    total_found: int = Field(default=0, description="Total number of matches found")

    clarifying_questions: list[ClarifyingQuestion] = Field(
        default_factory=list,
        description="Questions to help refine the search. To answer: add {question_id: selected_option} to search_context.user_refinements and call again.",
    )

    extracted_constraints: ExtractedConstraints | None = Field(
        None, description="Constraints extracted from the query"
    )

    search_context: SearchContext = Field(
        default_factory=SearchContext,
        description="Pass back unchanged on follow-up calls. To answer clarifying_questions, add choices to search_context.user_refinements first.",
    )

    error_message: str | None = Field(None, description="Error message if status is ERROR")

    search_strategy: str | None = Field(None, description="Description of the search strategy used")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "collections_found",
                    "collections": [
                        {
                            "concept_id": "C1234567890-PROVIDER",
                            "title": "MODIS/Terra Sea Surface Temperature Daily L2",
                            "similarity_score": 0.92,
                            "match_type": "direct",
                            "matched_attribute": "abstract",
                            "resolution": {
                                "temporal_resolution": "Daily",
                                "spatial_resolution": "1km",
                            },
                            "platforms": ["Terra"],
                            "instruments": ["MODIS"],
                        }
                    ],
                    "total_found": 1,
                    "clarifying_questions": [],
                    "extracted_constraints": {
                        "temporal_start": "2015-01-01T00:00:00Z",
                        "temporal_end": "2016-12-31T23:59:59Z",
                        "spatial_location": "Pacific Ocean",
                        "spatial_wkt": "POLYGON((...))...",
                    },
                },
                {
                    "status": "disambiguation_needed",
                    "collections": [],
                    "total_found": 4,
                    "clarifying_questions": [
                        {
                            "question_id": "temporal_res_123",
                            "question_text": "Multiple MODIS SST products found with different resolutions. Which do you prefer?",
                            "question_type": "resolution_preference",
                            "options": ["Daily", "8-Day", "Monthly"],
                            "explanations": None,
                            "recommendation": None,
                            "related_collection_ids": ["C123...", "C456...", "C789..."],
                        }
                    ],
                },
            ]
        }
    )


class ParsedTemporalExtraction(BaseModel):
    """Parsed temporal information extracted by LLM."""

    start_date: datetime | None = None
    end_date: datetime | None = None
    reasoning: str | None = None


class ParsedSpatialExtraction(BaseModel):
    """Parsed spatial information extracted by LLM with computed cache key."""

    location_name: str | None = None
    location_with_context: str | None = None
    reasoning: str | None = None

    @computed_field
    @property
    def cache_key(self) -> str | None:
        """Compute cache key from location_name."""
        if self.location_name:
            normalized = self.location_name.lower().strip()
            return f"geocode:{hashlib.sha256(normalized.encode()).hexdigest()}"
        return None
