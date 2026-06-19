# Pattern Strategy — Automated Daily Scheduler

This document describes the automated tasks that run each morning for the
price-action ("Pattern Strategy") trading bot, what each one does, and how to
manage them. It is safe to share with the team.

All tasks run locally on the trading machine via the Cowork scheduler. **They
run only while the Cowork app is open** — if the app is closed when a task is
due, it runs on the next launch.

---

## Daily routine at a glance

| Time (local) | Task | Purpose | Type |
|---|---|---|---|
| 08:03 | `slc-volume-gate-shadow-check` | Shadow-tests the volume gate; applies it only if it raises win rate | Auto-tuning (guarded) |
| 08:06 | `daily-trading-bot-review` | Morning performance review + tuning recommendations | Reporting |
| 08:21 | `pattern-hallucination-check` | **Verifies detected chart patterns are real (not hallucinated)** | Integrity (read-only) |
| 08:57 | `slc-agent-grounding-check` | Anti-hallucination audit of the auto-tuning agent (acts only on healthy data within its rules) | Integrity (read-only) |

All four are enabled and recur every day.

---

## Pattern hallucination / sanity check (the integrity guard)

**Task ID:** `pattern-hallucination-check`  ·  **Schedule:** daily ~08:21 local  ·  **Read-only**

### What it checks

For each recent trade, it re-derives the pattern the bot *claimed* to see from
the signal notes and verifies it against the actual OHLC bars that supposedly
formed it. A signal fails the audit if any of these don't hold:

1. **Peaks/troughs exist** — the claimed Double-Top peaks (or Double-Bottom
   troughs), and the valley/crest between them, match real bar highs/lows.
2. **Entry = pattern level** — the entry equals the R2 retest level and sits
   inside the bar window (a level outside the window = a fabricated price).
3. **Stop placement** — the stop is on the correct side and beyond the pattern
   extreme.
4. **Reward:risk** — the take-profit/stop geometry matches the signal's stated RR.
5. **Direction** — the trade side matches the pattern type (DT/H&S/TT → sell,
   DB/Inverse-H&S/TB → buy).
6. **Clarity** — the clarity score is within 0–100.

The audit is **deterministic** — it reads stored data and does arithmetic only,
so the check itself cannot hallucinate. It has been verified to PASS valid
patterns and FAIL fabricated ones (a peak not in the bars, an RR mismatch, a
wrong-side stop, or an out-of-window level all trip it).

### What it reports

One line per trade — `✓ PASS`, `▲ WARN`, or `✗ FAIL` — a summary, and a verdict:

- **PASS** — every pattern is grounded in its bars.
- **WARN** — minor mismatches worth a look; not necessarily hallucination.
- **FAIL** — the strategy logged a pattern the bars don't support. Treated as
  urgent: new signals should not be trusted until the detector is investigated.

### Underlying script

The task runs this committed, version-controlled script (read-only, no network):

```
trading-bot/tools/pattern_sanity_check.py
    --n 30          # audit the last 30 trades (default 15)
    --symbol CADJPY # optional: restrict to one symbol
```

It reads `trading-bot/state/trade_journal/*.json` (each journal stores the
signal plus the bars that formed the pattern). Exit code: `0` all clean,
`2` warnings only, `1` one or more FAIL.

### Task definition (scheduler SKILL.md)

```markdown
---
name: pattern-hallucination-check
description: Daily pattern-strategy hallucination check: verify detected patterns are grounded in the actual bars.
schedule: "12 8 * * *"   # daily, ~08:21 local after jitter
---

Daily PATTERN-STRATEGY hallucination / sanity check for the price-action trading bot.

Purpose: confirm the bot is NOT "seeing" chart patterns that aren't actually in
the price data — fabricated Double Top/Bottom peaks or troughs, levels that
don't exist in the bars, or signals whose entry/stop/target geometry is
internally inconsistent.

Steps (READ-ONLY — do not modify any files, config, or the running bot):

1. Run the deterministic checker via the workspace bash:
   cd "<BOT_DIR>/trading-bot" && python3 tools/pattern_sanity_check.py --n 30
   It reads state/trade_journal/*.json and needs no network.

2. Read its output: one line per trade (✓ PASS / ▲ WARN / ✗ FAIL), a Summary
   line, and a final VERDICT. Exit code 0 = clean, 2 = warnings, 1 = FAIL.

3. Report concisely: the overall VERDICT and counts (clean / warn / FAIL). For
   every FAIL or WARN, list the ticket, symbol, setup, and the specific issue.

4. If the VERDICT is FAIL, treat it as urgent: state plainly that the pattern
   detector may be hallucinating and that new signals should not be trusted
   until it is investigated. WARN = worth a look. PASS = one-line all-clear.

Keep it short — this is a daily integrity check, not a performance review.
```

> Replace `<BOT_DIR>` with the bot's install path on the machine running it.

---

## The other scheduled tasks (brief)

- **`slc-volume-gate-shadow-check`** (08:03) — runs the volume-gate shadow test
  and only applies the gate if it measurably improves win rate. Guarded
  auto-tuning, not a hard change.
- **`daily-trading-bot-review`** (08:06) — produces the morning performance
  review with tuning recommendations.
- **`slc-agent-grounding-check`** (08:57) — anti-hallucination audit of the
  *auto-tuning* agent: confirms it only acts on healthy (non-contaminated) data
  and within its configured rules. Complements the pattern check above, which
  targets the *detector* rather than the tuner.

---

## Managing the schedule

- **Where:** the Cowork app → **Scheduled** section in the sidebar lists all
  tasks; each can be run on demand ("Run now"), edited, paused, or removed.
- **Pre-approve tools:** hitting **Run now** once approves the permissions a
  task needs (e.g. shell access) so future automatic runs don't pause waiting
  for approval.
- **App must be open:** scheduled runs fire only while Cowork is running; a task
  due while it's closed runs at the next launch.
- **Times are local** to the machine, with a small random jitter (seconds) to
  avoid all tasks firing on the exact same second.

---

*Generated 16 Jun 2026. Integrity checks are read-only and never modify the bot,
its config, or the ledger.*
