from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

TRUNCATE_CHARS_DEFAULT = 12000


def truncate_for_log(value: Any, *, max_chars: int = TRUNCATE_CHARS_DEFAULT) -> Any:
    """Truncate a string when it exceeds `max_chars`. Returns the original
    value unchanged if it is not a string or fits under the cap. For
    over-cap strings, returns `{"truncated": <prefix>, "original_chars": N}`
    so an analyzer can still report the true payload size."""
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return {"truncated": value[:max_chars], "original_chars": len(value)}


class LLMTraceWriter:
    """Append-only JSONL writer for per-session LLM call records.
    Mirrors `agent.trace.TraceWriter` deliberately."""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> None:
        out = dict(event)
        out.setdefault("ts", datetime.now(UTC).isoformat())
        with self._path.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


class _ChatLike(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]: ...

    async def aclose(self) -> None: ...


class _TraceLike(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


def _truncate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply truncation to each message's `content` (and tool_call args if
    present). Returns a deep-enough copy so the original passed to the LLM
    is unaffected."""
    out: list[dict[str, Any]] = []
    for m in messages:
        copy = dict(m)
        if "content" in copy:
            copy["content"] = truncate_for_log(copy["content"])
        if "tool_calls" in copy and isinstance(copy["tool_calls"], list):
            new_tcs = []
            for tc in copy["tool_calls"]:
                tc_copy = dict(tc)
                if "function" in tc_copy and isinstance(tc_copy["function"], dict):
                    fn_copy = dict(tc_copy["function"])
                    if "arguments" in fn_copy:
                        fn_copy["arguments"] = truncate_for_log(fn_copy["arguments"])
                    tc_copy["function"] = fn_copy
                new_tcs.append(tc_copy)
            copy["tool_calls"] = new_tcs
        out.append(copy)
    return out


class LoggingLLMClient:
    """Wraps an `LLMClient`-shaped object, forwarding `chat()` calls while
    writing a sidecar record per call. The inner client stays
    provider-agnostic and unaware of the logger. Logging failures are
    swallowed and reported into the main trace as `llm_log_failed`."""

    def __init__(
        self,
        *,
        inner: _ChatLike,
        writer: Any,  # LLMTraceWriter or duck-typed; None disables logging
        trace: _TraceLike | None = None,
    ):
        self._inner = inner
        self._writer = writer
        self._trace = trace
        self._call_idx = 0
        # Best-effort introspection so the sidecar can record where the
        # request was sent without poking inner-client internals at call time.
        self._model = getattr(inner, "_model", None)
        self._base_url = getattr(inner, "_base_url", None)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        reasoning: bool = False,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        idx = self._call_idx
        self._call_idx += 1
        t0 = time.monotonic()
        msg, usage = await self._inner.chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            reasoning=reasoning,
            temperature=temperature,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        if self._writer is not None:
            try:
                self._writer.write(
                    {
                        "call_idx": idx,
                        "latency_ms": latency_ms,
                        "request": {
                            "model": self._model,
                            "base_url": self._base_url,
                            "messages": _truncate_messages(messages),
                            "tools": tools,
                            "tool_choice": tool_choice,
                            "temperature": temperature,
                        },
                        "response": {
                            "message": msg,
                            "usage": usage,
                        },
                    }
                )
            except Exception as e:
                if self._trace is not None:
                    with contextlib.suppress(Exception):
                        self._trace.write(
                            {"type": "llm_log_failed", "payload": {"error": str(e)[:200]}}
                        )
        return msg, usage

    async def aclose(self) -> None:
        await self._inner.aclose()
