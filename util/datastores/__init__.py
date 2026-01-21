"""Datastore abstractions for embedding storage."""

from util.datastores.base import EmbeddingDatastore
from util.datastores.postgres import PostgresEmbeddingDatastore


def get_datastore() -> EmbeddingDatastore:
    """Get the datastore implementation."""
    return PostgresEmbeddingDatastore()


__all__ = [
    "EmbeddingDatastore",
    "PostgresEmbeddingDatastore",
    "get_datastore",
]
