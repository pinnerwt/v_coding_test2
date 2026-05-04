import httpx
import pytest

from sec_toolbox.client import DEFAULT_USER_AGENT, SECClient
from sec_toolbox.throttle import TokenBucket


def make_client(handler, throttle: TokenBucket | None = None, user_agent: str | None = None):
    transport = httpx.MockTransport(handler)
    return SECClient(
        transport=transport,
        throttle=throttle,
        user_agent=user_agent,
    )


def test_default_user_agent_used_when_unset(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("https://data.sec.gov/foo")
    assert seen == [DEFAULT_USER_AGENT]


def test_env_var_user_agent_overrides_default(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "custom-ua test@example.com")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("https://data.sec.gov/foo")
    assert seen == ["custom-ua test@example.com"]


def test_explicit_user_agent_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "from-env")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, user_agent="explicit-ua")
    client.get("https://data.sec.gov/foo")
    assert seen == ["explicit-ua"]


def test_throttle_acquire_called_per_request():
    calls: list[float] = []

    class Counting:
        def acquire(self, n: float = 1.0) -> None:
            calls.append(n)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, throttle=Counting())
    client.get("https://data.sec.gov/a")
    client.get("https://data.sec.gov/b")
    assert calls == [1.0, 1.0]


def test_retries_once_on_429():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"err": "rate"})
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    class FakeThrottle:
        def acquire(self, n: float = 1.0) -> None:
            pass

    client = SECClient(
        transport=httpx.MockTransport(handler),
        throttle=FakeThrottle(),
        sleep_fn=sleeps.append,
    )
    resp = client.get("https://data.sec.gov/foo")
    assert resp.status_code == 200
    assert attempts["n"] == 2
    assert sleeps == [1.0]


def test_raises_on_persistent_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    class FakeThrottle:
        def acquire(self, n: float = 1.0) -> None:
            pass

    client = SECClient(
        transport=httpx.MockTransport(handler),
        throttle=FakeThrottle(),
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://data.sec.gov/foo")
