"""
Temporal constraint extraction with LLM parsing.
"""

import logging

from langfuse import observe

from models.tools.discover_data import ParsedTemporalExtraction, TemporalConstraint
from tools.discover_data.utils.llm_extraction import run_llm_extraction
from util.langfuse import trace_update

logger = logging.getLogger(__name__)


@observe(name="extract_temporal_constraint")
def extract_temporal_constraint(query: str) -> TemporalConstraint:
    """Extract temporal constraints from a natural language query.

    Args:
        query: Natural language description of a time period.

    Returns:
        TemporalConstraint with extracted start/end dates and reasoning.
    """
    if not query:
        logger.warning("Empty query provided for temporal constraint extraction.")
        return TemporalConstraint(
            start_date=None,
            end_date=None,
            reasoning="No temporal information found in query",
        )

    output = run_llm_extraction(
        query=query,
        prompt_filename="temporal_extraction.md",
        response_model=ParsedTemporalExtraction,
        extraction_label="temporal ranges",
    )

    trace_update(
        tags=["success", "temporal_extraction"],
        metadata={"success": True},
    )

    if output.start_date or output.end_date:
        logger.debug(
            "Extracted temporal constraint: start=%s, end=%s",
            output.start_date.isoformat() if output.start_date else None,
            output.end_date.isoformat() if output.end_date else None,
        )
    else:
        logger.debug("No temporal information extracted from query: %s", query)

    return TemporalConstraint(
        start_date=output.start_date,
        end_date=output.end_date,
        reasoning=output.reasoning,
    )
