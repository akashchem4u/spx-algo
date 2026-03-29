# Next Agent Action Plan

Updated: 2026-03-29 03:00 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- raise confidence in the app as an investor-facing decision tool
- stop optimizing appearance before measurement quality is strong
- make live recommendations easier to trust because their scope is explicit

---

## Executive View

The app is in a better state, but it is still not a high-confidence standalone recommendation engine.
The next work should focus on:

1. separating what is truly backtested from what is live-only
2. proving new signals out of sample instead of just adding more
3. creating a real shadow-performance ledger for 30-60 sessions

Do not spend the next cycle adding random variables unless they are paired with validation.

---

## Priority 1: Split Core Score From Live Overlay

Problem:
- the app mixes historically testable signals with live-only or same-session signals
- that makes the live score richer, but it weakens trust in the backtest comparison

What to implement:
- create three signal buckets inside `compute_ssr()` / scoring flow:
  - `core_backtested`
  - `session_context`
  - `live_overlay`
- examples:
  - `core_backtested`: trend, momentum, volatility, breadth, position signals that are valid from completed bars
  - `session_context`: gap/open-context signals that depend on the current session open
  - `live_overlay`: ORB width/distance, intraday RSI override, news sentiment, A/D, PCR, macro, overnight ES position
- produce two explicit scores in the app:
  - `Core SSR`
  - `Live Overlay Adjusted SSR`

Target files:
- `/Users/amummaneni/Desktop/Codex/Projects/spx-algo/app.py`

Acceptance criteria:
- the UI clearly shows which score is backtested vs live-adjusted
- the day backtest uses only the appropriate score for its time horizon
- signal breakdown labels each signal with one of: `core`, `session`, `live-only`

---

## Priority 2: Add Real Walk-Forward Validation By Regime

Problem:
- aggregate hit rates are not enough
- we need to know when the model works and when it fails

What to implement:
- add a research report that breaks results down by:
  - VIX regime: low / mid / high
  - gap regime: up / flat / down
  - weekday
  - event day vs non-event day
  - OpEx week vs normal week
- include:
  - sample size
  - directional accuracy
  - average error
  - coverage

Target files:
- `/Users/amummaneni/Desktop/Codex/Projects/spx-algo/app.py`
- optionally create a small helper report writer if the app file gets too crowded

Acceptance criteria:
- research tab shows regime tables, not just a single blended percentage
- weak regimes are obvious
- the team can say “use cautiously in X regime” with evidence

---

## Priority 3: Add Signal Ablation Testing

Problem:
- many new signals were added quickly
- we do not yet know which signals add real edge vs noise

What to implement:
- build a lightweight ablation loop:
  - baseline model
  - baseline + one new signal
  - baseline + one group
  - full model
- run this on the existing historical validation paths
- write results to a markdown or JSON artifact in `Codex/` or a `reports/` folder

Signals to test first:
- `Gap/ATR Normal`
- `VIX No Spike`
- `VIX 3d Relief`
- `Above Prior Day High`
- `Above Pivot`
- `Above 5d High`
- overnight ES position signals
- ORB width guard / ORB distance boost

Acceptance criteria:
- each newly added signal has evidence of:
  - improved accuracy
  - improved error
  - or improved regime-specific behavior
- any non-contributing signal is either downgraded or removed

---

## Priority 4: Build A Shadow Performance Ledger

Problem:
- investor confidence requires forward-tracked evidence, not only retrospective backtests

What to implement:
- create a daily snapshot log that records:
  - date
  - core SSR
  - live-adjusted SSR
  - current window bias
  - projected bias
  - key context: gap, VIX, event flags, ORB status
  - actual session result after close
- store as JSON or CSV locally
- add a small review surface to summarize the last 30-60 sessions

Target:
- a local artifact in the repo, for example:
  - `/Users/amummaneni/Desktop/Codex/Projects/spx-algo/Codex/shadow-ledger.csv`
  - or a simple append-only JSONL file

Acceptance criteria:
- the app can show “last 30 sessions, live-adjusted score hit X%, core score hit Y%”
- recommendations are measured on frozen daily outputs, not reconstructed later

---

## Priority 5: Clean Up The Remaining Low-Risk Accuracy Gaps

Implement after priorities 1-4:

1. Use adaptive `_slot_atr` in the live “Today So Far” chop threshold instead of flat `levels["atr"] / 6.5`.
2. Refactor `Gap/ATR Normal` so it uses an explicit `session_gap` parameter rather than implicitly reusing the latest completed bar context.
3. If feasible, reconstruct historical ORB width/distance for richer validation. If not feasible, keep it explicitly marked as live-only.

---

## What Not To Do Next

- do not add more signals before ablation testing
- do not advertise the app as “high-confidence” yet
- do not collapse live-only overlays into the same research claim as the core backtested score
- do not optimize UI wording ahead of measurement quality

---

## Suggested Order For The Next Agent

1. implement `Core SSR` vs `Live Overlay Adjusted SSR`
2. wire labels for `core` / `session` / `live-only` signals
3. add regime walk-forward tables
4. add ablation report
5. add shadow ledger
6. only then tune or add more variables

---

## Definition Of Done For Confidence Upgrade

The app can be described as materially more trustworthy only when all of the below are true:

- live score and backtested score are clearly separated
- the last 30-60 sessions are tracked in a forward shadow ledger
- regime breakdowns are visible
- major new signals have ablation evidence
- the team can explain when the model should be trusted and when it should be faded
