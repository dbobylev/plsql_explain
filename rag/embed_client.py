from __future__ import annotations

import os


class EmbeddingClient:
    """Thin wrapper around OpenAI-compatible embeddings endpoint."""

    def __init__(self) -> None:
        import openai  # imported lazily so tests can mock without installing

        base_url = os.environ.get("EMBEDDING_BASE_URL")
        api_key = os.environ.get("EMBEDDING_API_KEY", "")
        model = os.environ.get("EMBEDDING_MODEL")

        if not base_url:
            raise ValueError("EMBEDDING_BASE_URL is required")
        if not model:
            raise ValueError("EMBEDDING_MODEL is required")

        self._model = model
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]
