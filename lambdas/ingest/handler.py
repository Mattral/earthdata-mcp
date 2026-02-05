"""
Ingest Lambda - SNS receiver that forwards concept events to FIFO queue.

Receives concept update/delete events from CMR SNS topic and pushes them
to a FIFO SQS queue for ordered processing by the embedding lambda.
"""

import json
import logging
import os

from util.models import ConceptMessage
from util.sqs import get_sqs_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def process_record(record: dict) -> dict:
    """
    Process a single SNS record from the event.

    Parses and validates the message, then forwards it to the FIFO queue.

    Args:
        record: SNS event record

    Returns:
        dict with processing results

    Raises:
        ValueError: If required environment variables are missing
        json.JSONDecodeError: If message JSON is invalid
        ValidationError: If message schema validation fails
    """
    sns_message = record.get("Sns", {})
    message_id = sns_message.get("MessageId", "unknown")

    try:
        raw_message = json.loads(sns_message.get("Message", "{}"))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Failed to parse SNS message {message_id}: {e.msg}",
            e.doc,
            e.pos,
        ) from e

    try:
        message = ConceptMessage.model_validate(raw_message)
    except Exception as e:
        logger.error("Message validation failed for SNS %s: %s", message_id, e)
        raise

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

    Args:
        event: SNS event containing concept update/delete notifications
        _context: Lambda context object

    Returns:
        dict with processing results including count of processed/failed records.
        Failed messages are tracked for visibility and troubleshooting.
    """
    records = event.get("Records", [])
    logger.info("Processing %d SNS record(s)", len(records))

    results = []
    errors = []

    for idx, record in enumerate(records, 1):
        message_id = record.get("Sns", {}).get("MessageId", f"unknown-{idx}")
        try:
            result = process_record(record)
            results.append(result)
        except (json.JSONDecodeError, ValueError) as e:
            error_detail = {
                "message_id": message_id,
                "error_type": type(e).__name__,
                "error": str(e),
            }
            logger.error("Input error for SNS %s: %s", message_id, e)
            errors.append(error_detail)
        except Exception as e:
            error_detail = {
                "message_id": message_id,
                "error_type": type(e).__name__,
                "error": str(e),
            }
            logger.exception("Unexpected error processing record %s", message_id)
            errors.append(error_detail)

    response = {
        "processed": len(results),
        "failed": len(errors),
        "results": results,
    }

    if errors:
        response["errors"] = errors
        logger.warning("Completed with %d error(s)", len(errors))
    else:
        logger.info("Successfully processed all %d record(s)", len(results))

    return response
