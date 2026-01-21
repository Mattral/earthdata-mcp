"""AWS Bedrock embedding generator implementation."""

import json
import logging
from typing import Any

from util.bedrock import get_bedrock_client
from util.embeddings.base import EmbeddingError, EmbeddingGenerator

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"


class BedrockEmbeddingGenerator(EmbeddingGenerator):
    """
    AWS Bedrock embedding generator using Titan models.

    This implementation uses the same model for all concept types and attributes.
    For per-concept/attribute model routing, use RoutingEmbeddingGenerator.
    """

    def __init__(
        self,
        model_id: str | None = None,
        client: Any | None = None,
    ):
        """
        Initialize the Bedrock embedding generator.

        Args:
            model_id: Bedrock model ID. Defaults to EMBEDDING_MODEL env var.
            client: Optional boto3 bedrock-runtime client for testing.
        """
        self._model_id = model_id or DEFAULT_EMBEDDING_MODEL
        self._client = client

    @property
    def client(self):
        """Get the Bedrock client (uses centralized utility if not injected)."""
        if self._client is None:
            return get_bedrock_client()
        return self._client

    @property
    def model_id(self) -> str:
        """Return the model identifier."""
        return self._model_id

    def generate(
        self,
        text: str,
        concept_type: str | None = None,
        attribute: str | None = None,
        span: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[float]:
        """
        Generate an embedding vector using Bedrock Titan.

        Args:
            text: The text to embed.
            concept_type: Ignored in this implementation (same model for all).
            attribute: Ignored in this implementation (same model for all).
            span: Optional Langfuse span for observability.
            metadata: Optional metadata dict for Langfuse generation tracking.

        Returns:
            Embedding vector as a list of floats.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        observation = None
        if span:
            try:
                # Name includes attribute for easier identification in Langfuse UI
                obs_name = f"embed-{attribute}" if attribute else "embed"
                # Merge provided metadata with text_length
                obs_metadata = {"text_length": len(text)}
                if metadata:
                    obs_metadata.update(metadata)
                observation = span.start_observation(
                    name=obs_name,
                    as_type="embedding",
                    model=self._model_id,
                    input=text,
                    metadata=obs_metadata,
                )
            except Exception as e:
                logger.debug("Failed to create Langfuse observation: %s", e)

        try:
            response = self.client.invoke_model(
                modelId=self._model_id,
                body=json.dumps({"inputText": text}),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            embedding = result["embedding"]

            if observation:
                try:
                    # Titan returns inputTextTokenCount
                    input_tokens = result.get("inputTextTokenCount", 0)
                    observation.update(
                        usage_details={"input": input_tokens, "output": 0, "total": input_tokens}
                    )
                    observation.end()
                except Exception as e:
                    logger.debug("Failed to end Langfuse observation: %s", e)

            return embedding

        except Exception as e:
            if observation:
                try:
                    observation.update(level="ERROR", status_message=str(e))
                    observation.end()
                except Exception:
                    pass
            raise EmbeddingError(f"Failed to generate embedding: {e}") from e


class RoutingEmbeddingGenerator(EmbeddingGenerator):
    """
    Embedding generator that routes to different generators based on concept type/attribute.

    Example configuration:
        generators = {
            "collection.abstract": BedrockEmbeddingGenerator(model_id="..."),
            "collection": BedrockEmbeddingGenerator(model_id="..."),
            "default": BedrockEmbeddingGenerator(),
        }
        router = RoutingEmbeddingGenerator(generators)

    Routing priority:
        1. "{concept_type}.{attribute}" - most specific
        2. "{concept_type}" - concept-level default
        3. "default" - fallback
    """

    def __init__(
        self,
        generators: dict[str, EmbeddingGenerator],
        default_generator: EmbeddingGenerator | None = None,
    ):
        """
        Initialize the routing generator.

        Args:
            generators: Dict mapping keys to generators.
            default_generator: Fallback generator if no match found.
        """
        self._generators = generators
        self._default = default_generator or generators.get("default")
        if self._default is None:
            raise ValueError("Must provide either 'default' in generators or default_generator")

    def _get_generator(
        self,
        concept_type: str | None,
        attribute: str | None,
    ) -> EmbeddingGenerator:
        """Get the appropriate generator for the given concept type and attribute."""
        if concept_type and attribute:
            key = f"{concept_type}.{attribute}"
            if key in self._generators:
                return self._generators[key]

        if concept_type and concept_type in self._generators:
            return self._generators[concept_type]

        return self._default

    @property
    def model_id(self) -> str:
        """Return the default model identifier."""
        return self._default.model_id

    def generate(
        self,
        text: str,
        concept_type: str | None = None,
        attribute: str | None = None,
        span: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[float]:
        """Generate embedding using the appropriate generator for the concept/attribute."""
        generator = self._get_generator(concept_type, attribute)
        return generator.generate(text, concept_type, attribute, span, metadata)
