from agent.config import Config


def test_defaults(monkeypatch):
    for k in (
        "AGENT_MODEL_BASE_URL",
        "AGENT_MODEL_NAME",
        "MAX_STEPS",
        "URL_NOTE_QUERY_STRIP",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = Config.from_env()
    assert cfg.agent_model_base_url == "https://api.deepseek.com"
    assert cfg.agent_model_name == "deepseek-chat"
    assert cfg.max_steps == 50
    assert cfg.url_note_query_strip is True
    assert cfg.agent_api_key is None


def test_deepseek_api_key_picked_up(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    cfg = Config.from_env()
    assert cfg.agent_api_key == "sk-test-123"


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


def test_config_loads_anti_loop_defaults(monkeypatch):
    for k in ("SMALL_DIFF_THRESHOLD", "MAX_AUTO_ADVANCE_HOPS", "DIFF_INJECT_MAX_LINES"):
        monkeypatch.delenv(k, raising=False)
    from agent.config import Config

    c = Config.from_env()
    assert c.small_diff_threshold == 50
    assert c.max_auto_advance_hops == 32
    assert c.diff_inject_max_lines == 50


def test_config_overrides_anti_loop_via_env(monkeypatch):
    monkeypatch.setenv("SMALL_DIFF_THRESHOLD", "200")
    monkeypatch.setenv("MAX_AUTO_ADVANCE_HOPS", "8")
    monkeypatch.setenv("DIFF_INJECT_MAX_LINES", "5")
    from agent.config import Config

    c = Config.from_env()
    assert c.small_diff_threshold == 200
    assert c.max_auto_advance_hops == 8
    assert c.diff_inject_max_lines == 5
