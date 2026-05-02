from __future__ import annotations

from typing import Any

import httpx


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": reasoning}},
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        r = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"], data.get("usage")

    async def aclose(self) -> None:
        await self._client.aclose()
