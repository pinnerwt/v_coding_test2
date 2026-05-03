"""Surface to the model: action-call histogram + observation-novelty tally.

Both signals are computed from the tape and rendered into the user message so
the model can self-detect loops the stuck-state-machine doesn't catch (e.g. the
canirun.ai trace where click(94)/list_interactive/read alternated for 40 steps
without three-in-a-row identical entries)."""

from agent.context import build_messages


def _build(tape):
    msgs = build_messages(
        system="SYS",
        goal="g",
        qa=[],
        url_notes="",
        tape=tape,
        page_header="URL=x",
        replan_hint=None,
    )
    return msgs[1]["content"]


def test_histogram_shown_when_any_action_repeats():
    tape = [
        {"reason": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
        {"reason": "", "action": "read", "args": {}, "obs": "page A"},
        {"reason": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
        {"reason": "", "action": "read", "args": {"offset": 0}, "obs": "page A"},
        {"reason": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
    ]
    user = _build(tape)
    assert "Calls so far" in user
    # The hot action and its count must appear verbatim
    assert 'click({"id": 94})' in user
    assert "×3" in user


def test_histogram_hidden_when_no_repeats():
    tape = [
        {"reason": "", "action": "goto", "args": {"url": "https://a"}, "obs": "ok"},
        {"reason": "", "action": "read", "args": {}, "obs": "page A"},
        {"reason": "", "action": "list_interactive", "args": {}, "obs": "[]"},
    ]
    user = _build(tape)
    assert "Calls so far" not in user


def test_novelty_tally_zero_when_recent_all_seen_before():
    """Same obs across NOVELTY_WINDOW+ steps with different args → novelty=0/N."""
    from agent.context import NOVELTY_WINDOW

    tape = [
        # Two seed observations to populate the "earlier" set
        {"reason": "", "action": "read", "args": {}, "obs": "X"},
        {"reason": "", "action": "list_interactive", "args": {}, "obs": "Y"},
    ]
    # NOVELTY_WINDOW more steps, all yielding obs "X" or "Y" (already seen)
    for i in range(NOVELTY_WINDOW):
        tape.append(
            {
                "reason": "",
                "action": "click",
                "args": {"id": i},
                "obs": "X" if i % 2 == 0 else "Y",
            }
        )
    user = _build(tape)
    assert f"Novel observations in last {NOVELTY_WINDOW} steps: 0/{NOVELTY_WINDOW}" in user


def test_novelty_tally_full_when_each_obs_is_new():
    from agent.context import NOVELTY_WINDOW

    tape = [
        {"reason": "", "action": "read", "args": {"offset": k}, "obs": f"chunk-{k}"}
        for k in range(NOVELTY_WINDOW + 2)
    ]
    user = _build(tape)
    assert (
        f"Novel observations in last {NOVELTY_WINDOW} steps: {NOVELTY_WINDOW}/{NOVELTY_WINDOW}"
        in user
    )


def test_novelty_tally_hidden_when_tape_too_short():
    tape = [{"reason": "", "action": "read", "args": {}, "obs": "X"} for _ in range(5)]
    user = _build(tape)
    assert "Novel observations in last" not in user
