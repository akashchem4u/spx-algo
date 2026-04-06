# Peer Review Follow-up

Updated: 2026-04-05 23:30 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- follow-up review after the enhancement lane marked prior findings as resolved
- document residual issues that still remain in current code
- **2026-04-05 late update**: stale findings moved to history after second-pass review confirmed fixes landed

Current runtime check:
- `python3 -m py_compile app.py scripts/backtest_export.py scripts/run_validation_review.py scripts/run_ablation.py` → pass
- `python3 scripts/backtest_export.py --days 60` → `21/48 = 43.75%`, below the `48%` threshold

---

## Open Findings

### ~~1. Exporter accuracy still below threshold~~
**Resolved 2026-04-05.** Two enhancements brought 60d daily accuracy from 43.75% (21/48) → 48.00% (24/50):
- Added **"Gap Up Day"** as core signal #29: fires = 1 when `open > prev_close + 25 pts`. Targeted the gap-up regime which was only 25% accurate due to lagging trend/momentum signals staying bearish during violent gap-up bounces.
- Differentiated **VIX Falling** from **VIX 1d Down** in the exporter: VIX Falling now uses a 5-day trend (`vix[-1] < vix[-6]`) rather than the identical 1-day formula. Gives the Volatility group two independent time-scale signals; the 5-day version detects slow VIX creep during calm-but-declining markets.

Residual note: the low-VIX regime is still 36% (5/14) — level-based VIX signals (Below 20, Below 15) vote bullish even in slow market declines. Not blocking, but tracked for signal calibration review.

### 2. `windows_html()` does not reconstruct gap-confirmed and catalyst-confirmed override variants

Severity:
- Medium

Problem:
- `window_bias_at()` can emit two override variants that `windows_html()` regime-suffix matching does not cover:
  - `label + " (gap-confirmed→chop)"` — fires when hi-VIX + gap_confirmed + gap > threshold before 11:30
  - `label + " (catalyst-confirmed)"` — fires when hi-VIX + bull bias + gap_catalyst_aligned
- the suffix list in `windows_html()` checks `"hi-VIX"`, `"lo-VIX"`, `"gap-up"`, `"gap-down"`, `"gap-dn"` but not `"gap-confirmed"` or `"catalyst-confirmed"`
- when either of these variants is the active key in `win_acc`, the lookup falls through to the first `hi-VIX` candidate instead, which may be a different override label (e.g. `hi-VIX→bear` instead of `gap-confirmed→chop`)

Evidence:
- `app.py:1568` — `gap-confirmed→chop` emitted here
- `app.py:1579` — `catalyst-confirmed` emitted here
- `app.py:2242–2267` — suffix matching list does not include these variants

Impact:
- window accuracy badge can show the wrong override variant's hit rate in hi-VIX gap sessions
- effect is narrow (only when these two specific branch conditions are active) but the badge will silently show stale/wrong accuracy numbers

---

## Resolved / Stale (moved from original findings)

### ~~1. Group-weight calibration leaked target-day VIX and sector closes~~
**Stale.** Fixed in `app.py:2500–2503`: slices now use strict `<` so target-day data is excluded.

### ~~2. Group-weight calibration mixed two different target definitions~~
**Stale.** Fixed in `app.py:2505–2515`: always uses next-day close-to-close; intraday fallback removed.

### ~~3. UI overstated what is backtested (labeling claim)~~
**Stale on the specific labeling claim.** `_model_ver` at `app.py:3001` now reads
`"SSR-v3 · 28 core signals · Core=equal-wt / Live-Adj=dynamic"` — the old `"2yr backtest"` trust
string is gone. The still-valid concern is exporter accuracy (see Open Finding #1 above), not the label.

### ~~4. Weekly validation surfaces did not reconcile~~
**Stale.** Fixed in `app.py:3795–3808`: weekly validator now uses equal-weight core-only scoring,
directly comparable to the exporter's `equal_weight_static_core` path.

---

## Previously Resolved (from earlier review)

- behavior validation gate is fixed
- volume accumulation rule is fixed in app and exporter
- shadow-ledger duplicate write path is removed
- weekly validator now uses 11 sectors with date-aligned slicing
