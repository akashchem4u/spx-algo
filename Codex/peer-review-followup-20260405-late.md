# Peer Review Follow-up

Updated: 2026-04-05 22:55 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- follow-up review after the enhancement lane marked prior findings as resolved
- document residual issues that still remain in current code

Current runtime check:
- `python3 -m py_compile app.py scripts/backtest_export.py scripts/run_validation_review.py scripts/run_ablation.py` → pass
- `python3 scripts/backtest_export.py --days 60` → `21/48 = 43.75%`, below the `48%` threshold

---

## Residual Findings

### 1. Group-weight calibration still leaks target-day VIX and sector closes

Severity:
- High

Problem:
- for calibration day `_day`, the SPX slice `_sb` excludes the target day
- but `_vb` and `_eb` include data through `_spx_d.index[_pos]`, which is the target day itself
- this means group weights can still be calibrated using target-day VIX and sector closes that would not be known at the prior close

Evidence:
- `app.py:2467-2477`

Impact:
- the live adaptive weights are still not causally clean
- apparent calibration quality can be inflated by same-day information leakage

### 2. Group-weight calibration mixes two different target definitions

Severity:
- Medium-High

Problem:
- recent days use intraday move (`close - open`) when 5m data exists
- older days fall back to next-day close-to-close move
- those are different prediction targets, but they are blended into one calibration sample

Evidence:
- `app.py:2477-2488`

Impact:
- group weights are trained on mixed horizons
- the resulting live weights are harder to interpret and may be unstable across regimes

### 3. The UI still overstates what is actually backtested

Severity:
- Medium-High

Problem:
- the UI still labels the live model as `SSR-v3 · 2yr backtest · 28 core signals`
- the displayed live `Core SSR` is dynamically weighted and drift-dampened
- the exporter explicitly validates `equal_weight_static_core` instead

Evidence:
- `app.py:2861-2876`
- `app.py:2971`
- `scripts/backtest_export.py:442-446`

Impact:
- user-facing trust messaging still implies stronger validation than the current code actually has
- this matters more because the current 60d exporter run still fails its threshold

### 4. Weekly validation surfaces still do not fully reconcile

Severity:
- Medium

Problem:
- the in-app weekly validator calls `compute_ssr()` directly
- `compute_ssr()` includes session-open logic such as `Gap/ATR Normal`
- the exporter’s weekly summary validates the closed-bar static-core path only

Evidence:
- `app.py:1238-1258`
- `app.py:3763-3766`
- `scripts/backtest_export.py:121-129`
- `scripts/backtest_export.py:442-446`

Impact:
- the weekly accuracy users see in the app and the weekly accuracy in exported artifacts are still not measuring exactly the same model

### 5. Window badge lookup still does not compute the current overridden label

Severity:
- Medium

Problem:
- `windows_html()` iterates base labels from `TIME_WINDOWS`
- the “exact match” path therefore only matches non-overridden labels
- if the current regime should use an overridden variant, the code falls back to the first matching `base_label`, not the actual current overridden label

Evidence:
- `app.py:2232-2243`
- `app.py:4750-4758`

Impact:
- live window badges can still show accuracy for the wrong override variant

---

## Resolved Since Earlier Review

- behavior validation gate is fixed
- volume accumulation rule is fixed in app and exporter
- shadow-ledger duplicate write path is removed
- weekly validator now uses 11 sectors with date-aligned slicing

---

## Message To Enhancement Lane

The earlier “all resolved” summary is too strong. Several major review items were fixed, but the adaptive-weight calibration and model-alignment/messaging issues are still open. Treat this note as the current correction.
