from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from . import tools
from .config import Config
from .state import SessionState

# DeepSeek prices per million tokens (cache-miss).
_PRICES = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


def _estimate_cost(usage: dict | None, model: str) -> float:
    if not usage:
        return 0.0
    prices = _PRICES.get(model, (0.0, 0.0))
    return (
        usage.get("prompt_tokens", 0) * prices[0] / 1_000_000
        + usage.get("completion_tokens", 0) * prices[1] / 1_000_000
    )


async def _dispatch_tool(name: str, args: dict, state: SessionState, *, small, cfg: Config) -> dict:
    try:
        mod = tools.REGISTRY[name]
        if inspect.iscoroutinefunction(mod.run):
            # Tools whose run is async (only classify_statuses today) take
            # client/model kwargs.
            return await mod.run(state, args, client=small, model=cfg.small_model)
        return mod.run(state, args)
    except KeyError:
        return {"error": f"unknown tool: {name}"}
    except Exception as e:  # noqa: BLE001 — convert to error-dict so loop continues
        return {"error": f"{type(e).__name__}: {e}"}


async def run_loop(
    *,
    html_path: str,
    out_path: str,
    cfg: Config,
    big,
    small,
) -> dict[str, Any]:
    for model_name in (cfg.big_model, cfg.small_model):
        if model_name not in _PRICES:
            raise ValueError(f"unknown model for pricing: {model_name}")

    state = SessionState()
    state.inputs = {"html_path": html_path, "out_path": out_path}

    system = (Path(__file__).parent / "prompts" / "system_big.md").read_text()
    messages: list[dict] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Extract per-item JSON from {html_path}. "
                f"Write the result to {out_path}. Use the tools."
            ),
        },
    ]
    schemas = tools.schemas()

    while state.steps < cfg.max_steps:
        if state.cost_usd >= cfg.cost_ceiling_usd:
            return {"status": "cost_exceeded", "state": state, "messages": messages}

        msg, usage = await big.chat(messages, tools=schemas, reasoning=True, temperature=0)
        state.add_cost(_estimate_cost(usage, cfg.big_model))
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Use the tools to make progress, or call done() if extraction is complete."
                    ),
                }
            )
            state.bump_step()
            continue

        for tc in tool_calls:
            name = ""
            try:
                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                tool_call_id = tc["id"]
            except KeyError as e:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(
                            {"error": f"malformed tool_call: KeyError({e})"},
                            ensure_ascii=False,
                        ),
                    }
                )
                continue

            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as e:
                result: dict = {"error": f"bad tool arguments JSON: {e}"}
            else:
                result = await _dispatch_tool(name, args, state, small=small, cfg=cfg)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

            if name == "done":
                return {"status": "done", "state": state, "messages": messages}

        state.bump_step()

    return {"status": "max_steps_exceeded", "state": state, "messages": messages}
