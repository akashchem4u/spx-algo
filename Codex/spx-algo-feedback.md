# SPX Algo Feedback

Updated: 2026-03-29 (Claude agent session)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Fixed This Session (commits 490f4b5 → effbdaf)

| Commit | Fix |
|--------|-----|
| 490f4b5 | live_gap hoisted above tab blocks, NameError on load fixed |
| f9cba94 | compute_ssr(as_of_dt), window_bias_at(event_types, weekday), 2yr validation calls window_bias_at(), day backtest passes historical context |
| 55dc501 | compute_group_weights passes as_of_dt; RSI Strong Trend + VIX Below 15 + Sector Breadth ≥70% gradient signals; regime-aware 0.55/0.45 blend |
| a10405e | 2yr NameError (gap_val/vix_val read before assign); day backtest as_of_dt; prior_close stored at module level; live accuracy anchored to prior_close not first bar close |
| effbdaf | Scope labels on all 3 research surfaces (PCR/macro/news/ORB exclusions, slot grids) |

---

## Open Items (as of effbdaf)

### High — still worth fixing

**A. Projection math in day backtest still uses flat slot_atr**
- File: `app.py` in `run_backtest_for_day()`
- Line: `slot_atr = bt_atr / 6.5` (flat equal distribution)
- Live SPX path uses adaptive `_atr_profile = [0.28, 0.18, 0.12, 0.08, 0.09, 0.11, 0.09, 0.05]`
- Impact: day backtest projection errors are larger than live errors because it doesn't front-load morning vol
- Fix: use the same adaptive ATR profile in `run_backtest_for_day()`

**B. 5m RSI override is live-only, not in backtest**
- File: `app.py:1625` (live) vs `run_backtest_for_day()` (historical)
- Live page replaces `RSI Above 50` and `RSI Trend Zone` signals with intraday 5m RSI during RTH
- Historical backtest still uses daily RSI for all slots including the morning ones
- This makes the model different between live and research
- Fix option 1: add historical 5m RSI computation to the day backtest (complex)
- Fix option 2: label the live SSR card as "includes intraday RSI override" and the backtest as "daily RSI only" (simple, honest)
- Recommendation: option 2 for now, option 1 when you have time

**C. Projection mean-reversion dampener (0.015) is never calibrated**
- File: `app.py` in both `generate_es_projections()` and `generate_spx_projections()`
- The `-_drift * 0.015` reversion factor is a constant, not calibrated to observed reversion speed by regime
- Low priority but worth noting for the calibration pass

### Medium — quality improvements

**D. A/D ratio from macro uses spot data (^ADVN/^DECL) which is session-only**
- Historical days don't have historical A/D ratio data from yfinance
- The `A/D Line Positive` signal is live-only in practice
- Label it, or remove from historical scoring

**E. Weekly projection has no backtest surface**
- `generate_weekly_projections()` uses DOW tendencies and SSR exhaustion
- No historical validation at all — users have no way to assess its accuracy
- Recommendation: add a simple rolling 5-week accuracy table in Research tab

**F. UW_TOKEN is hardcoded in app.py (line ~2301)**
- Should be `st.secrets["UW_TOKEN"]` or from env — currently exposed in plain text

---

## Recommended Next Fix Order

1. Fix flat slot_atr in day backtest (use adaptive profile) — high accuracy value
2. Add "includes intraday RSI override" label to live SSR card
3. Label A/D ratio as live-only in signal breakdown
4. Fix UW_TOKEN to use st.secrets
5. Add weekly projection 5-week accuracy table

---

## Notes to Codex agent

- The gap_val/vix_val NameError was critical — the 2-year table was returning empty for every session. Now fixed.
- The prior_close anchor for live accuracy was also a meaningful bug — the 09:30 slot was always "chop" before.
- Scope labels are now honest and accurate. No false claims of "full live model" in research paths.
- The main remaining work is projection accuracy (adaptive ATR in backtest) and RSI override labeling.
- Do not re-fix anything in the committed list above unless you find a new bug.
