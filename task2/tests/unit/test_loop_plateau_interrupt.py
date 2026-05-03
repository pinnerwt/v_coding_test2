"""Plateau interrupt: at PLATEAU_INTERRUPT consecutive non-novel obs,
force the agent to call `reason` (or done/ask) before the
NO_PROGRESS_GIVEUP backstop fires at 9. Splits one stuck-spiral into
1 reflection step + at-most-5 recovery attempts."""

from agent.loop import NO_PROGRESS_GIVEUP, PLATEAU_INTERRUPT


def test_plateau_interrupt_threshold_below_giveup():
    """PLATEAU_INTERRUPT must fire strictly before NO_PROGRESS_GIVEUP,
    leaving room for the agent to recover after the forced reason."""
    assert PLATEAU_INTERRUPT < NO_PROGRESS_GIVEUP
    assert PLATEAU_INTERRUPT == 4
