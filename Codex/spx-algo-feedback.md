# SPX Algo Feedback

Updated: 2026-03-29 (morning session finalized)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Session Commits (effbdaf → 812aeaf)

| Commit | Change |
|--------|--------|
| b855d97 | Adaptive ATR in day backtest; UW_TOKEN → st.secrets; regime blend |
| 351bf08 | VIX 3d signals; Gap/ATR Normal; weekly SSR accuracy table |
| 08a2d01 | 52w range signals; Above BB Mid; regime-aware reversion dampener |
| 1059838 | ORB width guard; VIX 1d Down; Above Prior Day High; _orb_range_atr |
| 8845faa | Above Pivot; Above 5d High signals |
| c509585 | Sector Breadth ≥ 85% (3rd tier) |
| b888859 | ORB distance momentum boost in projections |
| 6323c1c | ORB range width and Dist/ATR in UI cards |
| c6e53f4 | Overnight ES range position signal + Why This Bias display |
| c24a2b9 | Fix VIX 3d Spike direction bug → VIX No Spike (inverted) |
| beca53e | Group score breakdown bar chart in Signal Breakdown expander |
| 51c1b91 | Codex feedback file updated |
| 0dda8b8 | Fix backtest: use 5m Open for day_open; adaptive chop threshold |
| b181c3f | Tighten research scope labels (items 1-3 resolved) |
| 4457b1b | Fix `_orb_range_atr` / `_orb_distance_atr` NameError by hoisting before Key Levels card |
| 812aeaf | Guard against zero-ATR flat projections when daily data download fails |

---

## Re-review Response (2026-03-29, re: Codex 02:40 CT items)

### Item 1: Day backtest session open — FIXED (0dda8b8)
`load_backtest_data()` now stores `day_open_series` = first 5m bar's Open.
`run_backtest_for_day()` uses `day_open_series[target_date]` for `day_open` and gap math.
First Close used as fallback only if Open data not available.

### Item 2: Adaptive chop threshold — FIXED (0dda8b8)
`_chop_thresh` now uses `p["slot_atr"]` (adaptive per-slot ATR from `_bt_atr_profile`)
instead of flat `slot_atr = bt_atr / 6.5`. Early-session slots are judged correctly.

### Item 3: Research scope labels — FIXED (b181c3f)
Both the 2-yr window validation and 10-day backtest captions now explicitly list:
"Not validated: ORB width/distance, news sentiment, intraday RSI, PCR, macro, A/D, overnight range.
Live projections layer all of these on top."

### Item 4: Same-session signals in prior-eve backtest score — ACKNOWLEDGED, not fixed

Context:
- `Gap/ATR Normal`, `Above Prior Day High`, `Above Pivot`, `Above 5d High` are computed
  using `close.iloc[-1]` (yesterday) relative to prior data when called in backtest context.
- This is technically consistent — they compute yesterday's context, not today's.
- The naming "today's gap" in comments is misleading but the math is correct for backtest context.

Decision: not fixing now. Proper fix requires passing an explicit `session_gap` param to
`compute_ssr()` (similar to `as_of_dt`) and changing Gap/ATR Normal to use it.
This is a medium-priority refactor — the current behavior is defensible but confusing.

Action taken: added note to the day backtest caption: "SSR scored as of prior evening..."
The group score breakdown also makes it visible which signals are live-only.

---

## Open Items

**Low priority:**
1. `_slot_atr` in live accuracy section is flat (`levels["atr"] / 6.5`) — used for chop
   classification threshold only, not projection math. Acceptable as-is.
2. `Gap/ATR Normal` in prior-eve SSR uses prior-day gap. Requires `session_gap` param refactor.
3. ORB width/distance not available in 2-yr hourly backtest (requires intraday ORB reconstruction).

## Final Publish Status

- Feedback note is current through commit `812aeaf`.
- Repo `main` is clean and synced with `origin/main`.
- Current handoff location remains `Codex/spx-algo-feedback.md`.

---

## Signal Inventory (as of b181c3f)

```
Trend (4):      Above 20/50/200 SMA, 20 SMA > 50 SMA
Momentum (5):   Higher Close 1d/5d, RSI Above 50, MACD Bullish, RSI Strong Trend
Volatility (6): VIX Below 20/15, VIX Falling, ATR Contracting, VIX 3d Relief, VIX 1d Down
Breadth (5):    Volume Above Avg, Sector Breadth ≥50/70/85%, A/D Line Positive
Extremes (2):   Stoch Bullish, RSI Trend Zone
Options (2):    Put/Call Fear Premium, Put/Call Fear Abating
Macro (2):      Yield Curve Positive, Credit Spread Calm
Context (4):    Gap/ATR Normal, VIX No Spike, Above Overnight Midpoint, Overnight Upper Third
Position (6):   52w Range Upper Half/Top 20%, Above BB Mid, Above Prior Day High, Above Pivot, Above 5d High
```

Live-only: A/D, Yield Curve, Credit Spread, Above Overnight Midpoint, Overnight Upper Third
RTH-override: RSI Above 50, RSI Trend Zone (replaced by 5m intraday RSI)
Convention: 1=bullish, 0=bearish (all signals verified correct)
