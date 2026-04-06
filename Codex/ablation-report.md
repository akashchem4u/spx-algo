# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T06:54:11Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `211`  
**Baseline accuracy: 49.3%  (104/211)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 49.6% (63/127) | 127 |
| VIX:mid | 50.0% (34/68) | 68 |
| VIX:high | 43.8% (7/16) | 16 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 53.7% (22/41) | 41 |
| gap:flat | 48.8% (81/166) | 166 |
| gap:down | 25.0% (1/4) | 4 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 51.4% (19/37) | 37 |
| Tue | 48.8% (21/43) | 43 |
| Wed | 62.2% (28/45) | 45 |
| Thu | 39.5% (17/43) | 43 |
| Fri | 44.2% (19/43) | 43 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 80.0% (4/5) | 5 |
| normal | 48.5% (100/206) | 206 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 53.1% (26/49) | 49 |
| normal | 48.1% (78/162) | 162 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| VIX No Spike | Context | 49.3% | 46.8% (88/188) | -2.5% | 89% |
| Sector Breadth ≥ 50% | Breadth | 49.3% | 47.7% (95/199) | -1.6% | 94% |
| Higher Close (5d) | Momentum | 49.3% | 47.8% (97/203) | -1.5% | 96% |
| 52w Range Upper Half | Position | 49.3% | 48.0% (95/198) | -1.3% | 94% |
| VIX Falling | Volatility | 49.3% | 48.0% (97/202) | -1.3% | 96% |
| Stoch Bullish | Extremes | 49.3% | 48.0% (85/177) | -1.3% | 84% |
| VIX Below 20 | Volatility | 49.3% | 48.3% (98/203) | -1.0% | 96% |
| Volume Above Average | Breadth | 49.3% | 48.5% (97/200) | -0.8% | 95% |
| RSI Above 50 | Momentum | 49.3% | 48.5% (99/204) | -0.8% | 97% |
| Gap Up Day | Context | 49.3% | 48.9% (92/188) | -0.4% | 89% |
| VIX 1d Down | Volatility | 49.3% | 49.0% (98/200) | -0.3% | 95% |
| Above 20 SMA | Trend | 49.3% | 49.0% (102/208) | -0.3% | 99% |
| VIX Below 15 | Volatility | 49.3% | 49.3% (99/201) | -0.0% | 95% |
| Higher Close (1d) | Momentum | 49.3% | 49.3% (100/203) | -0.0% | 96% |
| ATR Contracting | Volatility | 49.3% | 49.3% (100/203) | -0.0% | 96% |
| Above Pivot | Position | 49.3% | 49.3% (100/203) | -0.0% | 96% |
| MACD Bullish | Momentum | 49.3% | 49.3% (101/205) | -0.0% | 97% |
| RSI Strong Trend | Momentum | 49.3% | 49.3% (101/205) | -0.0% | 97% |
| Above 200 SMA | Trend | 49.3% | 49.3% (103/209) | -0.0% | 99% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| Above Prior Day High | Position | 49.3% | 49.5% (101/204) | +0.2% | 97% |
| Above 50 SMA | Trend | 49.3% | 49.5% (104/210) | +0.2% | 100% |
| Sector Breadth ≥ 85% | Breadth | 49.3% | 49.8% (101/203) | +0.5% | 96% |
| Above 5d High | Position | 49.3% | 49.8% (101/203) | +0.5% | 96% |
| Gap Down Contrarian | Context | 25.0% | 33.3% (1/3) | +8.3% | 75% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
