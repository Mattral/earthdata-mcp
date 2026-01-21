"""Base embedding generator abstract class."""

from abc import ABC, abstractmethod
from typing import Any


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class EmbeddingGenerator(ABC):
    """
    Abstract base class for embedding generation.

    Implementations can use different embedding models (Bedrock Titan, OpenAI, etc.)
    or approaches (dense embeddings, graph embeddings, etc.) and can vary by
    concept type and attribute.
    """

    @abstractmethod
    def generate(
        self,
        text: str,
        concept_type: str | None = None,
        attribute: str | None = None,
        span: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[float]:
        """
        Generate an embedding vector for the given text.

        Args:
            text: The text to embed.
            concept_type: Optional concept type for model routing.
            attribute: Optional attribute name for model routing.
            span: Optional Langfuse span for observability.
            metadata: Optional metadata dict for Langfuse generation tracking.

        Returns:
            Embedding vector as a list of floats.

        Raises:
            EmbeddingError: If embedding generation fails.
        """

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Return the model identifier."""
