from extract_agent.config import Config


def test_defaults_when_env_unset(monkeypatch):
    for k in (
        "AGENT_MODEL_BASE_URL",
        "AGENT_MODEL_BIG",
        "AGENT_MODEL_SMALL",
        "DEEPSEEK_API_KEY",
        "MAX_STEPS",
        "COST_CEILING_USD",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.big_model == "deepseek-v4-pro"
    assert cfg.small_model == "deepseek-v4-flash"
    assert cfg.api_key is None
    assert cfg.max_steps == 30
    assert cfg.cost_ceiling_usd == 0.50


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_BIG", "custom-big")
    monkeypatch.setenv("AGENT_MODEL_SMALL", "custom-small")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("MAX_STEPS", "10")
    monkeypatch.setenv("COST_CEILING_USD", "1.50")
    cfg = Config.from_env()
    assert cfg.big_model == "custom-big"
    assert cfg.small_model == "custom-small"
    assert cfg.api_key == "secret"
    assert cfg.max_steps == 10
    assert cfg.cost_ceiling_usd == 1.50
