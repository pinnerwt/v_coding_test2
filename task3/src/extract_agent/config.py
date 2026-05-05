from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    base_url: str
    big_model: str
    small_model: str
    api_key: str | None
    max_steps: int
    cost_ceiling_usd: float

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            base_url=os.getenv("AGENT_MODEL_BASE_URL", "https://api.deepseek.com"),
            big_model=os.getenv("AGENT_MODEL_BIG", "deepseek-v4-pro"),
            small_model=os.getenv("AGENT_MODEL_SMALL", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            max_steps=int(os.getenv("MAX_STEPS", "30")),
            cost_ceiling_usd=float(os.getenv("COST_CEILING_USD", "0.50")),
        )
