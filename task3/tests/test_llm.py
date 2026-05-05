import pytest

from sec_toolbox.llm import LLMClient


def test_llm_client_uses_env_vars(monkeypatch, httpx_mock):
    monkeypatch.setenv("TASK3_MODEL_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("TASK3_MODEL_NAME", "test-model")
    monkeypatch.setenv("TASK3_API_KEY", "k")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    httpx_mock.add_response(json={"choices": [{"message": {"content": "reply"}}]})

    out = LLMClient().chat([{"role": "user", "content": "hi"}])
    assert out == "reply"

    req = httpx_mock.get_requests()[0]
    assert req.url.host == "api.example.com"
    assert str(req.url).endswith("/chat/completions")
    assert req.headers["authorization"] == "Bearer k"


def test_llm_client_falls_back_to_deepseek_key(monkeypatch, httpx_mock):
    monkeypatch.delenv("TASK3_API_KEY", raising=False)
    monkeypatch.delenv("TASK3_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TASK3_MODEL_NAME", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep")
    httpx_mock.add_response(json={"choices": [{"message": {"content": "ok"}}]})

    LLMClient().chat([{"role": "user", "content": "hi"}])

    req = httpx_mock.get_requests()[0]
    assert req.url.host == "api.deepseek.com"
    assert req.headers["authorization"] == "Bearer deep"


def test_llm_client_sends_model_name(monkeypatch, httpx_mock):
    monkeypatch.setenv("TASK3_MODEL_NAME", "my-model")
    monkeypatch.setenv("TASK3_API_KEY", "k")
    httpx_mock.add_response(json={"choices": [{"message": {"content": "ok"}}]})

    LLMClient().chat([{"role": "user", "content": "hi"}])

    req = httpx_mock.get_requests()[0]
    import json

    body = json.loads(req.content)
    assert body["model"] == "my-model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_llm_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("TASK3_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API key"):
        LLMClient()
