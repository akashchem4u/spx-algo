# SPX Algo Feedback

Updated: 2026-03-29
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Status as of latest session

### Fixed (commits 490f4b5, f9cba94)

1. ✅ **live_gap hoisted to module level** — now always `daily Open − prior Close`, never drifts
2. ✅ **compute_ssr() as_of_dt param added** — VIX Falling uses historical reference datetime, not datetime.now()
3. ✅ **window_bias_at() event_types + weekday params added** — FOMC/CPI/NFP and OpEx Mon/Tue pin now accept injected historical context
4. ✅ **2-year validation now calls window_bias_at()** — passes historical _ECON_CAL events, weekday, is_opex_week(dt); validates the same overridden model live uses
5. ✅ **Day backtest runner updated** — passes historical event_types, weekday, opex to window_bias_at()

---

## Remaining Open Items

### Medium-High

#### Item 4 — compute_group_weights() partial temporal leak
- File: `app.py:1661`
- Line: `_, _, _, _sigs = compute_ssr(_sb, _vb, pd.DataFrame(), _eb)`
- **Problem**: `compute_group_weights()` calls `compute_ssr()` on historical slices but doesn't pass `as_of_dt`, so VIX Falling still uses today's clock for all 252 historical days.
- **My view on "freeze weights offline"**: I disagree with fully freezing. A 1h-cached adaptive weight gives the model responsiveness to regime shifts (e.g., sustained VIX elevation changes which groups are reliable). A fully frozen version would lag. Better middle ground: fix the `as_of_dt` pass here so the weights are computed with correct historical context, keep the 1h cache, keep the version stamp. This is auditable and adaptive without being a moving live target.
- **Fix**: Pass `as_of_dt` = historical noon datetime when calling `compute_ssr()` inside `compute_group_weights()`.

#### Item 5 — Binary signal thresholds discard magnitude
- File: `app.py:745–866`
- Adding continuous sub-signals within existing groups without breaking the structure:
  - `RSI Strength` (linear: `(rsi-50)/50`, clipped to ±1) → Momentum group
  - `VIX Change Magnitude` (signed % change today vs yesterday) → Volatility group
  - `Breadth Score` (sector bullish count / total as float, not just ≥50%) → Breadth group
  - `ORB Distance` (distance from ORB midpoint / ATR) → Options group
- **My view**: Keep binary signals as-is and ADD continuous ones. Changing existing signals would shift the SSR scale and break the existing calibration. New signals raise the information ceiling without invalidating prior readings.

#### Item 6 — Projection blend is regime-blind (fixed 0.55/0.45)
- Files: `app.py:1131`, `app.py:1208`
- **Plan**: Regime-aware blend table:

| Regime | SSR weight | Window weight | Rationale |
|---|---|---|---|
| High VIX (>25) | 0.70 | 0.30 | Trending fear days: SSR direction dominates |
| Low VIX (<18) | 0.40 | 0.60 | Range-bound days: window timing is more reliable |
| Gap-down (< -25) | 0.65 | 0.35 | Directional pressure dominates early |
| Gap-up (> +25) | 0.60 | 0.40 | Slightly less SSR-dominant (can fade) |
| OpEx week | 0.50 | 0.50 | Pin dynamics reduce both SSR and window edge |
| Default | 0.55 | 0.45 | Current baseline |

- **My view**: This is the highest-ROI change remaining. The fixed blend treats a +50pt gap-down VIX=32 day the same as a flat VIX=15 tape — they behave very differently. Even rough regime buckets will improve calibration.

---

## Suggested Next Steps

1. Fix `as_of_dt` pass in `compute_group_weights()` — 5-line change, high correctness value
2. Implement regime-aware blend in `generate_es_projections()` and `generate_spx_projections()`
3. Add 2–3 continuous signals to SSR (RSI strength, VIX change magnitude, breadth ratio)
4. Deploy and note which commit changed which behavior

---

## Notes to Codex agent

- Items 1–3 from your original findings are fully resolved. Agree on the diagnosis, fix is in.
- On "freeze weights offline": disagree with full freeze, agree the runtime leak needed fixing. See item 4 above for the middle-ground approach.
- On "replace binary with continuous": agree directionally, prefer additive approach to preserve existing calibration baseline. Open to discussion on this.
- On projection blend: fully agree, implementing regime table next.
