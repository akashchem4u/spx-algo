# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T07:01:16Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `211`  
**Baseline accuracy: 52.6%  (111/211)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 50.4% (60/119) | 119 |
| VIX:mid | 52.7% (39/74) | 74 |
| VIX:high | 66.7% (12/18) | 18 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 55.8% (24/43) | 43 |
| gap:flat | 52.1% (86/165) | 165 |
| gap:down | 33.3% (1/3) | 3 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 53.7% (22/41) | 41 |
| Tue | 47.2% (17/36) | 36 |
| Wed | 66.0% (31/47) | 47 |
| Thu | 44.2% (19/43) | 43 |
| Fri | 50.0% (22/44) | 44 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 61.5% (16/26) | 26 |
| normal | 51.4% (95/185) | 185 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 57.8% (26/45) | 45 |
| normal | 51.2% (85/166) | 166 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| VIX No Spike | Context | 52.6% | 51.6% (98/190) | -1.0% | 90% |
| Stoch Bullish | Extremes | 52.6% | 51.6% (94/182) | -1.0% | 86% |
| Above 5d High | Position | 52.6% | 52.0% (105/202) | -0.6% | 96% |
| Volume Above Average | Breadth | 52.6% | 52.2% (106/203) | -0.4% | 96% |
| Gap Up Day | Context | 52.6% | 52.3% (101/193) | -0.3% | 91% |
| Above 20 SMA | Trend | 52.6% | 52.4% (108/206) | -0.2% | 98% |
| Higher Close (1d) | Momentum | 52.6% | 52.5% (107/204) | -0.2% | 97% |
| Above Prior Day High | Position | 52.6% | 52.5% (107/204) | -0.2% | 97% |
| Sector Breadth ≥ 50% | Breadth | 52.6% | 52.5% (104/198) | -0.1% | 94% |
| Above 200 SMA | Trend | 52.6% | 52.6% (111/211) | +0.0% | 100% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| VIX Below 15 | Volatility | 52.6% | 52.7% (107/203) | +0.1% | 96% |
| Above 50 SMA | Trend | 52.6% | 52.9% (111/210) | +0.3% | 100% |
| VIX Below 20 | Volatility | 52.6% | 52.9% (109/206) | +0.3% | 98% |
| Above Pivot | Position | 52.6% | 52.9% (109/206) | +0.3% | 98% |
| Sector Breadth ≥ 85% | Breadth | 52.6% | 53.0% (107/202) | +0.4% | 96% |
| MACD Bullish | Momentum | 52.6% | 53.1% (110/207) | +0.5% | 98% |
| RSI Strong Trend | Momentum | 52.6% | 53.2% (109/205) | +0.6% | 97% |
| VIX Falling | Volatility | 52.6% | 53.2% (109/205) | +0.6% | 97% |
| VIX 1d Down | Volatility | 52.6% | 53.2% (109/205) | +0.6% | 97% |
| ATR Contracting | Volatility | 52.6% | 53.2% (108/203) | +0.6% | 96% |
| Higher Close (5d) | Momentum | 52.6% | 53.4% (109/204) | +0.8% | 97% |
| 52w Range Upper Half | Position | 52.6% | 53.5% (108/202) | +0.9% | 96% |
| RSI Above 50 | Momentum | 52.6% | 53.6% (111/207) | +1.0% | 98% |
| Gap Down Contrarian | Context | 33.3% | 50.0% (1/2) | +16.7% | 67% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
