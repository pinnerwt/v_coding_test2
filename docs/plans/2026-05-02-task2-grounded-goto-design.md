# Grounded `goto` — Constrained-Prevent Design

## Problem

WebVoyager Tier1 against our agent + local Qwen 27B scored 9/12. Closer
inspection of case 104 (GAN paper year on arXiv) showed the agent
called `goto https://arxiv.org/abs/1406.2661` despite that arXiv ID
appearing nowhere in any prior `read`/`list_interactive` observation —
it recalled the canonical ID from training data. The browser
interaction was theater: the LLM could have answered "2014" at step 0.

Field research confirms this is a known problem (Online-Mind2Web,
"Illusion of Progress?", arXiv:2504.01382 — a no-browsing Google-only
agent solves 51% of WebVoyager). Term: **shortcut-free evaluation**.

## Goal

Force the agent to ground navigation in observed page content. Concretely:
prevent `goto` to URLs not present in any prior tape observation or in
the goal text. Click and type are unaffected — clicked links are
inherently grounded.

## Approach: constrained-prevent (vs. detect-only)

Considered detect-only (post-hoc grounding score) and prevent. Picked
prevent because:
- Cheaper net effort than building an LLM-judge for detection.
- Produces a directly trustworthy headline number, not a "raw 9, but
  grounded 5" two-number report that needs explanation.
- AgentOccam (arXiv:2410.13825) reports +9.8 absolute success points
  from stripping `goto` entirely; constraining it (rather than
  removing) preserves the start-URL workflow.

## Mechanism

### Enforcement point

Inside the `goto` tool handler. Deterministic, doesn't depend on the
LLM following a system-prompt instruction (which the research shows
fails).

### Allowlist construction (per call)

Before each `goto(url)`:
1. Concatenate the goal text + every prior tape entry's `obs` string +
   prior `browser.page.url` after each successful goto.
2. Extract URL-shaped substrings (`https?://[^\s)\"']+`) — call this
   set the allowlist.

### Decision rule

`url` is allowed iff:
- The goal contains no URL at all (constraint disabled for the session — see "Bootstrap" below), OR
- `url` (case-insensitively) appears as a substring of any allowlisted
  URL. The match is one-directional: requested ⊆ observed.

So if `https://en.wikipedia.org/wiki/Tokyo` was observed, both
`goto https://en.wikipedia.org/wiki/Tokyo` and the prefix
`goto https://en.wikipedia.org` are allowed (shortening). But
`goto https://arxiv.org/abs/1406.2661` when only `https://arxiv.org`
was observed is blocked (extending past the observed prefix would let
the agent recall arbitrary deeper paths from training data — exactly
the GAN shortcut this design exists to stop).

### Block behavior

`goto` returns an error observation:

```
ERROR: blocked goto to <url> — URL not present in prior observations or goal.
Use list_interactive + click to navigate.
```

The step counts toward the budget; the loop continues. Trace gets a
`goto_blocked` event so the SPA can render a distinct card.

### Bootstrap (no URL in goal)

If the goal text contains no URL at all, the constraint is disabled
for that session. Pragmatic — the agent has no anchor and would
otherwise be unable to navigate at all. Only fires for free-form goals
without an embedded start URL.

### Redirect handling

After every successful `goto`, add the resolved `browser.page.url` to
the tape via the existing observation. So `goto arxiv.org` →
browser lands on `https://www.arxiv.org/`; the resolved URL is in the
next observation; subsequent `goto`s anchored on the www variant pass.

### Click-derived URLs

`list_interactive` returns `{role, name}` per element — no `href`. So
unclicked search-result URLs do **not** enter the allowlist. The agent
must click results, not URL-jump to them. This is intentional and the
core grounding signal.

## Observability

- **Trace event:** `{"type": "goto_blocked", "payload": {"url": "<requested>", "reason": "not in observation allowlist"}}`.
- **SPA card:** distinct visual treatment (yellow/warning) so blocks
  are scannable in the transcript.
- **Bench column:** `bench_webvoyager.py` reads each session's trace
  after the run and reports a `blocks` column. Lets us distinguish
  "succeeded after one block" from "blocked and gave up."

## Escape hatch

`AGENT_RESTRICT_GOTO` env var, default `true`. Set to `false` to
restore the unconstrained behavior — used for the apples-to-apples
baseline re-bench and for debugging. Single boolean, no fine-grained
relaxation knobs.

Plumbed through `Config.from_env()` and threaded into the `goto` tool
construction.

## Testing

Unit:
- `goto` blocked when URL not in tape.
- `goto` allowed when URL is a substring of an observed URL.
- `goto` allowed when an observed URL contains the requested URL as a
  prefix.
- `goto` allowed when goal has no URL (constraint off for session).
- Resolved redirect URL enters allowlist.
- `AGENT_RESTRICT_GOTO=false` disables the check entirely.

Integration:
- Full loop with a scripted LLM that tries the GAN-shortcut: expects a
  blocked observation, then a successful path via `click`.

Eval:
- Re-run WebVoyager Tier1 with restriction on.
- Compare per-case status + step count + `goto_blocked` count against
  the baseline run from this session.
- Headline metric: shortcut-free success rate (Tier1).

## Out of scope

- Detection / post-hoc grounding score for ungrounded answers (e.g.
  the LLM types the GAN answer "2014" without ever fetching the
  abstract). This design only targets URL-typing shortcuts.
- LLM-as-judge over screenshots (WebJudge-style).
- Removing `goto` entirely (AgentOccam approach) — too aggressive for
  a benchmark that includes goals with explicit start URLs.
- Click-and-type restrictions (unnecessary; clicked links are
  inherently grounded).
