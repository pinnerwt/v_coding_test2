import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from extract_agent.classify import classify_records, eligible_indices


def _make_record(item_number, body, status="extracted"):
    return {
        "part": "I",
        "item_number": item_number,
        "item_title": f"Item {item_number}",
        "char_range": [0, len(body)],
        "status": status,
        "content_text": body,
    }


def test_eligibility_skips_short_bodies():
    records = [
        _make_record("4", "Not applicable.", status="not_applicable"),
        _make_record("1", "x" * 500 + " incorporated by reference " + "x" * 500),
        _make_record("10", "x" * 500),  # no IBR phrase
    ]
    assert eligible_indices(records) == [1]


def test_eligibility_skips_huge_item_15():
    records = [
        _make_record("15", "x incorporated by reference " * 30000),
    ]
    assert eligible_indices(records) == []


def test_eligibility_requires_status_key():
    records = [
        {
            "item_number": "1",
            "content_text": "x" * 500 + " incorporated by reference",
            "item_title": "X",
            "part": "I",
            "char_range": [0, 0],
        }
    ]
    assert eligible_indices(records) == []


@pytest.mark.asyncio
async def test_classify_records_no_eligible_returns_unchanged():
    records = [_make_record("4", "Not applicable.", status="not_applicable")]
    new, rejections, n_calls = await classify_records(records, client=MagicMock(), model="x")
    assert new == records
    assert rejections == []
    assert n_calls == 0


@pytest.mark.asyncio
async def test_classify_records_dispatches_per_eligible():
    body = (
        "PROLOGUE intro paragraph for the test. "
        + "Substantive content here. " * 30
        + "Information is incorporated by reference to the proxy statement. "
        + "Trailing closing paragraph for the test."
    )
    records = [_make_record("11", body)]
    fake_segments = [
        {
            "status": "extracted",
            "starts_with": "PROLOGUE intro paragraph",
            "ends_with": "Substantive content here.",
        },
        {
            "status": "incorporated_by_reference",
            "starts_with": "Information is incorporated by reference",
            "ends_with": "the proxy statement.",
        },
        {
            "status": "extracted",
            "starts_with": "Trailing closing paragraph",
            "ends_with": "for the test.",
        },
    ]
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(
        return_value=(
            {"role": "assistant", "content": json.dumps({"segments": fake_segments})},
            None,
        )
    )
    new_records, rejections, n_calls = await classify_records(
        records, client=fake_client, model="x"
    )
    assert n_calls == 1
    assert rejections == []
    assert len(new_records) == 3
    assert [r["status"] for r in new_records] == [
        "extracted",
        "incorporated_by_reference",
        "extracted",
    ]
    fake_client.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_records_records_bad_json_rejection():
    body = "x" * 100 + " incorporated by reference " + "y" * 100
    records = [_make_record("11", body)]
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(
        return_value=({"role": "assistant", "content": "not json at all"}, None)
    )
    new_records, rejections, n_calls = await classify_records(
        records, client=fake_client, model="x"
    )
    assert n_calls == 1
    assert new_records == records
    assert any("bad small-model JSON" in r for r in rejections)


@pytest.mark.asyncio
async def test_classify_records_isolates_one_failing_call():
    body = (
        "PROLOGUE intro paragraph for record A. "
        + "Substantive content here. " * 30
        + "Information is incorporated by reference to the proxy statement. "
        + "Trailing closing paragraph for record A."
    )
    body_b = (
        "PROLOGUE intro paragraph for record B. "
        + "Other substantive content here. " * 30
        + "Information is incorporated by reference to the proxy statement. "
        + "Trailing closing paragraph for record B."
    )
    records = [_make_record("11", body), _make_record("12", body_b)]
    fake_segments = [
        {
            "status": "extracted",
            "starts_with": "PROLOGUE intro paragraph for record A",
            "ends_with": "Substantive content here.",
        },
        {
            "status": "incorporated_by_reference",
            "starts_with": "Information is incorporated by reference",
            "ends_with": "the proxy statement.",
        },
        {
            "status": "extracted",
            "starts_with": "Trailing closing paragraph for record A",
            "ends_with": "for record A.",
        },
    ]
    good_response = (
        {"role": "assistant", "content": json.dumps({"segments": fake_segments})},
        None,
    )
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(side_effect=[good_response, RuntimeError("transport boom")])
    new_records, rejections, n_calls = await classify_records(
        records, client=fake_client, model="x"
    )
    assert n_calls == 2
    # Record 0 was split into 3 sub-records; record 1 passes through unchanged
    assert len(new_records) == 4
    assert any("small-model call failed" in r and "12" in r for r in rejections)


@pytest.mark.asyncio
async def test_classify_records_strips_markdown_json_fence():
    body = (
        "PROLOGUE intro fenced. "
        + "Substantive content here. " * 30
        + "Information is incorporated by reference to the proxy statement. "
        + "Trailing closing fenced."
    )
    records = [_make_record("11", body)]
    fake_segments = [
        {
            "status": "extracted",
            "starts_with": "PROLOGUE intro fenced",
            "ends_with": "Substantive content here.",
        },
        {
            "status": "incorporated_by_reference",
            "starts_with": "Information is incorporated by reference",
            "ends_with": "the proxy statement.",
        },
        {"status": "extracted", "starts_with": "Trailing closing fenced", "ends_with": "fenced."},
    ]
    fenced = "```json\n" + json.dumps({"segments": fake_segments}) + "\n```"
    fake_client = MagicMock()
    fake_client.chat = AsyncMock(return_value=({"role": "assistant", "content": fenced}, None))
    new_records, rejections, n_calls = await classify_records(
        records, client=fake_client, model="x"
    )
    assert n_calls == 1
    assert rejections == []
    assert len(new_records) == 3
