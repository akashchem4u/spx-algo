# News Taxonomy Implementation Note

Updated: 2026-03-29 12:40 CT
Project: `/Users/amummaneni/Desktop/Codex/Projects/spx-algo`

Purpose:
- improve how the app tracks real-time geopolitical and macro news
- make the weightage reflect market reality, not just keyword presence
- help the model judge whether news should actually move markets

---

## Executive Summary

The current news taxonomy direction is mostly right, but the next upgrade should be:

1. split `threat` vs `action` vs `confirmed action`
2. split `mediation headline` vs `formal de-escalation`
3. apply a confirmation multiplier
4. confirm the news with cross-asset price action
5. log the event and its market impact to the shadow ledger

Do not just add more phrases. The next step is a state machine with better weight control.

---

## What Is Good Right Now

Current taxonomy in `app.py` already does a useful first-pass classification:
- `US_IRAN_WAR = 4.5`
- `OIL_SUPPLY_SHOCK = 4.0`
- `IRAN_ESCALATION = 3.5`
- `IRAN_DEESCALATION = 3.0`
- `PAKISTAN_MEDIATION_PROGRESS = 2.5`

This is directionally correct.

The weakness is not sign. The weakness is granularity.

Examples:
- actual Hormuz closure and mere Hormuz threat are still too close together conceptually
- confirmed US military action and deployment rhetoric are still too close together
- mediation chatter and real peace-process progress should not have the same effective force

---

## Priority 1: Convert Geo Taxonomy To Event Ladders

### A. Iran / US / Israel escalation ladder

Create separate categories for:
- `IRAN_THREAT`
- `IRAN_ESCALATION_LIMITED`
- `US_FORCE_MOBILIZATION_IRAN`
- `US_DIRECT_ACTION_IRAN`
- `US_IRAN_WAR_CONFIRMED`

Suggested base weights:
- `IRAN_THREAT`: `1.5`
- `IRAN_ESCALATION_LIMITED`: `2.5`
- `US_FORCE_MOBILIZATION_IRAN`: `3.0`
- `US_DIRECT_ACTION_IRAN`: `4.0`
- `US_IRAN_WAR_CONFIRMED`: `5.0`

Interpretation:
- threats and rhetoric matter, but less than missiles or confirmed strikes
- boots on the ground / direct US combat is top-tier risk and should dominate the news score

### B. Hormuz / oil disruption ladder

Create separate categories for:
- `HORMUZ_THREAT`
- `HORMUZ_DISRUPTION`
- `HORMUZ_CLOSURE_CONFIRMED`
- `OIL_INFRA_ATTACK`

Suggested base weights:
- `HORMUZ_THREAT`: `2.0`
- `HORMUZ_DISRUPTION`: `3.5`
- `HORMUZ_CLOSURE_CONFIRMED`: `5.0`
- `OIL_INFRA_ATTACK`: `4.0`

Interpretation:
- “may close Hormuz” is not the same as actual shipping disruption
- actual closure should be treated as a top-tier market event

### C. Mediation / de-escalation ladder

Create separate categories for:
- `MEDIATION_HEADLINE`
- `FORMAL_TALKS`
- `CEASEFIRE_PROGRESS`
- `CEASEFIRE_CONFIRMED`
- `SANCTIONS_RELIEF_OR_DEAL`

Suggested base weights:
- `MEDIATION_HEADLINE`: `1.0`
- `FORMAL_TALKS`: `1.5`
- `CEASEFIRE_PROGRESS`: `2.5`
- `CEASEFIRE_CONFIRMED`: `3.5`
- `SANCTIONS_RELIEF_OR_DEAL`: `3.0`

Interpretation:
- Pakistan mediating is bullish, but not as strong as an actual ceasefire or verified diplomatic agreement
- the app should avoid overreacting to one mediation headline

---

## Priority 2: Add A Confirmation Multiplier

Problem:
- not all headlines are equally trustworthy

Implement a `confirmation_multiplier` applied after category weight.

Suggested values:
- rumor / social quote / unconfirmed chatter: `0.50x`
- single-source news report: `0.75x`
- reputable multi-source coverage: `1.00x`
- official statement / government confirmation: `1.25x`

Target path:
- `_keyword_impact()`
- `load_news()`

Implementation idea:
- add source-tier metadata for each feed item
- parse confirmation words:
  - bearish confirmation words: `confirmed`, `official`, `deployed`, `closed`, `launched`
  - low-confidence words: `may`, `could`, `considering`, `reportedly`, `plans`

Acceptance criteria:
- a headline like “US may deploy boots on the ground” scores lower than “Pentagon confirms US ground deployment”
- a headline like “Pakistan offers mediation” scores lower than “Iran, US, and Israel enter formal talks”

---

## Priority 3: Add Cross-Asset News Confirmation

Problem:
- if the news taxonomy says “bearish escalation” but oil, VIX, and ES do not confirm it, the model should reduce confidence

Add a `market_confirmation_multiplier`.

Suggested data inputs:
- `CL=F` or another crude proxy
- `^VIX`
- `ES=F`
- optionally gold or 10y Treasury futures if easy to add

Suggested logic:
- escalation confirmed if:
  - oil up materially
  - VIX up
  - ES down
- de-escalation confirmed if:
  - oil down
  - VIX down
  - ES up

Suggested multiplier:
- no confirmation: `0.60x`
- partial confirmation: `0.85x`
- full confirmation: `1.10x`

Target files:
- `fetch_live()`
- `load_news()`
- `_keyword_impact()` or a wrapper around it

Acceptance criteria:
- news score strength should rise when cross-asset moves agree with the headline
- news score should soften when price action disagrees

---

## Priority 4: Add Time Decay For News Impact

Problem:
- breaking war headlines matter most in the first 30-90 minutes
- stale bullish/bearish articles should not keep dominating the live score

Implement a simple recency decay:
- 0-30 min: `1.00x`
- 30-90 min: `0.80x`
- 90-240 min: `0.55x`
- same day but stale: `0.30x`

Acceptance criteria:
- a fresh confirmed escalation outranks a stale article from earlier in the day
- the composite score becomes more reactive to what is actually new

---

## Priority 5: Add Geo Event State To Shadow Ledger

Problem:
- we need to learn which news categories actually move markets

Extend the shadow ledger in `app.py` to record:
- top news category
- raw news score
- confirmation multiplier
- cross-asset confirmation state
- final effective news contribution

Add a small research view:
- last 30-60 sessions by top news category
- average same-day SPX move
- average VIX move
- hit rate of bullish vs bearish classification

Acceptance criteria:
- the team can say which categories actually mattered
- weights can be recalibrated from observed market behavior, not just intuition

---

## Suggested Weight Map

These are the working investor-style weight bands:

- `5.0`
  - confirmed US-Iran direct war
  - confirmed Hormuz closure
  - major regional oil infrastructure hit with confirmed supply interruption

- `4.0`
  - direct US strikes / sustained Iran-Israel missile exchange
  - confirmed oil shipping disruption
  - verified military action with broad market confirmation

- `3.0`
  - force mobilization, carrier deployment, sanctions escalation, severe rhetoric with confirmation

- `2.0`
  - credible threat headlines, early mediation progress, formal talks start

- `1.0`
  - chatter, tentative diplomacy, soft speculation, low-confidence commentary

Important rule:
- de-escalation weights should almost always be lower than confirmed war weights unless the de-escalation is formal and verified

---

## Concrete Code Targets

Primary files:
- `/Users/amummaneni/Desktop/Codex/Projects/spx-algo/app.py`

Functions to touch:
- `_NEWS_IMPACTS`
- `_CATEGORY_AMP`
- `_keyword_impact()`
- `load_news()`
- `fetch_live()` if adding cross-asset confirmation feeds
- shadow ledger section near the existing `Shadow Performance Ledger`

---

## What Not To Do

- do not treat every geopolitical mention of Iran as the same event
- do not give mediation headlines the same force as confirmed ceasefire / deal
- do not keep expanding keyword lists without adding event-state logic
- do not assume headline direction is enough without checking oil / VIX / ES

---

## Definition Of Done

This news layer is materially better only when:
- threats, actions, and confirmed actions are separated
- mediation, talks, and ceasefire are separated
- confirmation multiplier exists
- cross-asset confirmation exists
- the shadow ledger records news category vs realized market effect
