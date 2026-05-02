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
    summarizer_model_base_url: str
    summarizer_model_name: str
    summarizer_api_key: str | None
    max_steps: int
    url_note_query_strip: bool
    restrict_goto: bool
    small_diff_threshold: int
    max_auto_advance_hops: int
    diff_inject_max_lines: int

    @classmethod
    def from_env(cls) -> Config:
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        return cls(
            agent_model_base_url=os.getenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com"),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", "deepseek-chat"),
            agent_api_key=deepseek_key,
            summarizer_model_base_url=os.getenv(
                "SUMMARIZER_MODEL_BASE_URL", "https://api.deepseek.com"
            ),
            summarizer_model_name=os.getenv("SUMMARIZER_MODEL_NAME", "deepseek-chat"),
            summarizer_api_key=deepseek_key,
            max_steps=int(os.getenv("MAX_STEPS", "50")),
            url_note_query_strip=_bool(os.getenv("URL_NOTE_QUERY_STRIP"), True),
            restrict_goto=_bool(os.getenv("AGENT_RESTRICT_GOTO"), True),
            small_diff_threshold=int(os.getenv("SMALL_DIFF_THRESHOLD", "500")),
            max_auto_advance_hops=int(os.getenv("MAX_AUTO_ADVANCE_HOPS", "32")),
            diff_inject_max_lines=int(os.getenv("DIFF_INJECT_MAX_LINES", "10")),
        )
