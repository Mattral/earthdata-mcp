"""Enrichment pipeline router handler.

Single Lambda that dispatches to step handlers based on the 'action' field
injected by the Step Function via Parameters.

Each step module exposes a handle() function decorated with @observe,
which automatically nests under the Langfuse trace created here.
The action name maps directly to the module name under lambdas.enrichment.
"""

import importlib
import logging

from util.langfuse import flush_langfuse, get_langfuse

logger = logging.getLogger(__name__)

# Fields to exclude from trace output (large or not useful)
_TRACE_EXCLUDE_FIELDS = {"metadata", "enriched_metadata", "fix_history"}


def _extract_trace_output(result: dict) -> dict:
    """Extract step-specific output for the Langfuse trace.

    Strips large/offloaded fields and returns only the interesting
    step results (validation summary, fix details, url_fix counts, etc.).
    """
    return {k: v for k, v in result.items() if k not in _TRACE_EXCLUDE_FIELDS}


def handler(event, context):
    """Route to the appropriate sub-handler based on the action field."""
    action = event.get("action")

    if not action:
        raise ValueError("Missing 'action' field in event")

    try:
        module = importlib.import_module(f"lambdas.enrichment.{action}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Unknown action: {action!r}") from e

    if not hasattr(module, "handle"):
        raise ValueError(f"Module 'lambdas.enrichment.{action}' has no handle() function")

    payload = event.get("payload", {})
    concept_id = payload.get("concept_id", "unknown")

    langfuse = get_langfuse()
    trace = None

    if langfuse:
        try:
            trace = langfuse.trace(
                name=f"enrichment:{action}",
                session_id=f"enrich-{concept_id}",
                metadata={
                    "concept_id": concept_id,
                    "action": action,
                },
            )
        except Exception as e:
            logger.debug("Failed to create Langfuse trace: %s", e)

    try:
        result = module.handle(payload, context)

        if trace:
            try:
                trace.update(output=_extract_trace_output(result))
            except Exception as exc:
                logger.debug("Failed to update Langfuse trace: %s", exc)

        return result

    except Exception as e:
        if trace:
            try:
                trace.update(level="ERROR", status_message=str(e))
            except Exception as exc:
                logger.debug("Failed to update Langfuse trace: %s", exc)
        raise

    finally:
        flush_langfuse()
