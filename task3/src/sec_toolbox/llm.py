"""Provider-agnostic OpenAI-compatible LLM client.

Used by the short-slice status reader. Defaults to DeepSeek
(``https://api.deepseek.com``, ``deepseek-chat``) but every knob is overridable
via environment variables so swapping to a local Qwen3.5 endpoint is one
``export TASK3_MODEL_BASE_URL=...`` away.

Env vars:

* ``TASK3_MODEL_BASE_URL`` — provider base URL (default ``https://api.deepseek.com``)
* ``TASK3_MODEL_NAME`` — model identifier (default ``deepseek-chat``)
* ``TASK3_API_KEY`` — bearer token; falls back to ``DEEPSEEK_API_KEY``
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEFAULT_MODEL = "deepseek-chat"


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("TASK3_MODEL_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model or os.environ.get("TASK3_MODEL_NAME") or _DEFAULT_MODEL
        key = api_key or os.environ.get("TASK3_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("LLM API key not configured (set TASK3_API_KEY or DEEPSEEK_API_KEY)")
        self._api_key = key
        self._client = httpx.Client(timeout=timeout)

    def chat(self, messages: list[dict[str, Any]], *, temperature: float = 0.0) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        r = self._client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._client.close()
