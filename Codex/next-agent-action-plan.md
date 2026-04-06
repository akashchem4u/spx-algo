# Next Agent Action Plan

Updated: 2026-04-06 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- document current model state and completed work
- define next productive areas of exploration
- avoid re-treading ground already exhausted

---

## Current Model State (as of 2026-04-06)

- **23+1opt scoring signals**: 23 core + 1 optional Gap Down Contrarian; 7 display-only
- **60d gate**: 18/33 = 54.55% ✓ (threshold 48%)
- **2yr fixed-window baseline**: ~52.8% (fixed walk-forward 2025-01-21 → 2026-04-01)
- **Shadow ledger**: 60 sessions tracked, 48.0% overall / 51.7% last 30d
- **Calendar coverage**: FOMC, CPI, NFP, PCE (HIGH), PPI, GDP (MED) through end of 2026
- **Signal exploration**: 15+ candidates exhausted in this session — all rejected

All five original priorities from the 2026-03-29 plan are complete:
1. Core SSR vs Live-Adj SSR split — ✓ implemented
2. Walk-forward regime breakdown — ✓ implemented (VIX/gap/DOW/event/OpEx)
3. Signal ablation testing — ✓ implemented (run_ablation.py)
4. Shadow performance ledger — ✓ operational (60 sessions)
5. Low-risk accuracy gaps — ✓ all three items implemented

---

## Completed Work This Session (2026-04-06)

- **PCE-CAL-01**: 18 PCE dates (HIGH) added to app.py, run_ablation.py, backtest_export.py
- **CAL-02**: 12 PPI dates + 8 GDP dates (MED) added to app.py
- **CAL-03**: 2026 H2 PCE (6 dates) + 2026 H2 PPI (6 dates) added — calendar complete through Dec 2026
- **EXP-01**: Event-day regime breakdown added to backtest_export.py output
- **LEDGER-FIX**: Shadow ledger display fixed — "up/down" → "bull/bear" normalization + live_adj fallback
- **Signal Exploration (all rejected)**: ^SKEW, sector breadth variants, HYG/TNX/IRX Macro, VIX term contango, VRP, QQQ>SPY, CMF, Weekly Pivot, pre-event gate, VIX relative signals

---

## Signal Landscape — Currently Saturated

The following signal classes have been systematically tested and rejected (all < 0 ablation delta):

| Class | Result | Reason |
|-------|--------|--------|
| Macro group (HYG, TLT, TNX/IRX) | All rejected | Group activation redistributes weights unfavorably |
| VIX relative (Z-score, 20d avg, 5d avg) | All rejected | Fire 52-76% of bars — no selectivity |
| High fire-rate signals (>80% bars) | All rejected | Near-constant bias, no selectivity |
| ^SKEW | Rejected | Fires 3.2% of bars — insufficient sample |
| Pre-event abstain gate | Rejected | Event days are the model's BEST days (75% accuracy) |
| Monday/Thursday abstain gate | Rejected | Small-sample regime-specific behavior |

---

## Open Areas Worth Exploring Next

### 1. ORB Historical Reconstruction (Priority 5 item 3, deferred)

**Problem**: ORB width/distance signals are live-only — no historical validation.

**Approach**: Use 5-minute SPX data (if available via yfinance `interval="5m"`) to reconstruct
the 9:30–10:00 open-range high/low for historical dates. Then validate ORB signals against
completed-bar outcomes.

**Risk**: 5m data via yfinance may be limited to ~60d. Would need alternative data source.

**Effort**: Medium. Only attempt if >60d of 5m data is reliably available.

### 2. Score-Band Accuracy Analysis in Shadow Ledger

**Problem**: The shadow ledger shows overall accuracy but not accuracy by score band.

**Approach**: Add a breakdown row showing accuracy within score bands: [45-54 neutral], [55-62 soft bull],
[63-75 moderate bull], [76-100 strong bull], and symmetric for bear. This would reveal whether
the model is more reliable at extreme scores.

**Effort**: Low. Pure display change in shadow ledger section of app.py.

### 3. 2026 H2 Event Calendar Verification

**Status**: PCE H2 2026 dates (Jul-Dec) were added based on BEA's typical schedule.
These are estimated. When BEA confirms actual release dates for H2 2026, verify and correct.

### 4. Probe OpEx Timing Sub-Windows

**Problem**: OpEx week is classified as a single binary flag, but the timing within OpEx week
likely matters (Monday setup vs Friday pin). The ablation shows OpEx weeks at 50.4% (53/105)
which is barely above baseline.

**Approach**: Break down OpEx accuracy by day-of-week within OpEx week. Mon OpEx vs Fri OpEx
may have meaningfully different characteristics.

**Effort**: Low. Add to run_ablation.py's existing OpEx breakdown.

---

## Key Architecture Rules (Do Not Violate)

1. **60d gate is canonical** — 2yr ablation window is fixed (2025-01-21 → 2026-04-01)
2. **Macro group stays dead** — any signal activating Macro group redistributes weights unfavorably
3. **Fire-rate > 80% is disqualifying** — no selectivity, adds constant bias
4. **Ablation delta > 1.0% = prune** — unless mechanistic justification (e.g., VIX Below 15, VVIX Below 100)
5. **60d gate > 2yr ablation delta** when they conflict (regime-specific signals)
6. **Pre-event days are model-strong** — do not abstract or suppress them
7. **Gap-down bear abstain must stay** — bear calls on large gap-downs are wrong 68% of time

---

## What Not To Do

- Do not add signals without ablation validation first
- Do not activate the Macro group (dead by design until a non-HYG/TNX signal is found)
- Do not add a day-of-week gate without 2yr ablation evidence (60d day-of-week is 6-sample noise)
- Do not re-probe signal classes already systematically rejected (see table above)
- Do not optimize UI wording ahead of measurement quality
