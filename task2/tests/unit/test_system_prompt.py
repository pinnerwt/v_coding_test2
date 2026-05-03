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
    assert "Recent observations (last 5)" in _SYSTEM, (
        "prompt must point the LLM at the `## Recent observations (last 5)` window"
    )


def test_system_prompt_drops_legacy_thought_field() -> None:
    assert "thought" not in _SYSTEM, "prompt must not reference the obsolete `thought` field"


def test_system_prompt_does_not_bench_max() -> None:
    """The prompt must not embed verbatim strings, values, or page wording
    pulled from any specific WebVoyager bench case. Examples drawn from a
    bench case bias the agent toward that case (bench-maxxing) and don't
    generalize. Keep examples placeholder-shaped (`<value>`, `License: X`,
    etc.) — never a real license string, real download count, real
    repository name, or real headline from a bench task."""
    flat = _SYSTEM.lower()
    banned = [
        # Specific values from bench traces.
        "apache-2.0",
        "16,267",
        "59,218,905",
        "59,513,990",
        "14,254,039",
        "99.9%",
        # Specific page wording from bench tasks.
        "downloads last month",
        "total downloads",
        # Specific entities/repos/models named in bench tasks.
        "aspect",
        "clauser",
        "zeilinger",
        "huggingface/transformers",
        "pytorch/pytorch",
        "mistral",
        "bert-base",
        "rtx 3090",
        "tokyo",
    ]
    found = [s for s in banned if s in flat]
    assert not found, (
        f"prompt contains bench-specific strings: {found}. Replace with "
        f'generic placeholder examples (e.g. `License: X` → `answer="X"`).'
    )


def test_system_prompt_keeps_list_interactive_grounding() -> None:
    assert "list_interactive" in _SYSTEM, (
        "prompt must keep the rule that element IDs come from list_interactive"
    )


def test_system_prompt_keeps_rendered_value_caveat() -> None:
    """The rendered-value caveat tells the agent: when the goal asks for a
    value the page doesn't render exactly, commit the rendered value with a
    short caveat instead of searching indefinitely. This test used to assert
    the bench-specific 'total downloads' vs 'Downloads last month' example
    stayed verbatim — that was bench-maxxing. The behavior is what matters,
    not the example wording."""
    flat = re.sub(r"\s+", " ", _SYSTEM).lower()
    assert "rendered value" in flat, (
        "prompt must explicitly tell the agent to commit the rendered value "
        "with a one-line caveat rather than searching indefinitely"
    )
    assert "caveat" in flat, (
        "prompt must use the word 'caveat' so the rule is searchable / nameable"
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


def test_system_prompt_answer_must_be_bare_value_not_sentence() -> None:
    """The validator requires answer⊂evidence (after normalize). If the agent
    returns 'The license is apache-2.0.' with evidence 'License:\\napache-2.0',
    containment fails because the answer is longer than the evidence. The
    prompt must steer the LLM toward a bare-value answer rather than a
    sentence/paragraph framing — across 9 traces the dominant failure mode.
    """
    flat = re.sub(r"\s+", " ", _SYSTEM).lower()
    # Some signal that the prompt forbids essay-style answers.
    forbids_essay = (
        "not a sentence" in flat
        or "not a full sentence" in flat
        or "bare value" in flat
        or "shortest substring" in flat
        or "smallest substring" in flat
    )
    assert forbids_essay, (
        "prompt must steer the agent away from essay-style answers — the "
        "answer must be the bare value (a short substring of the evidence), "
        "not a full sentence wrapping the value"
    )
    # And it should give a right/wrong shape contrast so the LLM has a
    # pattern to imitate (just stating the rule isn't enough — without an
    # example the LLM defaults to sentence framing). The example must be
    # generic / placeholder-shaped, not a verbatim string from any bench
    # case (no bench-maxxing).
    assert "right:" in flat and "wrong:" in flat, (
        "prompt should contrast a right-shape answer with a wrong-shape one "
        "(generic placeholders, not bench-specific strings)"
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
