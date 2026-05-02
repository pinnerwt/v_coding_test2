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
        {"thought": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
        {"thought": "", "action": "read", "args": {}, "obs": "page A"},
        {"thought": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
        {"thought": "", "action": "read", "args": {"offset": 0}, "obs": "page A"},
        {"thought": "", "action": "click", "args": {"id": 94}, "obs": "clicked id=94"},
    ]
    user = _build(tape)
    assert "Calls so far" in user
    # The hot action and its count must appear verbatim
    assert 'click({"id": 94})' in user
    assert "×3" in user


def test_histogram_hidden_when_no_repeats():
    tape = [
        {"thought": "", "action": "goto", "args": {"url": "https://a"}, "obs": "ok"},
        {"thought": "", "action": "read", "args": {}, "obs": "page A"},
        {"thought": "", "action": "list_interactive", "args": {}, "obs": "[]"},
    ]
    user = _build(tape)
    assert "Calls so far" not in user


def test_novelty_tally_zero_when_recent_all_seen_before():
    """Same obs across 10+ steps with different args → novelty=0/10."""
    tape = [
        # Two seed observations to populate the "earlier" set
        {"thought": "", "action": "read", "args": {}, "obs": "X"},
        {"thought": "", "action": "list_interactive", "args": {}, "obs": "Y"},
    ]
    # 10 more steps, all yielding obs "X" or "Y" (already seen)
    for i in range(10):
        tape.append(
            {
                "thought": "",
                "action": "click",
                "args": {"id": i},
                "obs": "X" if i % 2 == 0 else "Y",
            }
        )
    user = _build(tape)
    assert "Novel observations in last 10 steps: 0/10" in user


def test_novelty_tally_full_when_each_obs_is_new():
    tape = [
        {"thought": "", "action": "read", "args": {"offset": k}, "obs": f"chunk-{k}"}
        for k in range(12)
    ]
    user = _build(tape)
    assert "Novel observations in last 10 steps: 10/10" in user


def test_novelty_tally_hidden_when_tape_too_short():
    tape = [
        {"thought": "", "action": "read", "args": {}, "obs": "X"} for _ in range(5)
    ]
    user = _build(tape)
    assert "Novel observations in last" not in user
