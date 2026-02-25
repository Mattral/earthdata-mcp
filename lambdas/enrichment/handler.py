"""Enrichment pipeline router handler.

Single Lambda that dispatches to step handlers based on the 'action' field
injected by the Step Function via Parameters.

Each step module exposes a handle() function decorated with @observe,
which automatically nests as a span under the parent trace created here
via @observe on handler(). All steps sharing the same session_id are
grouped together in Langfuse.

The action name maps directly to the module name under lambdas.enrichment.
"""

import importlib
import logging

from langfuse import observe

from util.langfuse import flush_langfuse, trace_update

logger = logging.getLogger(__name__)

# Fields to exclude from trace output (large or not useful)
_TRACE_EXCLUDE_FIELDS = {"metadata", "enriched_metadata", "fix_history"}


def _extract_trace_output(result: dict) -> dict:
    """Extract step-specific output for the Langfuse trace.

    Strips large/offloaded fields and returns only the interesting
    step results (validation summary, fix details, url_fix counts, etc.).
    """
    return {k: v for k, v in result.items() if k not in _TRACE_EXCLUDE_FIELDS}


@observe(name="enrichment")
def handler(event, context):
    """Route to the appropriate sub-handler based on the action field."""
    try:
        action = event.get("action")

        if not action:
            raise ValueError("Missing 'action' field in event")

        # The action value (e.g. "fetch", "url_fix") maps directly to a module
        # filename under lambdas/enrichment/. Each step module must be named to
        # match its action and expose a handle(event, context) function.
        try:
            module = importlib.import_module(f"lambdas.enrichment.{action}")
        except ModuleNotFoundError as e:
            raise ValueError(f"Unknown action: {action!r}") from e

        if not hasattr(module, "handle"):
            raise ValueError(f"Module 'lambdas.enrichment.{action}' has no handle() function")

        payload = event.get("payload")
        if payload is None:
            raise ValueError(f"Missing 'payload' field in event for action {action!r}")

        concept_id = payload.get("concept_id", "unknown")

        trace_update(
            session_id=f"enrich-{concept_id}",
            metadata={"concept_id": concept_id, "action": action},
        )

        return module.handle(payload, context)
    finally:
        flush_langfuse()
