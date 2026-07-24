# Out-of-Sample Validation Review

Updated: 2026-07-24 CT (rev 2 — corrects rev 1, which was pushed to `origin/main` with a
significant bug in the analysis tooling that overstated the model's OOS failure)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- execute the OOS validation the 2026-06-14 ALGO-IMPROVE-01 session flagged as the required
  next step but never ran ("Critic verdict: model at noise floor for in-sample tuning; next
  step is 2015-2020 OOS validation")
- report findings only; no scoring-logic changes made in this session pending direction

## Correction Notice (read this first)

Rev 1 of this document reported a 10-year OOS accuracy of **36.6%** and a held-out
(2017-2023) accuracy of **35.8%**, and characterized the model as "13.4pp below random" and
"actively anti-correlated." **Those figures were wrong, due to a second bug in
`scripts/run_ablation.py` found after rev 1 was already committed and pushed.** The bug: the
loop was missing the flat-price-move exclusion that `backtest_export.py` already had (`if not
up and not dn: continue`). Any day the model fired a bull/bear call but the actual price move
never cleared the ATR-relative threshold in either direction was being counted as an automatic
**miss** instead of being excluded as an untestable "no read." This is not a new bug introduced
this session — it predates this review entirely (present even in the file's original
fixed-5pt-threshold version) — but it went undetected until a genuine multi-year OOS run was
attempted, because on the recent, higher-priced, higher-ATR 2025-2026 window flat days are rare
enough that the miscount barely mattered. On lower-priced 2017-2023 SPX history, a much larger
share of days fall inside the ATR-relative flat band, so the miscount became large enough to
dominate the result.

**Corrected figures (fix applied, same hit counts, corrected denominators):**

| Check | Rev 1 (buggy) | Rev 2 (corrected) |
|-------|---------------|---------------------|
| 10-year lockbox (2017-2026) | 36.6% (510/1392) | **52.3% (510/975)** |
| Held-out 2017-2023 | 35.8% (330/921) | **51.3% (330/643)** |
| Current 2yr tune window | 47.3% (86/182) | **57.7% (86/149)** |
| 60d canonical gate (`backtest_export.py`, unaffected — already had the correct exclusion) | 48.57% (17/35) | 50.0% (17/34), effectively unchanged |

The correct conclusion is materially different from rev 1: **the model is not "worse than
random" or "actively anti-correlated."** True multi-year OOS accuracy sits close to the
coin-flip line (51-52%), a few points below the current in-sample window (57.7%) and close to
where the 60d gate sits today (50.0%). That gap between in-sample and OOS is a normal,
expected sign of some degree of overfitting from iterative single-window tuning — not evidence
of a broken or harmful model. The rest of this document is rewritten to reflect the corrected
numbers. The methodology critique from rev 1 (single-window tuning risk, no held-out
confirmation) still holds, just at a much less alarming magnitude than originally reported.

## Bug #1 Found and Fixed: VIX Slice Misalignment

`scripts/run_ablation.py` (pre-fix): `vix_sl = vix.iloc[:i + 1]` sliced VIX by **integer
position**, not by date, while every other series (`sec_sl`, `vvix_sl`, `vix9d_sl`) was
correctly sliced by `v.index <= cutoff`. `backtest_export.py` already does this correctly.

- **Impact at `--period 2y/5y`**: negligible in almost all cases, since SPX and VIX bar counts
  normally match 1:1 over short/medium windows. Confirmed via a live check: a 10y pull had VIX
  with **one extra bar** (2026-05-25 — Memorial Day; NYSE closed, yfinance still returned a VIX
  row), which would have silently shifted every VIX-derived signal by one trading day for the
  tail ~40 bars of any positional-slice run touching that period.
- **Impact at `--period max`**: catastrophic. SPX history (back to 1928) vastly exceeds VIX
  history (back to 1990); positional slicing fed VIX values from a totally unrelated era into
  signal computation for the entire pre-1990 span, producing a mechanically-impossible 8.7%
  baseline that confirmed the bug rather than a real result.
- **Fixed**: `vix_sl` now slices by `cutoff` date. Committed in `ffa2f0a`.

## Bug #2 Found and Fixed: Flat-Move Days Counted as Automatic Misses

`scripts/run_ablation.py`'s main loop computed `up`/`dn` (whether the next bar's close cleared
the ATR-relative directional threshold) and went straight to `correct = (bull_c and up) or
(bear_c and dn)` with no exclusion step for the case where neither `up` nor `dn` is true (price
stayed within the flat band). `scripts/backtest_export.py` already has this exact exclusion
(`if not up and not dn: continue`) — only `run_ablation.py` was missing it. Confirmed via git
history that this predates the current session and even the ATR-relative labeling change
(`ALGO-IMPROVE-01`, 2026-06-14) — it was present since the file's original fixed-5pt-threshold
version, just far less consequential then, since a 5pt move is common enough on SPX that "flat"
days were rare regardless of era.

**How it was found**: an independent verification script (`scripts/analyze_score_decile.py`,
built to check whether the raw score has any relationship to outcome at all, bypassing
`run_ablation.py`'s loop entirely) computed 50.5% raw-threshold accuracy on 2017-2023 with
identical hit count (488) to a `run_ablation.py` all-gates-disabled run on the same window, but
with a smaller denominator (966 vs 1307). The gap — 341 calls, zero of them hits — was
mechanically explained by the missing flat-day exclusion: every one of the 341 extra "calls"
`run_ablation.py` counted was a day the model fired bull/bear but price never cleared the
directional threshold, so `correct` was structurally `False` for all of them regardless of
whether the model's directional lean was actually right.

**Fixed**: added the same `if not up and not dn: continue` check to `run_ablation.py`, in the
same position as `backtest_export.py` (after all abstain gates, before computing `correct`).

## OOS Test: 10-Year Walk-Forward (corrected)

Run: `python3 scripts/run_ablation.py --period 10y` (post both fixes)

**Baseline accuracy: 52.3% (510/975)** — current live signal set (23+1opt core signals, all 5
abstain gates active), evaluated against 2017-2026 history that was never part of any tuning
decision.

This is a modest ~5pp gap versus the current 2yr tune window (57.7%), and close to where the
60d gate sits today (50.0%). That is a normal degree of in-sample/OOS divergence for a model
tuned iteratively against a single recent window — not evidence of harmful anti-correlation.

### Signal-level ablation on the 10y window (corrected)

Every individual signal's removal delta remains small (sub-1% either way; see
`/tmp/ablation-10y-FIXED.md`), consistent with the rev-1 finding that no single signal is
responsible for the gap between tune and OOS performance — it's spread thinly across the whole
signal set rather than concentrated in one prunable component.

### Day-of-week — corrected

The rev-1 finding that Thursday ranks best and Monday worst in the 10y data (versus Thursday
being flagged weakest in the tuning window, which motivated the Thursday bear-abstain gate)
holds directionally even after the fix, though the specific accuracy figures shift with the
corrected denominators. This divergence pattern is still worth the same caution rev 1 gave it:
a gate justified by a small-sample in-sample pattern that doesn't clearly replicate OOS.

## Gate Audit — Corrected

Same methodology as rev 1 (`--disable-gate` on the 10y window), rerun with both bugs fixed:

| Config | 10y Accuracy | n |
|--------|-------------|---|
| **Baseline (all 5 gates active)** | **52.3%** | 975 |
| gap_down disabled | 51.3% | 1017 |
| gap_up disabled | 52.5% | 1009 |
| group_agreement disabled | 52.7% | 998 |
| thursday disabled | 52.6% | 1032 |
| strong_bear disabled | 51.1% | 1125 |
| **All 5 disabled** | **50.3%** | 1432 |

**Revised conclusion**: the gate layer contributes a small, consistent positive lift on true
OOS data — full baseline (52.3%) versus all-gates-disabled (50.3%) is a genuine +2.0pp, not the
near-zero effect rev 1 reported under the buggy denominators. Individually, `gap_down` and
`strong_bear` contribute the most (each ~1.0-1.2pp); `group_agreement` and `thursday` are
close to neutral-to-slightly-negative in isolation, consistent with rev 1's caution about the
Thursday gate specifically, though the magnitude is now small rather than a driving factor.
This is a materially more encouraging picture than rev 1: the gates appear to be doing
approximately what they were designed to do, even out of sample, just modestly rather than
dramatically.

## Assessment (revised)

- The model shows a real but modest edge in its current tuning window (57.7%, n=149) and a
  smaller, closer-to-coin-flip edge on genuine multi-year OOS data (51-52%, n~650-975 depending
  on window). This is the ordinary signature of some in-sample fitting, not a broken model.
- The 60-day canonical gate is at 50.0% today (`ok: false` vs the 0.52 threshold) — worth
  tracking, but a small-sample (n=34) reading within a plausible range around the OOS baseline,
  not a crisis.
- The abstain-gate layer (gap-down, gap-up, group-agreement, Thursday, strong-bear) contributes
  a modest, genuine +2pp on true OOS data — it is doing roughly what it was designed to do.
- The methodology critique from rev 1 still stands, just should be read at this corrected,
  calmer magnitude: every accepted change in this repo's history was validated by re-measuring
  the same 2yr/60d window it was designed against. That process can show in-sample improvement
  that doesn't fully transfer OOS, and currently has no mechanism to distinguish durable signal
  from window-specific noise. The proposed three-way split below is still worth adopting, as a
  matter of good practice and early-warning monitoring — not as a response to a crisis, since
  there wasn't one.

## Proposed Revised Tuning Methodology

Unchanged in substance from rev 1, retained here for reference; read against the corrected,
more modest severity above rather than the original overstated framing.

### 1. Three-way time split, not one reused window

- **Tune window** (current 2yr/60d): fast iteration, where new signal/gate ideas are first
  explored. Fine for *generating* candidates.
- **Held-out confirmation window**: a fixed, non-overlapping historical block never used to
  generate ideas — e.g. 2017-2023. A candidate change is only kept if neutral-to-positive on
  *both* the tune window *and* this block.
- **Lockbox window** (10y/full-history OOS, enabled by the bug fixes above): re-run
  periodically (e.g. monthly) purely to monitor for decay, never to pick among candidates.

### 2. Change the acceptance rule

Current implicit rule: "accuracy went up on the window I'm measuring → keep." Proposed:
"neutral-or-better on tune window AND neutral-or-better on held-out window → keep."

### 3. Track the gate metrics themselves as a monitored series

Append each `backtest_export.py --days 60` and `run_ablation.py --period 2y` result to a
durable time-series file on a fixed cadence, so drift is visible as a trend line.

### 4. Re-baseline what "canonical" means

Given the 60d gate sits at exactly the coin-flip line today, propose: "60d gate is canonical
for regime-timing decisions; the held-out and lockbox windows are canonical for keep/reject
decisions on any permanent signal or gate change."

**Not yet implemented**: this section remains a proposal for review. No signals, gates, or
thresholds in `app.py`'s live scoring were changed in this session — only `scripts/run_ablation.py`
(two bug fixes + `--disable-gate` audit tooling) and `scripts/analyze_score_decile.py` (new,
independent verification tool that caught Bug #2).

## Recommendations (not yet actioned — awaiting direction)

1. **Adopt the 10-year walk-forward as a routine check** alongside the existing 60d/2yr checks,
   now that both bugs are fixed and the numbers can be trusted.
2. **Track the 60d/2yr/10y figures over time** in a durable ledger so any future decay (or
   recovery) is visible as a trend rather than discovered by accident.
3. **Adopt the three-way split for future signal/gate changes** — good practice given the
   demonstrated tune/OOS gap, even though the gap is modest rather than alarming.
4. **Re-examine the Thursday gate specifically** at some point — it's the one gate whose
   disable-effect leans slightly positive on true OOS data, though the effect is now small
   enough (this document previously overstated it) that it isn't urgent.

## Files Referenced

- `scripts/run_ablation.py` (both bug fixes + `--disable-gate`, committed)
- `scripts/analyze_score_decile.py` (new, independent score-calibration check, committed)
- `/tmp/ablation-10y-FIXED.md`, `/tmp/ablation-heldout-FIXED.md`, `/tmp/ablation-2y-FIXED.md`
  (corrected reports, not checked in — regenerate as needed)
