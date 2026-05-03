"""Regression test for the agent's system prompt.

The prompt teaches the LLM about the new `reason` field, the `## Action history`
narrative section that the system prompt carries, and the `## Recent observations
(last 3)` window in the user message. The legacy `thought` term must be gone.
"""

import re

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
    assert "thought" not in _SYSTEM, "prompt must not reference the obsolete `thought` field"


def test_system_prompt_keeps_list_interactive_grounding() -> None:
    assert "list_interactive" in _SYSTEM, (
        "prompt must keep the rule that element IDs come from list_interactive"
    )


def test_system_prompt_keeps_rendered_value_caveat() -> None:
    flat = re.sub(r"\s+", " ", _SYSTEM)
    assert "total downloads" in flat and "Downloads last month" in flat, (
        "prompt must keep the rendered-value caveat with the concrete example "
        "(downloads-last-month vs total-downloads) — earned its keep on bench"
    )
    assert "rendered value" in flat.lower(), (
        "prompt must explicitly tell the agent to commit the rendered value "
        "with a one-line caveat rather than searching indefinitely"
    )


def test_system_prompt_keeps_blocked_banner_rule() -> None:
    assert "[BLOCKED" in _SYSTEM and "blocked by" in _SYSTEM, (
        "prompt must keep the [BLOCKED: …] banner rule with the "
        'done(failed, "blocked by <wall>") shape'
    )


def test_system_prompt_keeps_final_step_done_rule() -> None:
    assert "final" in _SYSTEM.lower() and "done" in _SYSTEM, (
        "prompt must keep the final-step rule (only `done` available; do not stall)"
    )


def test_system_prompt_requires_evidence_on_done_success() -> None:
    """done(success, ...) must be paired with a verbatim `evidence` substring
    drawn from a prior read/read_grep observation. The prompt must teach this,
    or the LLM will fabricate citations and the loop will downgrade them."""
    flat = re.sub(r"\s+", " ", _SYSTEM)
    # The prompt must spell out the `evidence` argument on the done() success
    # form, not just mention the word "evidence" in passing.
    assert 'done(status="success"' in flat and "evidence=" in flat, (
        "prompt must show the done(success) signature including the new "
        "`evidence` argument so the LLM knows to populate it"
    )
    assert "substring" in flat.lower(), (
        "prompt must say evidence is a substring of a prior read/read_grep "
        "observation (the loop validates it as a substring)"
    )
    low = flat.lower()
    assert "fabricat" in low or "downgrade" in low or "rejected" in low, (
        "prompt must warn that ungrounded/fabricated evidence is rejected or "
        "downgraded to failed — otherwise the LLM will guess"
    )
