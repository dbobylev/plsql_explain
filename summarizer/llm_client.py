from __future__ import annotations

import logging
import os

_logger = logging.getLogger(__name__)


class LlmClient:
    """Thin wrapper around openai.OpenAI for OpenAI-compatible endpoints."""

    def __init__(self) -> None:
        import openai  # imported lazily so tests can mock without installing

        base_url = os.environ.get("LLM_BASE_URL")
        api_key = os.environ.get("LLM_API_KEY", "")
        self._model = os.environ.get("LLM_MODEL", "gpt-4o")
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        _logger.debug(
            "[PROMPT]\n--- SYSTEM ---\n%s\n--- USER ---\n%s\n--- END ---",
            system,
            user,
        )
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        result = response.choices[0].message.content or ""
        _logger.debug("[RESPONSE]\n%s\n--- END ---", result)
        return result
