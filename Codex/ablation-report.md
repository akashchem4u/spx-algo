# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T06:26:31Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `257`  
**Baseline accuracy: 45.5%  (117/257)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 45.3% (63/139) | 139 |
| VIX:mid | 45.3% (39/86) | 86 |
| VIX:high | 46.9% (15/32) | 32 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 51.0% (26/51) | 51 |
| gap:flat | 46.7% (79/169) | 169 |
| gap:down | 32.4% (12/37) | 37 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 49.0% (25/51) | 51 |
| Tue | 38.0% (19/50) | 50 |
| Wed | 56.6% (30/53) | 53 |
| Thu | 37.0% (20/54) | 54 |
| Fri | 46.9% (23/49) | 49 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 62.5% (5/8) | 8 |
| normal | 45.0% (112/249) | 249 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 48.3% (28/58) | 58 |
| normal | 44.7% (89/199) | 199 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| Gap Down Contrarian | Context | 32.4% | 26.5% (9/34) | -6.0% | 92% |
| Sector Breadth ≥ 50% | Breadth | 45.5% | 44.4% (103/232) | -1.1% | 90% |
| Sector Breadth ≥ 85% | Breadth | 45.5% | 44.5% (110/247) | -1.0% | 96% |
| Volume Above Average | Breadth | 45.5% | 44.6% (111/249) | -0.9% | 97% |
| MACD Bullish | Momentum | 45.5% | 44.7% (113/253) | -0.9% | 98% |
| VIX No Spike | Context | 45.5% | 44.8% (99/221) | -0.7% | 86% |
| Higher Close (5d) | Momentum | 45.5% | 44.8% (112/250) | -0.7% | 97% |
| Higher Close (1d) | Momentum | 45.5% | 45.0% (113/251) | -0.5% | 98% |
| VIX 1d Down | Volatility | 45.5% | 45.1% (114/253) | -0.5% | 98% |
| VIX Falling | Volatility | 45.5% | 45.2% (112/248) | -0.4% | 96% |
| RSI Above 50 | Momentum | 45.5% | 45.2% (113/250) | -0.3% | 97% |
| Above 200 SMA | Trend | 45.5% | 45.3% (116/256) | -0.2% | 100% |
| ATR Contracting | Volatility | 45.5% | 45.3% (112/247) | -0.2% | 96% |
| Stoch Bullish | Extremes | 45.5% | 45.3% (112/247) | -0.2% | 96% |
| Above 20 SMA | Trend | 45.5% | 45.4% (114/251) | -0.1% | 98% |
| Above 50 SMA | Trend | 45.5% | 45.5% (116/255) | -0.0% | 99% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| Gap Up Day | Context | 45.5% | 45.5% (112/246) | +0.0% | 96% |
| VIX Below 15 | Volatility | 45.5% | 45.6% (115/252) | +0.1% | 98% |
| Above Pivot | Position | 45.5% | 45.6% (115/252) | +0.1% | 98% |
| Above 5d High | Position | 45.5% | 45.6% (115/252) | +0.1% | 98% |
| Above Prior Day High | Position | 45.5% | 45.7% (116/254) | +0.1% | 99% |
| RSI Strong Trend | Momentum | 45.5% | 45.8% (116/253) | +0.3% | 98% |
| 52w Range Upper Half | Position | 45.5% | 45.9% (113/246) | +0.4% | 96% |
| RSI Trend Zone | Extremes | 45.5% | 46.1% (106/230) | +0.6% | 89% |
| VIX Below 20 | Volatility | 45.5% | 46.1% (113/245) | +0.6% | 95% |
| 52w Range Top 20% | Position | 45.5% | 46.3% (114/246) | +0.8% | 96% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
