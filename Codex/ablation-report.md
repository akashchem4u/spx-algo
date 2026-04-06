# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T03:10:40Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `279`  
**Baseline accuracy: 44.4%  (124/279)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 43.7% (69/158) | 158 |
| VIX:mid | 43.8% (39/89) | 89 |
| VIX:high | 50.0% (16/32) | 32 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 50.0% (26/52) | 52 |
| gap:flat | 45.5% (85/187) | 187 |
| gap:down | 32.5% (13/40) | 40 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 49.1% (27/55) | 55 |
| Tue | 34.5% (20/58) | 58 |
| Wed | 50.0% (28/56) | 56 |
| Thu | 37.5% (21/56) | 56 |
| Fri | 51.9% (28/54) | 54 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 57.1% (4/7) | 7 |
| normal | 44.1% (120/272) | 272 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 48.3% (29/60) | 60 |
| normal | 43.4% (95/219) | 219 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| RSI Trend Zone | Extremes | 44.4% | 42.9% (108/252) | -1.6% | 90% |
| Sector Breadth ≥ 50% | Breadth | 44.4% | 43.4% (118/272) | -1.1% | 97% |
| VIX No Spike | Context | 44.4% | 43.7% (104/238) | -0.7% | 85% |
| Sector Breadth ≥ 70% | Breadth | 44.4% | 43.8% (120/274) | -0.6% | 98% |
| RSI Above 50 | Momentum | 44.4% | 43.8% (121/276) | -0.6% | 99% |
| 52w Range Upper Half | Position | 44.4% | 43.8% (121/276) | -0.6% | 99% |
| Above BB Mid | Position | 44.4% | 43.8% (121/276) | -0.6% | 99% |
| ATR Contracting | Volatility | 44.4% | 44.0% (121/275) | -0.4% | 99% |
| Above 20 SMA | Trend | 44.4% | 44.0% (122/277) | -0.4% | 99% |
| VIX Below 20 | Volatility | 44.4% | 44.2% (121/274) | -0.3% | 98% |
| MACD Bullish | Momentum | 44.4% | 44.4% (123/277) | -0.0% | 99% |
| RSI Strong Trend | Momentum | 44.4% | 44.4% (123/277) | -0.0% | 99% |
| 52w Range Top 20% | Position | 44.4% | 44.4% (123/277) | -0.0% | 99% |
| Above 50 SMA | Trend | 44.4% | 44.4% (124/279) | +0.0% | 100% |
| Above 200 SMA | Trend | 44.4% | 44.4% (124/279) | +0.0% | 100% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| VIX Falling | Volatility | 44.4% | 44.6% (124/278) | +0.2% | 100% |
| VIX Below 15 | Volatility | 44.4% | 44.6% (124/278) | +0.2% | 100% |
| VIX 3d Relief | Volatility | 44.4% | 44.6% (124/278) | +0.2% | 100% |
| VIX 1d Down | Volatility | 44.4% | 44.6% (124/278) | +0.2% | 100% |
| Sector Breadth ≥ 85% | Breadth | 44.4% | 44.7% (123/275) | +0.3% | 99% |
| 20 SMA > 50 SMA | Trend | 44.4% | 44.8% (124/277) | +0.3% | 99% |
| Above Prior Day High | Position | 44.4% | 44.8% (124/277) | +0.3% | 99% |
| Above Pivot | Position | 44.4% | 44.8% (124/277) | +0.3% | 99% |
| Above 5d High | Position | 44.4% | 44.8% (124/277) | +0.3% | 99% |
| Stoch Bullish | Extremes | 44.4% | 44.8% (121/270) | +0.4% | 97% |
| Higher Close (1d) | Momentum | 44.4% | 44.9% (124/276) | +0.5% | 99% |
| Higher Close (5d) | Momentum | 44.4% | 44.9% (124/276) | +0.5% | 99% |
| Volume Above Average | Breadth | 44.4% | 44.9% (124/276) | +0.5% | 99% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
