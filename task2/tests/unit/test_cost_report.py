from pathlib import Path

import pytest
from cost_report import (
    DEFAULT_PRICES,
    analyze_sidecar,
    price_usage,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sidecar_minimal.llm.jsonl"


def test_price_usage_splits_cache_hit_and_miss():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 200,
    }
    prices = {"input_miss": 0.27, "input_hit": 0.07, "output": 1.10}
    expected = (200 * 0.27 + 800 * 0.07 + 100 * 1.10) / 1_000_000
    assert price_usage(usage, prices) == pytest.approx(expected)


def test_price_usage_falls_back_to_prompt_tokens_when_no_cache_split():
    """Some providers don't return cache hit/miss — treat all as miss."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    prices = {"input_miss": 0.27, "input_hit": 0.07, "output": 1.10}
    expected = (1000 * 0.27 + 100 * 1.10) / 1_000_000
    assert price_usage(usage, prices) == pytest.approx(expected)


def test_price_usage_trusts_partial_cache_split_as_is():
    """If only one of hit/miss is present, the other is treated as 0
    (matching DeepSeek's both-or-neither contract)."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_miss_tokens": 200}
    prices = {"input_miss": 0.27, "input_hit": 0.07, "output": 1.10}
    expected = (200 * 0.27 + 0 * 0.07 + 100 * 1.10) / 1_000_000
    assert price_usage(usage, prices) == pytest.approx(expected)


def test_analyze_sidecar_aggregates_calls_and_tokens():
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    assert report["calls"] == 2
    assert report["prompt_tokens"] == 3000
    assert report["completion_tokens"] == 150
    assert report["cache_hit_tokens"] == 2300
    assert report["cache_miss_tokens"] == 700
    assert report["usd"] > 0


def test_analyze_sidecar_role_attribution_sums_to_one():
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    pcts = report["role_pct"]
    assert set(pcts.keys()) >= {"system", "user", "assistant", "tool"}
    assert sum(pcts.values()) == pytest.approx(1.0, abs=1e-6)


def test_analyze_sidecar_uses_original_chars_for_truncated_content():
    """Truncated `tool` content must contribute its original_chars,
    not the truncated prefix length, to role attribution."""
    report = analyze_sidecar(FIXTURE, prices_table=DEFAULT_PRICES)
    assert report["role_pct"]["tool"] > report["role_pct"]["system"]


def test_analyze_sidecar_skips_malformed_lines(tmp_path):
    """A truncated/corrupt last line should be skipped and counted, not crash."""
    p = tmp_path / "x.llm.jsonl"
    # One good line, one truncated/corrupt line.
    good_line = FIXTURE.read_text().splitlines()[0]
    corrupt_line = '{"call_idx": 99, "request": {"model": "deepseek-chat'  # unterminated
    p.write_text(good_line + "\n" + corrupt_line + "\n")

    report = analyze_sidecar(p, prices_table=DEFAULT_PRICES)
    assert report["calls"] == 1
    assert report["malformed_lines"] == 1
