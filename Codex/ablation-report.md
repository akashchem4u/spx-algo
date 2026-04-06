# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T08:16:50Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `212`  
**Baseline accuracy: 52.8%  (112/212)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 52.1% (62/119) | 119 |
| VIX:mid | 50.0% (38/76) | 76 |
| VIX:high | 70.6% (12/17) | 17 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 57.1% (24/42) | 42 |
| gap:flat | 52.1% (87/167) | 167 |
| gap:down | 33.3% (1/3) | 3 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 52.5% (21/40) | 40 |
| Tue | 50.0% (19/38) | 38 |
| Wed | 66.0% (31/47) | 47 |
| Thu | 44.2% (19/43) | 43 |
| Fri | 50.0% (22/44) | 44 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 57.1% (16/28) | 28 |
| normal | 52.2% (96/184) | 184 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 55.6% (25/45) | 45 |
| normal | 52.1% (87/167) | 167 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| Gap Down Contrarian | Context | 33.3% | 0.0% (0/1) | -33.3% | 33% |
| VIX No Spike | Context | 52.8% | 50.5% (96/190) | -2.3% | 90% |
| Higher Close (1d) | Momentum | 52.8% | 52.0% (106/204) | -0.9% | 96% |
| Above Pivot | Position | 52.8% | 52.0% (106/204) | -0.9% | 96% |
| Sector Breadth ≥ 50% | Breadth | 52.8% | 52.0% (104/200) | -0.8% | 94% |
| Higher Close (5d) | Momentum | 52.8% | 52.2% (108/207) | -0.7% | 98% |
| VIX Falling | Volatility | 52.8% | 52.2% (107/205) | -0.6% | 97% |
| Above Prior Day High | Position | 52.8% | 52.2% (107/205) | -0.6% | 97% |
| Above 5d High | Position | 52.8% | 52.2% (107/205) | -0.6% | 97% |
| Volume Above Average | Breadth | 52.8% | 52.2% (106/203) | -0.6% | 96% |
| 52w Range Upper Half | Position | 52.8% | 52.2% (106/203) | -0.6% | 96% |
| Above 20 SMA | Trend | 52.8% | 52.4% (108/206) | -0.4% | 97% |
| ATR Contracting | Volatility | 52.8% | 52.6% (110/209) | -0.2% | 99% |
| VIX 1d Down | Volatility | 52.8% | 52.6% (110/209) | -0.2% | 99% |
| MACD Bullish | Momentum | 52.8% | 52.7% (109/207) | -0.2% | 98% |
| RSI Strong Trend | Momentum | 52.8% | 52.7% (109/207) | -0.2% | 98% |
| Stoch Bullish | Extremes | 52.8% | 52.8% (94/178) | -0.0% | 84% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| Above 50 SMA | Trend | 52.8% | 52.9% (111/210) | +0.0% | 99% |
| Above 200 SMA | Trend | 52.8% | 52.9% (111/210) | +0.0% | 99% |
| VIX Below 20 | Volatility | 52.8% | 52.9% (111/210) | +0.0% | 99% |
| Sector Breadth ≥ 85% | Breadth | 52.8% | 52.9% (109/206) | +0.1% | 97% |
| VVIX Below 100 | Volatility | 52.8% | 53.6% (112/209) | +0.8% | 99% |
| VIX Below 15 | Volatility | 52.8% | 53.6% (111/207) | +0.8% | 98% |
| Gap Up Day | Context | 52.8% | 53.9% (104/193) | +1.1% | 91% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
