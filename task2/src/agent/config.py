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
    summarizer_model_base_url: str
    summarizer_model_name: str
    max_steps: int
    url_note_query_strip: bool
    restrict_goto: bool

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            agent_model_base_url=os.getenv("AGENT_MODEL_BASE_URL", "http://localhost:8090/v1"),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", "qwen3.5-27b"),
            summarizer_model_base_url=os.getenv(
                "SUMMARIZER_MODEL_BASE_URL", "http://localhost:8090/v1"
            ),
            summarizer_model_name=os.getenv("SUMMARIZER_MODEL_NAME", "qwen3.5-27b"),
            max_steps=int(os.getenv("MAX_STEPS", "50")),
            url_note_query_strip=_bool(os.getenv("URL_NOTE_QUERY_STRIP"), True),
            restrict_goto=_bool(os.getenv("AGENT_RESTRICT_GOTO"), True),
        )
