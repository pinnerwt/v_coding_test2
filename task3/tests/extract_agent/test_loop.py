from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock  # noqa: F401  (imported per spec)

import pytest

from extract_agent.config import Config
from extract_agent.loop import run_loop


class StubLLM:
    def __init__(self, scripted: list):
        self._scripted = list(scripted)
        self.calls: list[tuple[str, int]] = []

    async def chat(self, messages, *, tools=None, tool_choice=None, **_):
        self.calls.append((messages[-1].get("role"), len(messages)))
        return self._scripted.pop(0), {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
        }

    async def aclose(self) -> None:  # for symmetry with LLMClient
        pass


@pytest.mark.asyncio
async def test_loop_happy_path(tmp_path: Path):
    html = tmp_path / "f.html"
    html.write_text("<p>Item 1. Business</p><p>body</p><p>Item 2. Properties</p><p>body2</p>")
    out = tmp_path / "out.json"
    scripted = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "clean_and_load",
                        "arguments": f'{{"html_path": "{html}"}}',
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "2",
                    "type": "function",
                    "function": {"name": "find_anchors", "arguments": '{"text_id": "t0"}'},
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "3",
                    "type": "function",
                    "function": {"name": "slice_items", "arguments": '{"text_id": "t0"}'},
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "4",
                    "type": "function",
                    "function": {"name": "validate_records", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "5",
                    "type": "function",
                    "function": {
                        "name": "write_output",
                        "arguments": f'{{"json_path": "{out}"}}',
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "6",
                    "type": "function",
                    "function": {"name": "done", "arguments": '{"message": "ok"}'},
                }
            ],
        },
    ]
    cfg = Config.from_env()
    big = StubLLM(scripted)
    small = StubLLM([])
    result = await run_loop(html_path=str(html), out_path=str(out), cfg=cfg, big=big, small=small)
    assert result["status"] == "done"
    assert out.exists()


@pytest.mark.asyncio
async def test_loop_max_steps(tmp_path: Path):
    html = tmp_path / "f.html"
    html.write_text("<p>x</p>")
    scripted = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": str(i),
                    "type": "function",
                    "function": {
                        "name": "clean_and_load",
                        "arguments": f'{{"html_path": "{html}"}}',
                    },
                }
            ],
        }
        for i in range(50)
    ]
    cfg = replace(Config.from_env(), max_steps=3)
    big = StubLLM(scripted)
    small = StubLLM([])
    out = tmp_path / "out.json"
    result = await run_loop(html_path=str(html), out_path=str(out), cfg=cfg, big=big, small=small)
    assert result["status"] == "max_steps_exceeded"


@pytest.mark.asyncio
async def test_loop_dispatcher_returns_error_dict_when_tool_raises(tmp_path: Path):
    """If a tool's run() raises, the dispatcher must wrap it as
    {"error": "..."} in the tool message and let the loop continue —
    the LLM should see the error and choose what to do, not crash the run."""
    html = tmp_path / "f.html"
    html.write_text("<p>Item 1. Business</p><p>body</p>")
    out = tmp_path / "out.json"
    # 1) clean_and_load — works
    # 2) find_anchors with a bad regex — raises re.error inside the tool
    # 3) done
    bad_regex_args = '{"text_id": "t0", "regex": "(unclosed"}'
    scripted = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "clean_and_load",
                        "arguments": f'{{"html_path": "{html}"}}',
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "2",
                    "type": "function",
                    "function": {"name": "find_anchors", "arguments": bad_regex_args},
                }
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "3",
                    "type": "function",
                    "function": {"name": "done", "arguments": "{}"},
                }
            ],
        },
    ]
    cfg = Config.from_env()
    big = StubLLM(scripted)
    small = StubLLM([])
    result = await run_loop(html_path=str(html), out_path=str(out), cfg=cfg, big=big, small=small)
    assert result["status"] == "done"
    # The tool message for call id 2 must carry an "error" key in its JSON content
    import json as _json

    state = result["state"]  # noqa: F841
    # Inspect the messages stashed on state for the tool message we expect
    tool_msgs = [
        m for m in result["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "2"
    ]
    assert len(tool_msgs) == 1
    payload = _json.loads(tool_msgs[0]["content"])
    assert "error" in payload
