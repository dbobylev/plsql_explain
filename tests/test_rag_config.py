from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from rag.config import RagConfig, load_rag_config
from rag.embed_client import EmbeddingClient
from rag.qdrant_client import QdrantClient


def _make_dotenv_path() -> Path:
    base_dir = Path(".tmp_pytest_local") / "rag_config"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{uuid4().hex}.env"


def test_load_rag_config_reads_values_from_dotenv() -> None:
    dotenv_path = _make_dotenv_path()
    dotenv_path.write_text(
        "\n".join(
            [
                "EMBEDDING_BASE_URL=http://embedding.local/v1",
                "EMBEDDING_API_KEY=embed-key",
                "EMBEDDING_MODEL=text-embedding-3-small",
                "QDRANT_URL=http://qdrant.local:6333",
                "QDRANT_API_KEY=qdrant-key",
            ]
        ),
        encoding="utf-8",
    )
    load_rag_config.cache_clear()

    config = load_rag_config(dotenv_path)

    assert config == RagConfig(
        embedding_base_url="http://embedding.local/v1",
        embedding_api_key="embed-key",
        embedding_model="text-embedding-3-small",
        qdrant_url="http://qdrant.local:6333",
        qdrant_api_key="qdrant-key",
    )


def test_load_rag_config_requires_required_values() -> None:
    dotenv_path = _make_dotenv_path()
    dotenv_path.write_text("QDRANT_URL=http://qdrant.local:6333\n", encoding="utf-8")
    load_rag_config.cache_clear()

    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        load_rag_config(dotenv_path)


def test_embedding_client_uses_rag_config() -> None:
    fake_openai_client = Mock()
    fake_openai_module = SimpleNamespace(OpenAI=Mock(return_value=fake_openai_client))
    config = RagConfig(
        embedding_base_url="http://embedding.local/v1",
        embedding_api_key="embed-key",
        embedding_model="text-embedding-3-small",
        qdrant_url="http://qdrant.local:6333",
        qdrant_api_key="qdrant-key",
    )

    with patch("rag.embed_client.load_rag_config", return_value=config), \
         patch.dict("sys.modules", {"openai": fake_openai_module}):
        client = EmbeddingClient()

    fake_openai_module.OpenAI.assert_called_once_with(
        base_url="http://embedding.local/v1",
        api_key="embed-key",
    )
    assert client._model == "text-embedding-3-small"


def test_qdrant_client_uses_rag_config() -> None:
    config = RagConfig(
        embedding_base_url="http://embedding.local/v1",
        embedding_api_key="embed-key",
        embedding_model="text-embedding-3-small",
        qdrant_url="http://qdrant.local:6333/",
        qdrant_api_key="qdrant-key",
    )

    with patch("rag.qdrant_client.load_rag_config", return_value=config):
        client = QdrantClient()

    assert client._base_url == "http://qdrant.local:6333"
    assert client._api_key == "qdrant-key"
