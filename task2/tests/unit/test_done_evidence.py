import pytest

from agent.tools.meta import LoopDone, QuestionChannel, build_meta_tools


def _done(prior_obs: list[str]):
    """Return a done() bound to a tape composed of the given prior obs."""
    tape = [{"action": "read", "obs": o} for o in prior_obs]
    qc = QuestionChannel()
    fns = build_meta_tools(question_channel=qc, tape=tape)
    return fns["done"]


@pytest.mark.asyncio
async def test_done_success_requires_non_empty_evidence():
    done = _done(["alpha beta gamma"])
    with pytest.raises(ValueError, match="evidence"):
        await done(status="success", answer="alpha")


@pytest.mark.asyncio
async def test_done_success_evidence_must_be_in_prior_obs():
    done = _done(["alpha beta gamma"])
    with pytest.raises(ValueError, match="not found in any prior observation"):
        await done(status="success", answer="alpha", evidence="omega delta sigma")


@pytest.mark.asyncio
async def test_done_success_answer_must_be_in_evidence():
    done = _done(["alpha beta gamma delta epsilon"])
    with pytest.raises(ValueError, match="answer.*not contained in evidence"):
        await done(status="success", answer="omega", evidence="alpha beta gamma")


@pytest.mark.asyncio
async def test_done_success_evidence_min_length_10():
    done = _done(["the answer is 9 today"])
    with pytest.raises(ValueError, match="at least 10"):
        await done(status="success", answer="9", evidence="9")


@pytest.mark.asyncio
async def test_done_success_passes_when_evidence_grounded_and_contains_answer():
    done = _done(["The Definite integral evaluates to 9 over [0,3]"])
    with pytest.raises(LoopDone) as e:
        await done(
            status="success",
            answer="9",
            evidence="evaluates to 9 over [0,3]",
        )
    assert e.value.status == "success" and e.value.answer == "9"


@pytest.mark.asyncio
async def test_done_failed_evidence_optional():
    done = _done(["page text"])
    with pytest.raises(LoopDone) as e:
        await done(status="failed", answer="couldn't find it")
    assert e.value.status == "failed"


@pytest.mark.asyncio
async def test_done_failed_evidence_when_provided_must_be_in_prior_obs():
    done = _done(["page text"])
    with pytest.raises(ValueError, match="not found in any prior observation"):
        await done(
            status="failed",
            answer="blocked",
            evidence="this string is not on the page",
        )


@pytest.mark.asyncio
async def test_done_evidence_normalization_whitespace_and_case():
    done = _done(["The Quick Brown Fox\nJumps Over"])
    with pytest.raises(LoopDone):
        await done(
            status="success",
            answer="quick brown fox",
            evidence="the   QUICK brown   fox",
        )


@pytest.mark.asyncio
async def test_done_answer_with_parens_matches_unparenthesized_evidence():
    """Regression for trace 7bd13ccc — agent identified Python at 99.9% from a
    GitHub repo's Languages sidebar, then burned 9 steps trying to satisfy the
    answer⊂evidence check because 'Python (99.9%)' has parens that the page
    text 'Python\\n99.9%' does not. Bracket characters must not break the
    containment check; the answer is factually present in the evidence."""
    done = _done(["Languages\nPython\n99.9%\n \nOther\n0.1%"])
    with pytest.raises(LoopDone) as e:
        await done(
            status="success",
            answer="Python (99.9%)",
            evidence="Languages\nPython\n99.9%\nOther\n0.1%",
        )
    assert e.value.status == "success" and e.value.answer == "Python (99.9%)"


@pytest.mark.asyncio
async def test_done_answer_with_brackets_matches_unbracketed_evidence():
    """Same shape, square brackets — common when answers cite a range like
    '9 [over 0,3]' but the page just said '9 over 0,3'."""
    done = _done(["the integral evaluates to 9 over 0,3 by FTC"])
    with pytest.raises(LoopDone):
        await done(
            status="success",
            answer="9 [over 0,3]",
            evidence="evaluates to 9 over 0,3",
        )
