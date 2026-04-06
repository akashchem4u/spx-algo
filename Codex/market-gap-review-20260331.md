# Market Gap Review

Updated: 2026-03-31 22:52 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Artifacts:
- `Codex/validation-artifacts/validation-20260331-225122.md`
- `Codex/session-reviews/session-review-20260331-225122.md`
- manual broad run: `python3 scripts/backtest_export.py --days 252`

## Findings

1. Recent daily core edge is weak.
- Updated 60-session Core SSR backtest: `23/48 = 47.92%`, below the `0.48` threshold.
- Recent misses cluster on `2026-03-24` and `2026-03-30`.
- Both were bearish core scores that failed on upside reversal sessions.

2. Broader daily edge still exists, but it is small and regime-sensitive.
- Broader 252-session daily Core SSR backtest: `107/207 = 51.69%`.
- Weakest broad regime is `gap-down` days: `12/32 = 37.5%`.
- This is the clearest structural gap in the current daily model.

3. Weekly direction is better than recent daily, but still only modestly useful.
- Weekly Core SSR validation on the broader run: `51/91 = 56.04%` with `8` neutral calls excluded.
- Treat this as a bias surface, not a path oracle.

4. The recent high-VIX tape is exposing a specific failure mode.
- The model still produced very bearish scores (`10`, `20`) into relief/upside squeeze days.
- That suggests the current regime logic is too willing to press bearish conviction after stress events instead of widening the neutral band.

5. Validation quality improved materially in this session.
- `scripts/backtest_export.py` now reconstructs the 28 closed-bar Core SSR signals much more closely.
- The exporter now warms enough history for SMA200 / 52-week range signals.
- `app.py` weekly validation now includes the current Friday bar and measures the following week from next-week open instead of Monday close.

## Recommendations

1. Add a regime-specific bear dampener for large down-gap setups.
- Priority case: `gap < -25` where `VIX 1d Down` or `VIX 3d Relief` is already positive.
- Goal: stop converting every stress setup into a hard bear call when the tape is vulnerable to squeeze/reversion.

2. Widen the neutral band in reversal-prone stress regimes.
- Candidate rule: high-VIX plus down-gap or high-VIX plus bounce follow-through should require a lower score before issuing a bear call.
- The current `<=44` bear cutoff is too eager in these conditions.

3. Make regime-specific ablation the next research pass.
- Test `VIX No Spike`, `Gap/ATR Normal`, `Above Prior Day High`, and `Above Pivot` specifically inside:
  - high-VIX sessions
  - gap-down sessions
  - first reversal day after a sharp selloff

4. Start recording forward misses in the shadow ledger with regime context.
- Add call direction, actual direction, `vix_regime`, and `gap_regime` to the daily ledger flow.
- Right now `Codex/shadow-ledger.csv` is still header-only, so we cannot prove live drift or recovery.

5. Keep trust claims conservative until recent daily performance improves.
- Broad backtests are still slightly positive.
- Recent daily performance is not strong enough yet to tighten investor-facing language.

## Concrete Next Actions For The Other Agent

1. Prototype a high-VIX / gap-down neutral-band override and rerun the 60-session daily exporter.
2. Compare pre-change vs post-change results for:
- overall 60-session daily accuracy
- gap-down regime accuracy
- the specific miss dates `2026-03-24` and `2026-03-30`
3. If the change improves only gap-downs but hurts the broad baseline, keep it as a regime gate instead of a global threshold change.
