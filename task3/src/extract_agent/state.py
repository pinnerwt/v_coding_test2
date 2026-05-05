from __future__ import annotations


class SessionState:
    def __init__(self) -> None:
        self.text_store: dict[str, str] = {}
        self.anchors: list[dict] | None = None
        self.records: list[dict] | None = None
        self.diagnostic: dict | None = None
        self.cost_usd: float = 0.0
        self.steps: int = 0
        self.inputs: dict = {}
        self._next_text_id: int = 0

    def store_text(self, text: str) -> str:
        tid = f"t{self._next_text_id}"
        self._next_text_id += 1
        self.text_store[tid] = text
        return tid

    def get_text(self, text_id: str) -> str:
        return self.text_store[text_id]

    def add_cost(self, usd: float) -> None:
        self.cost_usd += usd

    def bump_step(self) -> None:
        self.steps += 1
