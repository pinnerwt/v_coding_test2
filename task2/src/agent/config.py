from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(s: str | None, default: bool) -> bool:
    if s is None:
        return default
    return s.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    agent_model_base_url: str
    agent_model_name: str
    agent_api_key: str | None
    agent_auth_token: str | None
    max_steps: int
    url_note_query_strip: bool
    small_diff_threshold: int
    max_auto_advance_hops: int
    diff_inject_max_lines: int

    @classmethod
    def from_env(cls) -> Config:
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        auth_token = os.getenv("AGENT_AUTH_TOKEN") or None
        return cls(
            agent_model_base_url=os.getenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com"),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", "deepseek-chat"),
            agent_api_key=deepseek_key,
            agent_auth_token=auth_token,
            max_steps=int(os.getenv("MAX_STEPS", "50")),
            url_note_query_strip=_bool(os.getenv("URL_NOTE_QUERY_STRIP"), True),
            small_diff_threshold=int(os.getenv("SMALL_DIFF_THRESHOLD", "50")),
            max_auto_advance_hops=int(os.getenv("MAX_AUTO_ADVANCE_HOPS", "32")),
            diff_inject_max_lines=int(os.getenv("DIFF_INJECT_MAX_LINES", "50")),
        )
