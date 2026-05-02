from agent.config import Config


def test_defaults(monkeypatch):
    for k in (
        "AGENT_MODEL_BASE_URL",
        "AGENT_MODEL_NAME",
        "SUMMARIZER_MODEL_BASE_URL",
        "SUMMARIZER_MODEL_NAME",
        "MAX_STEPS",
        "URL_NOTE_QUERY_STRIP",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.agent_model_base_url == "https://api.deepseek.com"
    assert cfg.agent_model_name == "deepseek-v4-flash"
    assert cfg.summarizer_model_base_url == "https://api.deepseek.com"
    assert cfg.summarizer_model_name == "deepseek-v4-flash"
    assert cfg.max_steps == 50
    assert cfg.url_note_query_strip is True
    assert cfg.agent_api_key is None
    assert cfg.summarizer_api_key is None


def test_deepseek_api_key_picked_up(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    cfg = Config.from_env()
    assert cfg.agent_api_key == "sk-test-123"
    assert cfg.summarizer_api_key == "sk-test-123"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MAX_STEPS", "10")
    monkeypatch.setenv("URL_NOTE_QUERY_STRIP", "false")
    cfg = Config.from_env()
    assert cfg.agent_model_base_url == "https://example.test/v1"
    assert cfg.max_steps == 10
    assert cfg.url_note_query_strip is False


def test_restrict_goto_defaults_true(monkeypatch):
    monkeypatch.delenv("AGENT_RESTRICT_GOTO", raising=False)
    cfg = Config.from_env()
    assert cfg.restrict_goto is True


def test_restrict_goto_false_when_env_false(monkeypatch):
    monkeypatch.setenv("AGENT_RESTRICT_GOTO", "false")
    cfg = Config.from_env()
    assert cfg.restrict_goto is False
