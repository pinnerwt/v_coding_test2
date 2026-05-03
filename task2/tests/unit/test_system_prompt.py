"""Regression test for the agent's system prompt.

The prompt teaches the LLM about the new `reason` field, the `## Action history`
narrative section that the system prompt carries, and the `## Recent observations
(last 3)` window in the user message. The legacy `thought` term must be gone.
"""

from agent.loop import _SYSTEM


def test_system_prompt_mentions_reason_field() -> None:
    assert "reason" in _SYSTEM, "prompt must mention the mandatory `reason` field"


def test_system_prompt_mentions_action_history() -> None:
    assert "Action history" in _SYSTEM, (
        "prompt must point the LLM at the `## Action history` narrative"
    )


def test_system_prompt_mentions_recent_observations() -> None:
    assert "Recent observations" in _SYSTEM, (
        "prompt must point the LLM at the `## Recent observations (last 3)` window"
    )


def test_system_prompt_drops_legacy_thought_field() -> None:
    assert "thought" not in _SYSTEM, (
        "prompt must not reference the obsolete `thought` field"
    )
