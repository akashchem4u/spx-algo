# SPX Algo Feedback

Updated: 2026-03-29 (Claude agent session, morning finalization)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Session Summary (commits effbdaf → beca53e)

All original reviewer high and medium items (A–F) have been fixed.
Major additions this session:

| Commit | Change |
|--------|--------|
| b855d97 | Adaptive ATR in day backtest; UW_TOKEN → st.secrets; regime blend |
| 351bf08 | VIX 3d signals; Gap/ATR Normal; weekly SSR accuracy table |
| 08a2d01 | 52w range signals; Above BB Mid; regime-aware reversion dampener |
| 1059838 | ORB width guard (narrow ORB suppressed); VIX 1d Down; Above Prior Day High; _orb_range_atr |
| 8845faa | Above Pivot; Above 5d High signals |
| c509585 | Sector Breadth ≥ 85% (3rd tier); Codex feedback rewritten |
| b888859 | ORB distance momentum boost in projections (continuous ORB size signal) |
| 6323c1c | ORB range width and Dist/ATR displayed in UI cards |
| c6e53f4 | Overnight ES range position signal + Why This Bias display |
| c24a2b9 | Fix VIX 3d Spike direction bug → renamed VIX No Spike (inverted) |
| beca53e | Group score breakdown bar chart in Signal Breakdown expander |

---

## Original Items — All Resolved

**A. Projection math in day backtest** — FIXED (b855d97): adaptive ATR profile used in both live and backtest.

**B. 5m RSI override label** — IMPLEMENTED: signal breakdown shows `(5m)` badge; live SSR card shows "📡 Intraday RSI (5m): X — live signal active".

**C. Mean-reversion dampener** — FIXED (08a2d01): regime-aware `_rev_rate` = 0.008 (hi-VIX), 0.020 (lo-VIX), 0.015 (default).

**D. A/D ratio live-only label** — IMPLEMENTED: `(live-only)` badge shown in signal breakdown for A/D Line Positive, Yield Curve, Credit Spread Calm, and now also for overnight signals.

**E. Weekly projection backtest** — FIXED (351bf08): 20-week SSR accuracy table added in Research tab.

**F. UW_TOKEN hardcoded** — FIXED (b855d97): now `st.secrets.get("UW_TOKEN", "")`.

---

## Signal Inventory (current state)

```
Trend (4):     Above 20/50/200 SMA, 20 SMA > 50 SMA
Momentum (5):  Higher Close 1d/5d, RSI Above 50, MACD Bullish, RSI Strong Trend
Volatility (6): VIX Below 20/15, VIX Falling, ATR Contracting, VIX 3d Relief, VIX 1d Down
Breadth (5):   Volume Above Avg, Sector Breadth ≥50/70/85%, A/D Line Positive
Extremes (2):  Stoch Bullish, RSI Trend Zone
Options (2):   Put/Call Fear Premium, Put/Call Fear Abating
Macro (2):     Yield Curve Positive, Credit Spread Calm
Context (4):   Gap/ATR Normal, VIX No Spike, Above Overnight Midpoint, Overnight Upper Third
Position (6):  52w Range Upper Half/Top 20%, Above BB Mid, Above Prior Day High, Above Pivot, Above 5d High
```

Live-only signals: A/D Line Positive, Yield Curve Positive, Credit Spread Calm,
                   Above Overnight Midpoint, Overnight Upper Third
RTH-override: RSI Above 50, RSI Trend Zone (replaced by intraday 5m RSI during market hours)

---

## Remaining Items

### Medium

**1. ORB width signal in backtest window_bias_at calls**
- Backtest day view calls `window_bias_at()` without `orb_range_atr` — the narrow ORB guard doesn't apply
- This is acceptable: backtest doesn't have intraday ORB data anyway; the guard is live-only
- Status: no action needed; label it "live ORB guard" if needed

**2. VIX No Spike default in historical backtest**
- `sigs["VIX No Spike"] = 1` when `len(vix_c) < 4` (insufficient history)
- In backtest paths, this could default to 1 even when historical VIX data shows a spike
- Low impact: the backtests use at most 30-bar VIX data, so this is very rarely hit
- Status: acceptable default for now

**3. _slot_atr in live accuracy section is flat (levels["atr"] / 6.5)**
- Line ~3139 in live accuracy section uses flat ATR per slot as chop threshold
- This is for categorical classification (chop/bull/bear), not projection math
- Adaptive threshold would slightly improve chop detection in morning slots
- Low priority

### Notes

- VIX 3d Spike was a direction bug (1=bullish in scoring but spike=bearish in reality).
  Fixed by inverting: VIX No Spike = 1 when no spike (calm = bull).
- All signals now correctly follow convention: 1=bullish, 0=bearish.
- Group score breakdown bar chart added to Signal Breakdown expander.
- Overnight ES range position is live-only; fully integrated into SSR Context group.
