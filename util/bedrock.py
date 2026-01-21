"""AWS Bedrock client utility."""

import boto3

_client = None


def get_bedrock_client():
    """Get the Bedrock runtime client (lazy initialization, reused across Lambda invocations)."""
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime")
    return _client
