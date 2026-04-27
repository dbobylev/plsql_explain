from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DOTENV_PATH = _PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class RagConfig:
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    qdrant_url: str
    qdrant_api_key: str


@lru_cache(maxsize=1)
def load_rag_config(dotenv_path: Path = _DEFAULT_DOTENV_PATH) -> RagConfig:
    values = dotenv_values(dotenv_path)
    embedding_base_url = _required_value(values, "EMBEDDING_BASE_URL", dotenv_path)
    embedding_model = _required_value(values, "EMBEDDING_MODEL", dotenv_path)
    qdrant_url = _required_value(values, "QDRANT_URL", dotenv_path)

    return RagConfig(
        embedding_base_url=embedding_base_url,
        embedding_api_key=_optional_value(values, "EMBEDDING_API_KEY"),
        embedding_model=embedding_model,
        qdrant_url=qdrant_url,
        qdrant_api_key=_optional_value(values, "QDRANT_API_KEY"),
    )


def _required_value(values: dict[str, str | None], name: str, dotenv_path: Path) -> str:
    value = _optional_value(values, name)
    if value:
        return value
    raise ValueError(f"{name} is required in {dotenv_path}")


def _optional_value(values: dict[str, str | None], name: str) -> str:
    return (values.get(name) or "").strip()
