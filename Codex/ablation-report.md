# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T05:52:48Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `262`  
**Baseline accuracy: 45.0%  (118/262)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 44.4% (63/142) | 142 |
| VIX:mid | 44.8% (39/87) | 87 |
| VIX:high | 48.5% (16/33) | 33 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 51.0% (26/51) | 51 |
| gap:flat | 46.2% (80/173) | 173 |
| gap:down | 31.6% (12/38) | 38 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 50.0% (26/52) | 52 |
| Tue | 37.3% (19/51) | 51 |
| Wed | 55.6% (30/54) | 54 |
| Thu | 37.0% (20/54) | 54 |
| Fri | 45.1% (23/51) | 51 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 57.1% (4/7) | 7 |
| normal | 44.7% (114/255) | 255 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 46.6% (27/58) | 58 |
| normal | 44.6% (91/204) | 204 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| VIX Falling | Volatility | 45.0% | 44.2% (111/251) | -0.8% | 96% |
| ATR Contracting | Volatility | 45.0% | 44.3% (112/253) | -0.8% | 97% |
| VIX 1d Down | Volatility | 45.0% | 44.4% (114/257) | -0.7% | 98% |
| Above Pivot | Position | 45.0% | 44.4% (115/259) | -0.6% | 99% |
| 52w Range Upper Half | Position | 45.0% | 44.5% (113/254) | -0.5% | 97% |
| Above BB Mid | Position | 45.0% | 44.5% (113/254) | -0.5% | 97% |
| VIX No Spike | Context | 45.0% | 44.5% (101/227) | -0.5% | 87% |
| Above 20 SMA | Trend | 45.0% | 44.6% (115/258) | -0.5% | 98% |
| Higher Close (1d) | Momentum | 45.0% | 44.6% (115/258) | -0.5% | 98% |
| VIX Below 15 | Volatility | 45.0% | 44.6% (116/260) | -0.4% | 99% |
| Above 5d High | Position | 45.0% | 44.6% (116/260) | -0.4% | 99% |
| Higher Close (5d) | Momentum | 45.0% | 44.7% (113/253) | -0.4% | 97% |
| Gap Up Day | Context | 45.0% | 44.7% (113/253) | -0.4% | 97% |
| RSI Above 50 | Momentum | 45.0% | 44.7% (114/255) | -0.3% | 97% |
| 52w Range Top 20% | Position | 45.0% | 44.7% (114/255) | -0.3% | 97% |
| Volume Above Average | Breadth | 45.0% | 44.7% (115/257) | -0.3% | 98% |
| Above 200 SMA | Trend | 45.0% | 44.8% (116/259) | -0.3% | 99% |
| MACD Bullish | Momentum | 45.0% | 44.8% (116/259) | -0.3% | 99% |
| Above 50 SMA | Trend | 45.0% | 44.8% (117/261) | -0.2% | 100% |
| Above Prior Day High | Position | 45.0% | 44.8% (117/261) | -0.2% | 100% |
| VIX Below 20 | Volatility | 45.0% | 44.8% (113/252) | -0.2% | 96% |
| RSI Strong Trend | Momentum | 45.0% | 45.0% (116/258) | -0.1% | 98% |
| Sector Breadth ≥ 50% | Breadth | 45.0% | 45.0% (108/240) | -0.0% | 92% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| Sector Breadth ≥ 85% | Breadth | 45.0% | 45.1% (115/255) | +0.1% | 97% |
| Stoch Bullish | Extremes | 45.0% | 45.3% (115/254) | +0.2% | 97% |
| RSI Trend Zone | Extremes | 45.0% | 45.4% (109/240) | +0.4% | 92% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
