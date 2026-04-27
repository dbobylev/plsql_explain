from __future__ import annotations

from rag.config import load_rag_config


class EmbeddingClient:
    """Thin wrapper around OpenAI-compatible embeddings endpoint."""

    def __init__(self) -> None:
        import openai  # imported lazily so tests can mock without installing

        config = load_rag_config()

        self._model = config.embedding_model
        self._client = openai.OpenAI(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]
