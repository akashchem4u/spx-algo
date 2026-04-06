# Peer Review Handoff

Updated: 2026-04-06 (enhancement lane — all 7 findings resolved)
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

---

## Resolution Status

All findings from the review lane have been addressed in the enhancement lane.
Current HEAD: see `git log --oneline -5` for latest commits.

---

## Finding Resolution Log

### Finding #1 — Backtest alignment drift between exporter and live app
**Status: RESOLVED**
- `model_alignment` renamed `equal_weight_static_core` (was misleading `core_ssr_clone`)
- Exporter `limitations[]` explicitly documents: live Core SSR uses drift dampening +
  dynamic group weights; exporter does not — accuracy numbers are a floor estimate only
- Code comment added in `app.py` at Core SSR computation block

### Finding #2 — Weekly SSR validator uses 5 sectors, row-position slicing
**Status: RESOLVED**
- Weekly validator expanded from 5 → 11 sector ETFs (XLF/XLK/XLE/XLV/XLI/XLC/XLY/XLP/XLB/XLRE/XLU)
- VIX and sector slices are now date-aligned (`index <= cutoff_ts`) not row-position
- Caption updated to reflect 11 sectors and date-alignment

### Finding #3 — Behavior validation can report success while backtest fails
**Status: RESOLVED**
- `--profile behavior` now gates `ok` on the 60d backtest result (same as `release`)
- A 60d accuracy below 48% → artifact `ok: false`

### Finding #4 — Adaptive group weights on tiny noisy sample + flat days forced bear
**Status: RESOLVED**
- VIX and sector fetch extended from 30d/60d → 100d so calibration has full history
- `compute_group_weights()` now iterates last 60 daily bars (not 20 5m days)
  using date-aligned VIX/sector slices; falls back to daily close-to-close direction
  when 5m intraday unavailable
- Flat days (SPX move < 5 pts) are skipped — previously forced into -1 (bear),
  biasing group weights downward in choppy/flat regimes
- Minimum effective-n raised from 5 → 10 before trusting any group's accuracy
- Same-session self-referential leak: `_eval_days = _dl[max(0, _td-61): _td-1]`
  already excludes today's partial session from calibration

### Finding #5 — Window calibration aggregates override variants under base label
**Status: RESOLVED**
- `aggregate_window_stats()` now keys by full label including override suffix
  (e.g. "Morning Trend (hi-VIX→bear)") — bull/bear/chop variants tracked separately
- `base_label` stored alongside for lookup fallback
- `windows_html()` tries full label first, falls back to base label match
  so windows without regime overrides still surface aggregate accuracy badges

### Finding #6 — Shadow ledger two competing write paths (U/D vs bull/bear/flat)
**Status: RESOLVED**
- Early write path (Path 1, ~line 2893) that wrote `actual_dir` as `"U"`/`"D"` has been
  removed entirely — replaced with a comment documenting why it was removed
- Unified to Path 2 only (~line 5245): writes `actual_dir=""` and lets
  `_ledger_fill_actuals()` normalize retroactively to `"bull"`/`"bear"`/`"flat"`
- Existing shadow-ledger.csv had no contaminated rows (header only — no data yet)
- The display accuracy logic at ~line 5288 now receives correctly normalized values

### Finding #7 — Volume signal fires without price direction confirmation
**Status: RESOLVED**
- `Volume Above Average` now requires BOTH above-avg volume AND positive close
  (close > prev close) — accumulation signal, not raw volume
- Applied consistently in `app.py` and `scripts/backtest_export.py`

---

## Remaining Open Items

None from this review cycle.

### Structural limitation (acknowledged, not patched)
- 60d backtest accuracy: ~43.8% — below the 48% threshold
- Gap-up accuracy: 25% (2/8) — structural limitation of backward-looking SSR
  in bear-trend + gap-up reversal environments
- The behavior validation artifact now correctly reflects this as FAIL
- No additional tuning attempted on the directional call threshold — the current
  regime (high volatility, gap-up bounces in a downtrend) is genuinely hard for
  a prior-close momentum model

---

## Direct Message To The Review Agent

All 7 findings have been resolved and pushed to `origin/main`. The shadow ledger
write path is now unified. The weekly validator uses 11 sectors with date alignment.
The group weight calibration covers 60 days and skips flat sessions. The behavior
validation is now a real gate.

If you run a new validation pass, expect `ok: false` on 60d backtest (model is below
threshold in the current regime — this is honest). Syntax should pass. The model
alignment label is `equal_weight_static_core`.
