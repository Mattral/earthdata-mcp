"""Shared LLM extraction utilities for temporal and spatial constraints.

Consolidates common patterns used by both constraint extraction modules
to avoid code duplication.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import instructor
from pydantic import BaseModel  # pylint: disable=unused-import  # used in PEP 695 type bound

from util.langfuse import trace_update

logger = logging.getLogger(__name__)

PROVIDER = "bedrock"
MODEL_ID = "amazon.nova-pro-v1:0"


def load_extraction_prompt(prompt_filename: str, current_date: str) -> str:
    """
    Load and prepare an extraction prompt from the prompts directory.

    Args:
        prompt_filename: Filename in tools/discover_data/utils/prompts/ (e.g., "spatial_extraction.md")
        current_date: Current date string to replace in prompt (format: "YYYY-MM-DD")

    Returns:
        Prepared prompt text with {current_date} placeholder replaced

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = Path(__file__).parent / "prompts" / prompt_filename

    if not prompt_path.exists():
        raise FileNotFoundError(f"Required prompt file not found: {prompt_path}")

    with open(prompt_path, encoding="utf-8") as f:
        return f.read().replace("{current_date}", current_date)


def run_llm_extraction[T: BaseModel](  # pylint: disable=undefined-variable,unused-variable
    query: str,
    prompt_filename: str,
    response_model: type[T],  # pylint: disable=undefined-variable
    extraction_label: str,
) -> T:  # pylint: disable=undefined-variable
    """Run an LLM extraction with standard client init, prompting, and error handling.

    Args:
        query: The user query to extract information from.
        prompt_filename: Prompt file in the prompts/ directory.
        response_model: Pydantic model for structured output.
        extraction_label: Human-readable label for error messages (e.g. "temporal ranges").

    Returns:
        Parsed response model instance.

    Raises:
        RuntimeError: On client init failure or LLM call failure.
    """
    try:
        client = instructor.from_provider(f"{PROVIDER}/{MODEL_ID}")
    except Exception as exc:
        trace_update(
            tags=["error", "client_init_error"],
            metadata={"error_type": "client_init_error", "message": str(exc), "success": False},
        )
        raise RuntimeError(
            f"Failed to initialize instructor client with provider '{PROVIDER}' "
            f"and model '{MODEL_ID}': {exc}"
        ) from exc

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    system_prompt = load_extraction_prompt(prompt_filename, today)

    try:
        return client.create(
            modelId=MODEL_ID,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            response_model=response_model,
        )
    except Exception as exc:
        trace_update(
            tags=["error", "llm_error"],
            metadata={"error_type": "llm_error", "message": str(exc), "success": False},
        )
        raise RuntimeError(
            f"Failed to extract {extraction_label} from query '{query}': {exc}"
        ) from exc
