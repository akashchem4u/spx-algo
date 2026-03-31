# SPX-Algo Ablation Report

Generated: 2026-03-31
Model: SSR-v3 · 28 core signals · 9 signal groups
Backtest window: 90 trading days (2025-12-01 → 2026-03-31)
Accuracy (full model): **55.1%** (38/69 directional days, threshold 48%)
Current regime: **high_vix** (VIX 28.1)

---

## Signal Group Contribution (walk-forward, 90d)

Each row shows estimated directional accuracy when that group's signals are
zeroed out (held at 0). Delta = full-model accuracy minus ablated accuracy.
Positive delta = group is additive; negative = group is currently a drag.

| Group | Signals | Est. Ablated Acc | Delta | Status |
|-------|---------|-----------------|-------|--------|
| Volatility | VIX Below 20, VIX Falling, ATR Contracting, VIX Below 15, VIX 3d Relief, VIX 1d Down | 47% | **+8%** | ✅ High lift |
| Momentum | Higher Close (1d/5d), RSI Above 50, MACD Bullish, RSI Strong Trend | 50% | +5% | ✅ Positive |
| Trend | Above 20/50/200 SMA, 20 SMA > 50 SMA | 52% | +3% | ✅ Positive |
| Position | 52w Range Upper/Top 20%, Above BB Mid, Above Prior Day High, Above Pivot, Above 5d High | 53% | +2% | ✅ Marginal positive |
| Context | Gap/ATR Normal, VIX No Spike | 54% | +1% | ➡ Neutral/slight lift |
| Extremes | Stoch Bullish, RSI Trend Zone | 55% | 0% | ➡ Neutral |
| Breadth | Volume Above Average, Sector Breadth ≥50/70/85% | 56% | −1% | ⚠ Slight drag in high-VIX |
| Options | Put/Call Fear Premium, Put/Call Fear Abating | 55% | 0% | ➡ Neutral |
| Macro | Yield Curve Positive, Credit Spread Calm | 55% | 0% | ➡ Neutral |

> Note: Ablation estimates are derived from the walk-forward group-score
> perturbation. Full per-signal SHAP decomposition pending shadow-ledger
> accumulation (target: 200 resolved rows).

---

## Regime-Stratified Accuracy

| Regime | VIX Range | 90d Acc | N |
|--------|-----------|---------|---|
| low_vix | < 18 | ~62% | ~12 |
| mid_vix | 18–25 | ~57% | ~28 |
| high_vix | > 25 | ~48% | ~29 |

High-VIX accuracy is borderline — the Volatility group signals (`VIX Below 20`,
`VIX Below 15`) systematically fire 0 in this regime, reducing the composite
score and pushing more bars into the bear-call bucket. Bear calls have been
correct in the recent trend (2026-03-25, 26, 27) but reversed on 2026-03-20
and 2026-03-24 (bear trapped by relief rallies).

---

## Signal Drift Flags (last 10 evaluated days)

Signals are flagged when they fired the wrong direction ≥70% of evaluable days.
As of 2026-03-31 the drift monitor checks for this at app load (cached 1h).

| Signal | Drift? | Notes |
|--------|--------|-------|
| VIX Below 20 | ⚠ Likely | VIX consistently above 25 — fires 0 always |
| VIX Below 15 | ⚠ Likely | Same — structural zero in high-VIX |
| RSI Strong Trend | Monitor | Trend zone (60–75) rarely hit in current chop |
| All others | OK | Within normal range |

---

## Open Items

- [ ] Accumulate 200+ shadow-ledger rows for per-signal SHAP decomposition
- [ ] Add regime-conditional signal weight scaling (high-VIX dampens absolute-level VIX signals)
- [ ] Re-run ablation after the current high-VIX episode resolves to compare mid-VIX lift
- [ ] Consider promoting Put/Call signals if options data feed stabilises
