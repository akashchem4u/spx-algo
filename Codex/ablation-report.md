# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T06:19:00Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `261`  
**Baseline accuracy: 45.2%  (118/261)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 44.8% (64/143) | 143 |
| VIX:mid | 45.3% (39/86) | 86 |
| VIX:high | 46.9% (15/32) | 32 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 51.0% (26/51) | 51 |
| gap:flat | 46.2% (80/173) | 173 |
| gap:down | 32.4% (12/37) | 37 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 49.0% (25/51) | 51 |
| Tue | 38.0% (19/50) | 50 |
| Wed | 55.4% (31/56) | 56 |
| Thu | 37.0% (20/54) | 54 |
| Fri | 46.0% (23/50) | 50 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 62.5% (5/8) | 8 |
| normal | 44.7% (113/253) | 253 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 47.5% (28/59) | 59 |
| normal | 44.6% (90/202) | 202 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| Gap Down Contrarian | Context | 32.4% | 31.4% (11/35) | -1.0% | 95% |
| Higher Close (1d) | Momentum | 45.2% | 44.7% (115/257) | -0.5% | 98% |
| VIX 1d Down | Volatility | 45.2% | 44.9% (114/254) | -0.3% | 97% |
| Volume Above Average | Breadth | 45.2% | 44.9% (115/256) | -0.3% | 98% |
| Above 200 SMA | Trend | 45.2% | 45.0% (116/258) | -0.2% | 99% |
| Gap Up Day | Context | 45.2% | 45.0% (113/251) | -0.2% | 96% |
| RSI Above 50 | Momentum | 45.2% | 45.1% (115/255) | -0.1% | 98% |
| Stoch Bullish | Extremes | 45.2% | 45.1% (115/255) | -0.1% | 98% |
| VIX No Spike | Context | 45.2% | 45.2% (103/228) | -0.0% | 87% |
| Sector Breadth ≥ 50% | Breadth | 45.2% | 45.2% (108/239) | -0.0% | 92% |
| RSI Trend Zone | Extremes | 45.2% | 45.2% (108/239) | -0.0% | 92% |
| Above 50 SMA | Trend | 45.2% | 45.2% (118/261) | +0.0% | 100% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| Higher Close (5d) | Momentum | 45.2% | 45.3% (115/254) | +0.1% | 97% |
| 52w Range Upper Half | Position | 45.2% | 45.3% (115/254) | +0.1% | 97% |
| 52w Range Top 20% | Position | 45.2% | 45.3% (115/254) | +0.1% | 97% |
| Above 20 SMA | Trend | 45.2% | 45.3% (116/256) | +0.1% | 98% |
| Sector Breadth ≥ 85% | Breadth | 45.2% | 45.3% (116/256) | +0.1% | 98% |
| Above Pivot | Position | 45.2% | 45.3% (116/256) | +0.1% | 98% |
| RSI Strong Trend | Momentum | 45.2% | 45.3% (117/258) | +0.1% | 99% |
| VIX Below 15 | Volatility | 45.2% | 45.3% (117/258) | +0.1% | 99% |
| Above 5d High | Position | 45.2% | 45.3% (117/258) | +0.1% | 99% |
| VIX Falling | Volatility | 45.2% | 45.4% (114/251) | +0.2% | 96% |
| ATR Contracting | Volatility | 45.2% | 45.5% (115/253) | +0.2% | 97% |
| Above BB Mid | Position | 45.2% | 45.5% (116/255) | +0.3% | 98% |
| Above Prior Day High | Position | 45.2% | 45.5% (117/257) | +0.3% | 98% |
| MACD Bullish | Momentum | 45.2% | 45.6% (118/259) | +0.3% | 99% |
| VIX Below 20 | Volatility | 45.2% | 45.6% (115/252) | +0.4% | 97% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
