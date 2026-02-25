"""
Embedding Lambda - Orchestrator for CMR concept processing.

Routes concept events from the FIFO queue:
- Updates: Starts the enrichment Step Function (validates, fixes, embeds)
- Deletes: Removes all stored embeddings and associations directly
"""

import json
import logging
import os
from typing import Any

from pydantic import ValidationError

from models.cmr import ConceptMessage
from util.datastores import EmbeddingDatastore, get_datastore
from util.langfuse import flush_langfuse
from util.sfn import get_sfn_client

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def handle_update(message: ConceptMessage) -> dict:
    """
    Start the enrichment Step Function for a concept update.

    The Step Function handles the full lifecycle: validation, fixing,
    embedding generation, and storage.
    """
    state_machine_arn = os.environ.get("ENRICHMENT_STATE_MACHINE_ARN")
    if not state_machine_arn:
        raise ValueError("ENRICHMENT_STATE_MACHINE_ARN environment variable is not set")

    sfn_input = {
        "concept_id": message.concept_id,
        "revision_id": message.revision_id,
        "concept_type": message.concept_type.value,
    }

    response = get_sfn_client().start_execution(
        stateMachineArn=state_machine_arn,
        input=json.dumps(sfn_input),
    )

    logger.info(
        "Started enrichment for %s:%s (revision %s) -> ExecutionArn: %s",
        message.concept_type,
        message.concept_id,
        message.revision_id,
        response["executionArn"],
    )

    return {
        "concept_id": message.concept_id,
        "status": "enrichment_started",
        "execution_arn": response["executionArn"],
    }


def handle_delete(message: ConceptMessage, datastore: EmbeddingDatastore) -> None:
    """Remove all stored data for a concept."""
    external_id = message.concept_id

    deleted_chunks = datastore.delete_chunks(external_id)
    deleted_assocs = datastore.delete_associations(external_id)
    deleted_kms = datastore.delete_kms_associations(external_id)

    # For collections, also delete from collections table
    deleted_collection = False
    if message.concept_type == "collection":
        deleted_collection = datastore.delete_collection(external_id)

    logger.info(
        "Deleted %s: %d chunks, %d associations, %d KMS links, collection=%s",
        external_id,
        deleted_chunks,
        deleted_assocs,
        deleted_kms,
        deleted_collection,
    )


def process_message(
    record: dict[str, Any],
    datastore: EmbeddingDatastore,
) -> None:
    """Parse and route a single SQS message."""
    body = json.loads(record["body"])
    message = ConceptMessage.model_validate(body)

    if message.action == "concept-update":
        handle_update(message)
    elif message.action == "concept-delete":
        handle_delete(message, datastore)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Lambda handler for FIFO queue messages.

    Uses partial batch response - failed messages are returned for retry,
    successful messages are deleted from the queue.
    """
    records = event.get("Records", [])
    logger.info("Processing %d messages", len(records))

    datastore = get_datastore()
    failures = []

    try:
        for record in records:
            message_id = record["messageId"]
            try:
                process_message(record, datastore)
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                logger.exception("Failed message %s: %s", message_id, e)
                failures.append({"itemIdentifier": message_id})
    finally:
        flush_langfuse()

    if failures:
        logger.warning("Completed with %d/%d failures", len(failures), len(records))

    return {"batchItemFailures": failures}
