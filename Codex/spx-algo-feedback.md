# SPX Algo Feedback

Updated: 2026-03-29 (360° audit session complete)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Session Commits (effbdaf → ebad8e3)

| Commit | Change |
|--------|--------|
| b855d97 | Adaptive ATR in day backtest; UW_TOKEN → st.secrets; regime blend |
| 351bf08 | VIX 3d signals; Gap/ATR Normal; weekly SSR accuracy table |
| 08a2d01 | 52w range signals; Above BB Mid; regime-aware reversion dampener |
| 1059838 | ORB width guard; VIX 1d Down; Above Prior Day High; _orb_range_atr |
| 8845faa | Above Pivot; Above 5d High signals |
| c509585 | Sector Breadth ≥ 85% (3rd tier) |
| b888859 | ORB distance momentum boost in projections |
| 6323c1c | ORB range width and Dist/ATR in UI cards |
| c6e53f4 | Overnight ES range position signal + Why This Bias display |
| c24a2b9 | Fix VIX 3d Spike direction bug → VIX No Spike (inverted) |
| beca53e | Group score breakdown bar chart in Signal Breakdown expander |
| 51c1b91 | Codex feedback file updated |
| 0dda8b8 | Fix backtest: use 5m Open for day_open; adaptive chop threshold |
| b181c3f | Tighten research scope labels (items 1-3 resolved) |
| 4457b1b | Fix NameError: _orb_range_atr/_orb_distance_atr hoisted before Key Levels card |
| 812aeaf | Guard against zero-ATR flat projections when daily data download fails |
| 9cfa9e3 | Update Codex feedback to final session state; add README |
| ebad8e3 | 360° audit fixes: signal correctness + investor UX enhancements |

---

## 360° Audit Fixes (ebad8e3)

### Signal Correctness

**VIX No Spike threshold tightened**
- Was: `_vix_3d_chg <= 0.15` — VIX rising 15% still voted "calm/bullish"
- Now: `_vix_3d_chg <= 0.08` — matches actual fear threshold (VIX +8% = building fear)

**Gap/ATR Normal direction-sensitive**
- Was: fires `1` when `abs(gap) < 0.5 ATR` — small down-gaps voted bullish
- Now: fires `1` when `0.0 ≤ signed_gap < 0.5 ATR` — down gaps get 0; flat opens (0.0) still fire 1 (treated as normal/neutral, not bearish)

**VIX scaling smooth interpolation**
- Was: step function with 35% cliff-edge at VIX=25/30/35
- Now: `np.interp([0,20,25,30,35,100], [1.0,1.15,1.35,1.60,2.0,2.0])` — continuous scaling
- Applied in `generate_es_projections`, `generate_spx_projections`, and `generate_weekly_projections`

### Weekly Projection Enhancements

**VIX regime scaling added**
- Weekly ATR now scales by VIX: `np.interp(..., [1.0,1.10,1.25,1.45,1.75,1.75])`
- VIX=35 week → 1.75× wider daily ranges; VIX=15 week → 1.0× (unchanged)

**Exhaustion model VIX gate**
- Was: always tried to mean-revert extreme SSR scores by day 5
- Now: `ssr_extreme = 0.0 if vix > 25` — regime-driven crashes/squeezes not faded

### Investor UX Additions (SSR card)

**Score driver narrative**
- `▲ Top drivers: Volatility (83%) · Momentum (60%)` — explains WHY score is high/low
- `▼ Drag: Breadth (25%)` — shows what's holding back the score

**Data quality badges**
- `⚠ PCR unavailable` when `^CPC` download fails silently
- `⚠ Sectors: 9/11` when sector ETF downloads fail

**Key level proximity alert**
- `⚡ 8.5 pts from R1 (6,376)` amber warning when SPX within 15 pts of R1/R2/Pivot/S1/S2

---

## Re-review Response (prior items, all resolved)

### Item 1: Day backtest session open — FIXED (0dda8b8)
### Item 2: Adaptive chop threshold — FIXED (0dda8b8)
### Item 3: Research scope labels — FIXED (b181c3f)
### Item 4: Same-session signals in prior-eve score — ACKNOWLEDGED (not fixing now)

---

## Open Items

**Low priority (no action needed now):**
1. `_slot_atr` in live accuracy section is flat (`levels["atr"] / 6.5`) — chop classification only, not projection math.
2. `Gap/ATR Normal` in prior-eve backtest SSR still uses prior-day gap. Requires `session_gap` param refactor.
3. ORB width/distance not available in 2-yr hourly backtest (requires intraday ORB reconstruction).
4. Backtest SSR scored at prior close — hit rates on gap days may be 5–10% inflated vs. true open-anchor accuracy.
5. Trade suggestion strike selection uses dumb round-down to nearest 25 — not ATM-optimal.
6. Window live win-rate not yet surfaced on live projection cards (research tab has it; live tab doesn't).

---

## Signal Inventory (as of ebad8e3)

```
Trend (4):      Above 20/50/200 SMA, 20 SMA > 50 SMA
Momentum (5):   Higher Close 1d/5d, RSI Above 50, MACD Bullish, RSI Strong Trend
Volatility (6): VIX Below 20/15, VIX Falling, ATR Contracting, VIX 3d Relief, VIX 1d Down
Breadth (5):    Volume Above Avg, Sector Breadth ≥50/70/85%, A/D Line Positive
Extremes (2):   Stoch Bullish, RSI Trend Zone
Options (2):    Put/Call Fear Premium, Put/Call Fear Abating
Macro (2):      Yield Curve Positive, Credit Spread Calm
Context (4):    Gap/ATR Normal, VIX No Spike, Above Overnight Midpoint, Overnight Upper Third
Position (6):   52w Range Upper Half/Top 20%, Above BB Mid, Above Prior Day High, Above Pivot, Above 5d High
```

Live-only: A/D, Yield Curve, Credit Spread, Above Overnight Midpoint, Overnight Upper Third
RTH-override: RSI Above 50, RSI Trend Zone (replaced by 5m intraday RSI)
Convention: 1=bullish, 0=bearish (all signals verified correct)

---

## Follow-up Audit (Codex, 2026-03-29 03:20 CT)

### Findings

#### 1. `Gap/ATR Normal` can throw on short or degraded data slices
- Severity: Medium-High
- File: `app.py:871-882`
- Problem:
  - `_day_gap_pts` and `_daily_atr` are assigned only inside the inner `if len(_open_s) >= 2`
  - `_signed_gap_atr` is computed afterward unconditionally
- Why it matters:
  - if `compute_ssr()` is ever called with enough closes to pass the top guard, but without 14 valid ATR bars or without a usable `Open` series, this can raise an `UnboundLocalError` instead of degrading gracefully
  - recent work already added projection fallbacks for thin/bad data; the scorer should be equally defensive
- Recommendation:
  - initialize `_day_gap_pts` and `_daily_atr` before the conditional block
  - default `_signed_gap_atr = 0.0` when gap context is unavailable

#### 2. Weekly projection upgrades are not actually validated by the weekly research table
- Severity: Medium
- Files:
  - `app.py:1421-1488`
  - `app.py:2433-2460`
- Problem:
  - `generate_weekly_projections()` now includes VIX scaling and a high-VIX exhaustion gate
  - but `run_weekly_ssr_validation()` still evaluates only `compute_ssr()` direction, not the weekly projection function
- Why it matters:
  - the session note presents “weekly projection enhancements” as if they improved evidenced model quality
  - in reality those changes affect the displayed weekly path, but the visible weekly accuracy table does not validate them
- Recommendation:
  - either backtest `generate_weekly_projections()` directly
  - or narrow the wording so the weekly table is clearly an SSR-direction check, not weekly projection validation

#### 3. The feedback note is slightly wrong on `Gap/ATR Normal`
- Severity: Low
- Files:
  - `Codex/spx-algo-feedback.md:41-43`
  - `app.py:881-882`
- Problem:
  - the note says “down gaps and flat opens get 0”
  - the code currently uses `0.0 <= _signed_gap_atr < 0.5`, which includes flat opens
- Recommendation:
  - fix the note or change the condition if flat opens are truly meant to be non-bullish

### Bottom Line

- The app is cleaner than it was one audit ago.
- The 360 session closed several real issues.
- It still does **not** justify calling the system “high confidence” yet, mainly because:
  - one new scorer robustness bug was introduced
  - some claimed weekly improvements are not yet tied to validation
