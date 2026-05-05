from __future__ import annotations

from typing import Any

import httpx


class ToolNameNotAllowed(Exception):
    """The model emitted a tool_call whose name is not in the `tools`
    list passed to `chat()`. DeepSeek/OpenAI relay whatever the model
    emits; only the client can enforce the contract."""

    def __init__(self, name: str, allowed: set[str]):
        super().__init__(
            f"model emitted tool_call name {name!r} not in allowed set {sorted(allowed)!r}"
        )
        self.name = name
        self.allowed = allowed


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
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
        }
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        r = await self._client.post(
            f"{self._base_url}/chat/completions", json=payload, headers=headers
        )
        if r.status_code >= 400:
            msg = (
                f"Client error '{r.status_code} {r.reason_phrase}' for url "
                f"'{r.request.url}' (model={self._model}): {r.text}"
            )
            raise httpx.HTTPStatusError(msg, request=r.request, response=r)
        data = r.json()
        msg = data["choices"][0]["message"]
        if tools is not None:
            allowed = {
                t["function"]["name"]
                for t in tools
                if isinstance(t, dict) and "function" in t and "name" in t["function"]
            }
            for tc in msg.get("tool_calls") or []:
                name = tc.get("function", {}).get("name")
                if name not in allowed:
                    raise ToolNameNotAllowed(name, allowed)
        return msg, data.get("usage")

    async def aclose(self) -> None:
        await self._client.aclose()
