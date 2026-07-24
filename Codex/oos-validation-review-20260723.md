# Out-of-Sample Validation Review

Updated: 2026-07-23 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- execute the OOS validation the 2026-06-14 ALGO-IMPROVE-01 session flagged as the required
  next step but never ran ("Critic verdict: model at noise floor for in-sample tuning; next
  step is 2015-2020 OOS validation")
- report findings only; no scoring-logic changes made in this session pending direction

## Bug Found and Fixed

`scripts/run_ablation.py:452-456` (pre-fix): `vix_sl = vix.iloc[:i + 1]` sliced VIX by
**integer position**, not by date, while every other series (`sec_sl`, `vvix_sl`, `vix9d_sl`)
was correctly sliced by `v.index <= cutoff`. `backtest_export.py` already does this correctly
(`vix[vix.index <= cutoff_ts]`) — only `run_ablation.py` had the bug.

- **Impact at `--period 2y/5y`**: negligible in almost all cases, since SPX and VIX bar counts
  normally match 1:1 over short/medium windows. Confirmed via a live check: the 10y pull had
  VIX with **one extra bar** (2026-05-25 — Memorial Day; NYSE closed, yfinance still returned
  a VIX row), which would have silently shifted every VIX-derived signal by one trading day for
  the tail ~40 bars of any positional-slice run touching that period.
- **Impact at `--period max`**: catastrophic. SPX history (^GSPC, back to 1928) vastly exceeds
  VIX history (^VIX, back to 1990), so positional slicing fed VIX values from a totally
  unrelated era into signal computation for the entire pre-1990 span. A `--period max` run
  produced **8.7% baseline accuracy** (1135/12978) — mechanically impossible without a bug,
  confirming the misalignment rather than a real result.
- **Fix applied**: `vix_sl` now slices by `cutoff` date like every other series. Verified the
  fix doesn't change the 10y result (36.6% before and after — see below), confirming the 10y
  window itself is clean and the fix's practical effect is limited to windows that touch data
  gaps like the one found.
- **Status**: fixed in working tree, not yet committed pending sign-off on this note.

## OOS Test: 10-Year Walk-Forward (2017-05-10 → 2026-07-22)

Run: `python3 scripts/run_ablation.py --period 10y` (post-fix)

**Baseline accuracy: 36.6% (510/1392)** — n=1392 directional calls, current live signal set
(23+1opt core signals, all abstain gates active: gap-down, gap-up, group-agreement,
Thursday, strong-bear).

This is the first time the current signal/gate configuration has been evaluated against data
that was never part of any tuning decision. All ABLATION-PRUNE-*, STRONG-BEAR-01, VVIX-01,
and related choices in the git history were validated exclusively against the 2yr window
(2025-01-21 → 2026-04-01) or the 60d gate — both entirely inside the same bear/high-vol
"tariff-volatility regime" the codebase's own comments repeatedly reference.

**36.6% is 13.4pp below random (50%) over 1392 calls** — not "no edge," but a systematic
anti-correlation. A model that simply guessed randomly would outperform it; inverting every
call would score 63.4% on this exact dataset. That magnitude of miss is a strong signature of
a model whose rules encode the sign of a specific recent regime rather than a durable pattern.

### Signal-level ablation on the 10y window

Every single signal's removal delta falls within **-0.5% to +0.6%** (see
`/tmp/ablation-10y-fixed.md` for full table). No individual signal carries decade-scale edge —
this rules out "one bad signal is dragging the average down." The problem is the aggregate
architecture (thresholds, group weights, abstain gates), not a prunable component.

### Day-of-week reversal — concrete example

| Day | 2yr/60d (tuning window, historical) | 10yr OOS |
|-----|--------------------------------------|----------|
| Thu | weakest day (37-45%), motivated the Thursday bear-abstain gate | **best day (41.3%)** |
| Mon | not flagged | **worst day (30.1%)** |

The Thursday bear-abstain gate (`app.py`, `run_ablation.py:507-509`, `backtest_export.py`) was
added specifically because Thursday looked weak in the tuning window. The 10-year data shows
the opposite ranking. Diagnostic test — disabling the gate on the 10y run:

- With gate: 36.6% (510/1392)
- Without gate: 37.0% (543/1468) — the 76 previously-abstained Thursday bear calls hit 43.4%
  (33/76), better than the model's own 36.6% average.

**Conclusion**: the gate is mildly counter-productive OOS, but it is not the primary driver of
the 36.6% result (removing it only recovers 0.4pp) — it's one symptom of the broader pattern,
not the root cause. Documented here rather than fixed unilaterally, since the same in-sample
process that produced this gate produced the rest of the signal set and abstain logic too, and
a one-gate fix would understate the scope of the issue.

## Current-Window Degradation Since the Last Documented Validation

The 2026-06-14 ALGO-IMPROVE-01 commit reported: *"252d (2y window): 62.3% accuracy, 95% CI
[54.1%, 69.7%], n=122"*. Re-running the identical check today (six weeks later, same
methodology, rolling window now shifted forward):

| Check | 2026-06-14 (reported) | 2026-07-23 (today) |
|-------|------------------------|----------------------|
| 2yr rolling window | 62.3% (n=122) | **47.3% (86/182)** |
| 60d canonical gate | passing (≥52%) | **48.57% (17/35) — `"ok": false`, fails the 0.52 threshold** |

The live repo's own `backtest_export.py --days 60` right now reports `"ok": false"` against
its own 0.52 accuracy gate — this is the current, actual state of the model, not a
hypothetical. A 62.3% → 47.3% swing in six weeks on the "home turf" window that was supposed
to represent validated performance is itself evidence against durability, independent of the
10-year OOS result.

## Assessment

Three independent checks converge on the same conclusion:
1. True 10-year OOS: 36.6% (well below random, n=1392 — not a small-sample fluke)
2. The model fails its own canonical 60-day gate today: 48.57% vs required 52%
3. The "validated" 2yr figure decayed 62.3% → 47.3% in six weeks

This is consistent with the tuning methodology itself, not any single signal: every commit in
this repo's history (see `next-agent-action-plan.md`, `peer-review-followup-20260405-late.md`)
validated changes by re-measuring the same 2yr/60d window the change was designed against, then
accepting anything with a positive delta on that window. That process will always show
improvement in-sample; it has no mechanism to detect when a rule (like the Thursday gate) is
fitting regime-specific noise rather than structure. The April session's own comment on the
Monday-gate revert — *"pure in-sample data snooping (rule found AND validated on same 5yr
window). Awaiting 2015-2020 OOS confirmation"* — applies equally to most of the gates and
prunes that were kept, not just the one that got reverted.

## Gate Audit — Isolating Each Abstain Gate on the 10y Window

Added `--disable-gate {gap_down,gap_up,group_agreement,thursday,strong_bear}` to
`run_ablation.py` (repeatable flag; no behavior change when omitted) specifically to make this
kind of audit reproducible. Ran the 10y window with each gate individually disabled, and with
all five disabled:

| Config | 10y Accuracy | n |
|--------|-------------|---|
| **Baseline (all 5 gates active)** | **36.6%** | 1392 |
| gap_down disabled | 36.2% | 1441 |
| gap_up disabled | 37.1% | 1429 |
| group_agreement disabled | 36.9% | 1427 |
| thursday disabled | 37.0% | 1468 |
| strong_bear disabled | 36.6% | 1570 |
| **All 5 disabled** | **37.1%** | 1942 |

**Conclusion: the abstain-gate layer is not the problem.** Every gate, individually or
together, moves the 10y number by at most +0.5pp. Stripping out the entire gate layer (nearly
40% more raw directional calls let through) still lands at 37.1% — barely different from the
36.6% baseline. Combined with the signal-ablation table above (every individual signal within
±0.6pp), this rules out both "one bad gate" and "one bad signal" as the explanation. The
problem is the base 23-signal group-voting function and its ≥55/≤44 bull/bear thresholds
themselves, not any single addition on top of it. This also means the last four months of
gate-adding work (gap-down abstain, gap-up abstain, group-agreement filter, Thursday abstain,
strong-bear abstain — each accepted based on a positive delta on the 2yr/60d window) delivered
real in-sample improvement and approximately zero decade-scale improvement. That is the
expected signature of tuning against a single reused window rather than genuine generalizable
signal.

## Proposed Revised Tuning Methodology

The common thread: every accepted change in this repo's history was validated by re-measuring
the same window it was designed against, then kept if that window's number went up. This
guarantees in-sample improvement and provides no mechanism to detect regime-specific fitting.
Proposed replacement process:

### 1. Three-way time split, not one reused window

- **Tune window** (current 2yr/60d): fast iteration, where new signal/gate ideas are
  first explored. Keep using it for that — it's fine for *generating* candidates.
- **Held-out confirmation window**: a fixed, non-overlapping historical block never used to
  generate ideas — e.g. 2017-2023 (pre-dates the current tuning regime entirely). A candidate
  change is only kept if it is neutral-to-positive on *both* the tune window *and* this block.
  If it helps tune but hurts held-out, that's the signature this session found for the
  Thursday gate and, more broadly, for the whole gate layer — reject or mark explicitly
  regime-conditional with a stated mechanism, not just a measured delta.
- **Lockbox window** (the 10y/full-history OOS check enabled by this session's bug fix):
  re-run periodically (e.g. monthly) purely to monitor for decay. Its number must never be
  used to pick among candidate changes — that would just make it a second tune window. Its
  job is early warning, like the 62.3%→47.3% six-week decay this session surfaced.

### 2. Change the acceptance rule

Current rule (implicit throughout the git history): "accuracy went up on the window I'm
measuring → keep." Proposed rule: "accuracy neutral-or-better on tune window AND
neutral-or-better on held-out window → keep. Otherwise reject, regardless of tune-window
delta." This directly targets the exact failure mode identified in this review.

### 3. Track the gate metrics themselves as a monitored series

Nothing caught the 62.3%→47.3% six-week decay until this review happened to re-run the same
check. Proposed: append each `backtest_export.py --days 60` and `run_ablation.py --period 2y`
result to a durable time-series file (parallel to `Codex/shadow-ledger.csv`, but for the
aggregate metric rather than per-session calls) on a fixed cadence, so decay is visible as a
trend line rather than discovered by accident months later.

### 4. Re-baseline what "canonical" means

`next-agent-action-plan.md` states "60d gate is canonical" and "60d gate > 2yr ablation delta
when they conflict." That rule was reasonable when the two windows mostly agreed. Given the
current model fails the 60d gate today (48.57% vs 52%) and the 2yr window has fallen from
62.3% to 47.3%, propose amending this to: "60d gate is canonical for regime-timing decisions
(e.g. which abstain gate applies right now); the held-out and lockbox windows are canonical
for keep/reject decisions on any permanent signal or gate change."

**Not yet implemented**: this section is a proposal for review, not a committed process. No
signals, gates, or thresholds in `app.py`'s live scoring were changed in this session — only
`run_ablation.py` (bug fix + `--disable-gate` audit tooling).

## Recommendations (not yet actioned — awaiting direction)

1. **Adopt the 10-year walk-forward as a mandatory gate** for any future signal/gate change,
   alongside (not instead of) the existing 60d/2yr checks. The bug fix above makes this
   feasible now (`--period 10y` is clean; avoid `--period max` until pre-1990 VIX-gap handling
   is addressed, or accept `10y`/`15y` as the practical ceiling).
2. **Re-audit each existing abstain gate against the 10y window in isolation** (gap-down,
   gap-up, group-agreement, strong-bear, Thursday) — the Thursday gate result above shows this
   process surfaces real, actionable findings quickly.
3. **Treat the current live model as failing its own bar.** The 60d gate is `ok: false` today.
   Under this project's own stated rules ("60d gate is canonical"), that alone would normally
   block further additions until it passes again — worth deciding whether to disclose this in
   the app's trust surface rather than only in this note.
4. **Reconsider the tuning methodology**, not just individual signals: hold out a block of
   history untouched by any pruning decision (e.g. pre-2024) and require changes to improve
   both the tuning window and the held-out block before being kept.

## Files Referenced

- `scripts/run_ablation.py` (bug fix applied, uncommitted)
- `/tmp/ablation-10y-fixed.md` (full 10y report, not checked in — regenerate with
  `python3 scripts/run_ablation.py --period 10y --out Codex/validation-artifacts/oos-10y-20260723.md`
  if this should become a durable artifact)
- `/tmp/bt60.json` (current 60d backtest_export output, not checked in)
