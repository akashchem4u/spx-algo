# Ablation Report — SPX Algo Core SSR

Generated: `2026-04-06T07:27:15Z`  
Walk-forward period: `2025-01-21` → `2026-04-01`  
Total directional calls evaluated: `213`  
**Baseline accuracy: 54.0%  (115/213)**

---

## Regime Breakdown

### VIX Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| VIX:low | 52.5% (64/122) | 122 |
| VIX:mid | 52.7% (39/74) | 74 |
| VIX:high | 70.6% (12/17) | 17 |

### Gap Regime

| Regime | Accuracy | Calls |
|--------|----------|-------|
| gap:up | 56.8% (25/44) | 44 |
| gap:flat | 53.6% (89/166) | 166 |
| gap:down | 33.3% (1/3) | 3 |

### Day of Week

| Day | Accuracy | Calls |
|-----|----------|-------|
| Mon | 56.4% (22/39) | 39 |
| Tue | 51.4% (19/37) | 37 |
| Wed | 66.7% (32/48) | 48 |
| Thu | 45.5% (20/44) | 44 |
| Fri | 48.9% (22/45) | 45 |

### Event Days (FOMC/CPI/NFP)

| Type | Accuracy | Calls |
|------|----------|-------|
| event | 58.6% (17/29) | 29 |
| normal | 53.3% (98/184) | 184 |

### OpEx Week

| Type | Accuracy | Calls |
|------|----------|-------|
| opex | 58.7% (27/46) | 46 |
| normal | 52.7% (88/167) | 167 |

---

## Signal Ablation

Each row shows what happens when one signal is removed from the model.  
**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; negative = signal helps).  
**Coverage** = fraction of directional calls preserved after removal.

| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |
|--------|-------|-------------|----------|-------|----------|
| VIX No Spike | Context | 54.0% | 51.1% (97/190) | -2.9% | 89% |
| Stoch Bullish | Extremes | 54.0% | 51.9% (94/181) | -2.1% | 85% |
| Above 5d High | Position | 54.0% | 52.2% (107/205) | -1.8% | 96% |
| Sector Breadth ≥ 50% | Breadth | 54.0% | 52.5% (105/200) | -1.5% | 94% |
| 52w Range Upper Half | Position | 54.0% | 52.5% (105/200) | -1.5% | 94% |
| Above Pivot | Position | 54.0% | 52.7% (109/207) | -1.3% | 97% |
| Higher Close (1d) | Momentum | 54.0% | 52.7% (107/203) | -1.3% | 95% |
| Above Prior Day High | Position | 54.0% | 52.9% (110/208) | -1.1% | 98% |
| Higher Close (5d) | Momentum | 54.0% | 52.9% (108/204) | -1.0% | 96% |
| Volume Above Average | Breadth | 54.0% | 53.2% (108/203) | -0.8% | 95% |
| Sector Breadth ≥ 85% | Breadth | 54.0% | 53.4% (110/206) | -0.6% | 97% |
| VIX Below 20 | Volatility | 54.0% | 53.6% (113/211) | -0.4% | 99% |
| Gap Up Day | Context | 54.0% | 53.6% (104/194) | -0.4% | 91% |
| RSI Strong Trend | Momentum | 54.0% | 53.6% (111/207) | -0.4% | 97% |
| MACD Bullish | Momentum | 54.0% | 53.7% (109/203) | -0.3% | 95% |
| Above 200 SMA | Trend | 54.0% | 53.8% (114/212) | -0.2% | 100% |
| Above 50 SMA | Trend | 54.0% | 53.8% (113/210) | -0.2% | 99% |
| VIX Falling | Volatility | 54.0% | 53.8% (113/210) | -0.2% | 99% |
| ATR Contracting | Volatility | 54.0% | 53.8% (112/208) | -0.1% | 98% |
| VIX Below 15 | Volatility | 54.0% | 53.8% (112/208) | -0.1% | 98% |
| Above 20 SMA | Trend | 54.0% | 53.9% (111/206) | -0.1% | 97% |
| Put/Call Fear Premium | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Put/Call Fear Abating | Options | n/a | n/a (coverage loss) | n/a | 0% |
| Yield Curve Positive | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Credit Spread Calm | Macro | n/a | n/a (coverage loss) | n/a | 0% |
| Gap/ATR Normal | Context | n/a | n/a (coverage loss) | n/a | 0% |
| VIX 1d Down | Volatility | 54.0% | 54.2% (115/212) | +0.3% | 100% |
| Gap Down Contrarian | Context | 33.3% | 50.0% (1/2) | +16.7% | 67% |

---

## Notes

- Equal group weights used throughout (ablation-consistent, no drift dampening).
- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.
- Session-open and live-overlay signals excluded (closed-bar core only).
- Regime breakdown and ablation share the same walk-forward universe.
