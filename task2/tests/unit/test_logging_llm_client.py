import json
from pathlib import Path

import httpx
import pytest

from agent.llm import LLMClient
from agent.llm_trace import LLMTraceWriter, LoggingLLMClient


class _FakeTransport:
    def __init__(self, response_body: dict):
        self.response_body = response_body
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.response_body)


def _make_inner(response_body: dict) -> tuple[LLMClient, _FakeTransport]:
    transport = _FakeTransport(response_body)
    inner = LLMClient(
        base_url="http://test/v1",
        model="deepseek-chat",
        api_key="sk-x",
        transport=httpx.MockTransport(transport.handle_async_request),
    )
    return inner, transport


@pytest.mark.asyncio
async def test_proxy_forwards_chat_and_writes_one_sidecar_line(tmp_path: Path):
    inner, _ = _make_inner(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "1",
                                "type": "function",
                                "function": {"name": "done", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
            },
        }
    )
    sidecar = tmp_path / "abc.llm.jsonl"
    writer = LLMTraceWriter(sidecar)
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    msg, usage = await proxy.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "done", "parameters": {}}}],
        tool_choice="required",
    )
    assert usage["prompt_tokens"] == 100

    lines = sidecar.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["call_idx"] == 0
    assert rec["request"]["model"] == "deepseek-chat"
    assert rec["request"]["base_url"] == "http://test/v1"
    assert rec["request"]["messages"] == [{"role": "user", "content": "hi"}]
    assert rec["request"]["tool_choice"] == "required"
    assert rec["request"]["reasoning"] is False
    assert rec["response"]["usage"]["prompt_cache_hit_tokens"] == 80
    assert rec["response"]["message"]["tool_calls"][0]["function"]["name"] == "done"
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_proxy_increments_call_idx_per_call(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    writer = LLMTraceWriter(tmp_path / "x.llm.jsonl")
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    await proxy.chat([{"role": "user", "content": "1"}])
    await proxy.chat([{"role": "user", "content": "2"}])
    await proxy.chat([{"role": "user", "content": "3"}])

    recs = [json.loads(line) for line in (tmp_path / "x.llm.jsonl").read_text().splitlines()]
    assert [r["call_idx"] for r in recs] == [0, 1, 2]


@pytest.mark.asyncio
async def test_proxy_truncates_oversize_message_content(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    writer = LLMTraceWriter(tmp_path / "x.llm.jsonl")
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    big = "z" * 20000
    await proxy.chat([{"role": "tool", "content": big, "tool_call_id": "t1"}])

    rec = json.loads((tmp_path / "x.llm.jsonl").read_text().splitlines()[0])
    msg0 = rec["request"]["messages"][0]
    assert isinstance(msg0["content"], dict)
    assert msg0["content"]["truncated"] == "z" * 12000
    assert msg0["content"]["original_chars"] == 20000


@pytest.mark.asyncio
async def test_proxy_swallows_writer_failures(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    class _BoomWriter:
        def write(self, _event):
            raise RuntimeError("disk full")

    failures: list[dict] = []

    class _FakeTrace:
        def write(self, event):
            failures.append(event)

    proxy = LoggingLLMClient(inner=inner, writer=_BoomWriter(), trace=_FakeTrace())
    msg, _ = await proxy.chat([{"role": "user", "content": "hi"}])
    assert msg["content"] == "ok"  # call still succeeds
    assert len(failures) == 1
    assert failures[0]["type"] == "llm_log_failed"
    assert "disk full" in failures[0]["payload"]["error"]


@pytest.mark.asyncio
async def test_proxy_aclose_forwards_to_inner():
    closed = {"flag": False}

    class _FakeInner:
        async def chat(self, *a, **k):
            return {"content": "ok"}, None

        async def aclose(self):
            closed["flag"] = True

    proxy = LoggingLLMClient(inner=_FakeInner(), writer=None)  # writer optional in this path
    await proxy.aclose()
    assert closed["flag"] is True


@pytest.mark.asyncio
async def test_proxy_does_not_mutate_caller_messages(tmp_path: Path):
    inner, _ = _make_inner({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    writer = LLMTraceWriter(tmp_path / "x.llm.jsonl")
    proxy = LoggingLLMClient(inner=inner, writer=writer)

    big = "z" * 20000
    messages = [{"role": "tool", "content": big, "tool_call_id": "t1"}]
    original_msg_id = id(messages[0])
    original_content = messages[0]["content"]

    await proxy.chat(messages)

    assert messages[0]["content"] is original_content  # not replaced
    assert id(messages[0]) == original_msg_id  # same dict
    assert messages[0]["content"] == "z" * 20000  # not truncated in place


@pytest.mark.asyncio
async def test_proxy_propagates_inner_exception_without_writing_sidecar(tmp_path: Path):
    class _BoomInner:
        def __init__(self):
            self._model = "deepseek-chat"
            self._base_url = "http://test/v1"

        async def chat(self, *a, **k):
            raise RuntimeError("inner exploded")

        async def aclose(self):
            pass

    sidecar = tmp_path / "x.llm.jsonl"
    writer = LLMTraceWriter(sidecar)
    proxy = LoggingLLMClient(inner=_BoomInner(), writer=writer)

    with pytest.raises(RuntimeError, match="inner exploded"):
        await proxy.chat([{"role": "user", "content": "hi"}])

    # No sidecar line for failed inner calls
    assert not sidecar.exists() or sidecar.read_text() == ""
    # call_idx still advanced (so a future successful call gets idx 1)
    assert proxy._call_idx == 1
