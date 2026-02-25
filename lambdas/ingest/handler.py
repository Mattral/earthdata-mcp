"""
Ingest Lambda - SNS receiver that routes concept events to the embedding queue.

Receives concept update/delete events from CMR SNS topic and forwards all
messages to the embedding queue. The embedding lambda handles routing:
- Updates: Triggers the enrichment Step Function (validates, fixes, then embeds)
- Deletes: Removes stored data directly
"""

import json
import logging
import os

from models.cmr import ConceptMessage
from util.sqs import get_sqs_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def process_record(record: dict) -> dict:
    """
    Process a single SNS record from the event.

    Forwards all messages to the embedding queue for processing.
    """
    sns_message = record.get("Sns", {})
    raw_message = json.loads(sns_message.get("Message", "{}"))
    message = ConceptMessage.model_validate(raw_message)

    return _send_to_queue(message)


def _send_to_queue(message: ConceptMessage) -> dict:
    """Send a message to the embedding queue."""
    queue_url = os.environ.get("EMBEDDING_QUEUE_URL")
    if not queue_url:
        raise ValueError("EMBEDDING_QUEUE_URL environment variable not set")

    response = get_sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=message.model_dump_json(by_alias=True),
        MessageGroupId=f"{message.concept_type}:{message.concept_id}",
        MessageDeduplicationId=f"{message.concept_id}:{message.revision_id}",
    )

    logger.info(
        "Queued %s for %s:%s (revision %s) -> SQS MessageId: %s",
        message.action,
        message.concept_type,
        message.concept_id,
        message.revision_id,
        response["MessageId"],
    )

    return {
        "concept_id": message.concept_id,
        "status": "queued",
        "sqs_message_id": response["MessageId"],
    }


def handler(event: dict, _context) -> dict:
    """
    Lambda handler for processing CMR concept events from SNS.

    Returns dict with processing results including count of processed/failed.
    """
    records = event.get("Records", [])
    logger.info("Processing %d SNS record(s)", len(records))

    results = []
    errors = []

    for record in records:
        try:
            result = process_record(record)
            results.append(result)
        except Exception as e:
            logger.exception("Failed to process record")
            errors.append(
                {
                    "message_id": record.get("Sns", {}).get("MessageId", "unknown"),
                    "error": str(e),
                }
            )

    response = {
        "processed": len(results),
        "failed": len(errors),
        "results": results,
    }

    if errors:
        response["errors"] = errors
        logger.warning("Completed with %d error(s): %s", len(errors), errors)
    else:
        logger.info("Successfully processed %d record(s)", len(results))

    return response
