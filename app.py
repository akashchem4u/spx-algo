"""
SPX Algo — Player224 Style  |  Streamlit Web UI
Run: streamlit run spx_app.py
"""

import sys, io, re, os, csv, xml.etree.ElementTree as _ET
from datetime import datetime, date, timedelta, timezone
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as _components
import urllib.request, urllib.error, urllib.parse
import json as _json

# ─────────────────────────────────────────────────────────────────────────────
EST = pytz.timezone("America/New_York")

TIME_WINDOWS = [
    ("09:30", "09:40", "Open Volatility",        "chop"),
    # Pre-Bull Fade: bear on gap-down days (100% hit), chop on gap-up (17% hit)
    # → gap-conditional handled in window_bias_at()
    ("09:40", "10:00", "Pre-Bull Fade",          "bear"),
    ("10:00", "10:30", "Bull Window",            "bull"),
    ("10:30", "10:45", "Bull→Bear Transition",   "chop"),
    # Bear Dom split: 10:45 & 11:00 & 11:30 changed to chop (35-45% as bear = misleading)
    # 11:15 kept as bear (65% hit rate — only reliable sub-slot)
    ("10:45", "11:00", "Bear Dom Bounce",         "chop"),
    ("11:00", "11:15", "Intraday Bounce",        "chop"),
    ("11:15", "11:30", "Bear Continuation",      "bear"),
    ("11:30", "12:00", "Intraday Chop",          "chop"),
    ("12:00", "13:00", "Lunch Chop",             "chop"),
    # Bull Window 2: changed to chop (40% as bull across all gap types)
    ("13:00", "13:15", "1PM Pivot",              "chop"),
    ("13:15", "14:00", "Bear Window / Peak",     "bear"),
    # 1:30–3:00 PM: bear on gap-down (65-70%), chop on gap-up (33%)
    # → gap-conditional handled in window_bias_at()
    ("14:00", "15:00", "Afternoon Trend",        "bear"),
    ("15:00", "15:30", "Bounce / Fade Setup",    "chop"),
    ("15:30", "16:00", "EOD Trend",              "bear"),
    ("19:11", "20:00", "AH Bull Window (ES)",    "bull"),
    ("20:00", "21:00", "AH Bear Window (ES)",    "bear"),
]

# Gap thresholds (points) for conditional window overrides
GAP_THRESHOLD = 25.0

# VIX regime thresholds for window bias overrides
VIX_FEAR_THRESHOLD = 25.0   # high fear → chop windows trend bear, bull windows soften
VIX_CALM_THRESHOLD = 18.0   # low vol  → bear windows soften, chop is more likely real chop

# ── Signal groups — audited & cleaned ────────────────────────────────────────
# Removed signals (with reasons):
#   "Above 9 EMA"          → redundant with "Above 20 SMA" (same direction, 90% correlated)
#   "MACD Rising"          → redundant with "MACD Bullish" (derivative of same series)
#   "RSI Not Overbought"   → descriptive not predictive; fires ~80% of time (no edge)
#   "RSI Not Oversold"     → fires ~95% of time; no directional information
#   "Stoch Not Overbought" → descriptive; fires ~85% of time
#   "VIX Below 25"         → redundant threshold alongside "VIX Below 20"
#   "VIX Below 10d Avg"    → 85% correlated with "VIX Falling"; adds noise not signal
#   "Above BB Mid"         → RETAINED in Position group (short-term trend context)
#   "Above BB Lower"       → fires ~95% of time (only off during crashes); valueless intraday
#   "Not at BB Upper"      → fires ~95% of time; no predictive edge
#   "Sector Breadth ≥ 30%" → triple-threshold redundancy; keep only 50% threshold
#   "Sector Breadth ≥ 70%" → same; stricter threshold doesn't add independent info
#
# Fixed signal directions:
#   "ATR Contracting"      → REVERSED from "ATR Expanding". High ATR = fear = NOT bullish.
#                            ATR contracting = calm market = better for continuation trades.
#   "Put/Call Fear Premium"→ REVERSED from "PCR < 1". Retail with PCR < 1 = complacent = bearish.
#                            PCR > 1 = fear premium = contrarian BULLISH (smart money hedge unwind).
#   "Put/Call Fear Abating"→ PCR falling FROM elevated level = fear winding down = bullish context.
#
# Added:
#   "RSI Trend Zone"       → RSI in 45–65 range = healthy trend, not extreme. More predictive
#                            than "RSI Not Overbought" which fires even at RSI=51.
SIGNAL_GROUPS = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA", "20 SMA > 50 SMA"],
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "RSI Above 50", "MACD Bullish",
                   "RSI Strong Trend"],          # RSI 60-75: strong momentum, not extreme
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting",
                   "VIX Below 15",              # ultra-calm tier for gradient
                   "VIX 3d Relief",              # 3-day VIX decline = fear unwinding = bull
                   "VIX 1d Down"],               # day-over-day VIX decline (live + historical)
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%", "A/D Line Positive",
                   "Sector Breadth ≥ 70%",        # strong breadth: second tier for gradient
                   "Sector Breadth ≥ 85%"],        # near-full breadth: third tier
    "Extremes":   ["Stoch Bullish", "RSI Trend Zone"],
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
    "Context":    ["Gap/ATR Normal",             # gap < 0.5× daily ATR = low-conviction open
                   "VIX No Spike",               # no 3-day VIX surge = calm context (inverted: 0 when spike)
                   "Gap Up Day",                 # open > prev close + GAP_THRESHOLD = large positive gap
                   "Above Overnight Midpoint",   # ES holding upper half of overnight range (live-only)
                   "Overnight Upper Third",       # ES in top 1/3 of overnight range: strong bull lean
                   "Overnight Range Compressed",  # tight overnight range = breakout pending
                   "Overnight Range Expanded",    # wide overnight range = exhaustion / chop lean
                   "NQ Bull Divergence",          # NQ outperforming ES = tech risk-on
                   "NQ Bear Divergence",          # NQ lagging ES = distribution / risk-off
                   "ES Pre-Market Momentum Bull", # ES rising last 30 min pre-open
                   "ES Pre-Market Momentum Bear"], # ES falling last 30 min pre-open
    "Position":   ["52w Range Upper Half",       # above midpoint of 52w range = trend context
                   "52w Range Top 20%",           # near yearly highs = momentum continuation
                   "Above BB Mid",               # above 20d BB midline = short-term bull context
                   "Above Prior Day High",        # current close > PDH = trend continuation / breakout
                   "Above Pivot",                 # above classic pivot = session bull lean
                   "Above 5d High"],              # broke above prior 5-bar high = weekly breakout
}

# Signal tier classification — determines which signals are included in Core SSR vs Live-Adj SSR.
#
#   core    — computed from completed daily bars (SPX/VIX/sectors); historically backtestable
#   session — depends on the current-session open price (today's gap); not available prior to open
#   live    — requires real-time or intraday feeds (PCR, macro rates, A/D, overnight ES)
#             these cannot be reconstructed historically in the day backtest
#
# Core SSR = score from core signals only → directly comparable to backtest accuracy numbers
# Live-Adj SSR = Core + session/live overlay → richer but only partially validated
SIGNAL_TIERS = {
    # ── core (28 signals) — backtestable from closed daily bars ──────────────
    "Above 20 SMA":           "core",
    "Above 50 SMA":           "core",
    "Above 200 SMA":          "core",
    "20 SMA > 50 SMA":        "core",
    "Higher Close (1d)":      "core",
    "Higher Close (5d)":      "core",
    "RSI Above 50":           "core",
    "MACD Bullish":           "core",
    "RSI Strong Trend":       "core",
    "VIX Below 20":           "core",
    "VIX Falling":            "core",
    "ATR Contracting":        "core",
    "VIX Below 15":           "core",
    "VIX 3d Relief":          "core",
    "VIX 1d Down":            "core",
    "VIX No Spike":           "core",
    "Volume Above Average":   "core",
    "Sector Breadth ≥ 50%":   "core",
    "Sector Breadth ≥ 70%":   "core",
    "Sector Breadth ≥ 85%":   "core",
    "Stoch Bullish":          "core",
    "RSI Trend Zone":         "core",
    "52w Range Upper Half":   "core",
    "52w Range Top 20%":      "core",
    "Above BB Mid":           "core",
    "Above Prior Day High":   "core",
    "Above Pivot":            "core",
    "Above 5d High":          "core",
    "Gap Up Day":             "core",     # large positive gap, computable from daily OHLC Open
    # ── session (1 signal) — valid only after today's open price is known ────
    "Gap/ATR Normal":         "session",
    # ── live (7 signals) — real-time feeds; not available in day backtest ────
    "A/D Line Positive":      "live",
    "Put/Call Fear Premium":  "live",
    "Put/Call Fear Abating":  "live",
    "Yield Curve Positive":   "live",
    "Credit Spread Calm":     "live",
    "Above Overnight Midpoint":      "live",
    "Overnight Upper Third":         "live",
    "Overnight Range Compressed":    "live",
    "Overnight Range Expanded":      "live",
    "NQ Bull Divergence":            "live",
    "NQ Bear Divergence":            "live",
    "ES Pre-Market Momentum Bull":   "live",
    "ES Pre-Market Momentum Bear":   "live",
}

# US Federal Holidays (market closed) — 2025 and 2026
# Source: NYSE holiday schedule
US_MARKET_HOLIDAYS = {
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}

BIAS_COLOR = {"bull": "🟢", "bear": "🔴", "chop": "⚪", "neutral": "⚪"}

# ─────────────────────────────────────────────────────────────────────────────
# NEWS & ECONOMIC CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

# Optional Alpha Vantage key for scored news sentiment (free at alphavantage.co)
try:
    _AV_KEY = st.secrets.get("AV_KEY", "")
except Exception:
    import os; _AV_KEY = os.environ.get("AV_KEY", "")

# Optional GNews API key — free tier: 100 req/day, real-time, no delay
# Sign up at https://gnews.io — set GNEWS_KEY in st.secrets or env
# Used for targeted geopolitical keyword search (Iran, Hormuz, oil, war)
try:
    _GNEWS_KEY = st.secrets.get("GNEWS_KEY", "")
except Exception:
    import os; _GNEWS_KEY = os.environ.get("GNEWS_KEY", "")

# ── 2026 Economic Calendar (FOMC/CPI/NFP/PPI/GDP) ────────────────────────────
# Source: Federal Reserve, BLS, BEA — all published months in advance
# format: (YYYY-MM-DD, label, type, impact)
_ECON_CAL = [
    # FOMC Rate Decisions (announcement is second day of 2-day meeting)
    ("2026-01-28","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-03-18","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-04-29","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-06-10","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-07-29","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-09-16","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-11-04","FOMC Rate Decision","FOMC","HIGH"),
    ("2026-12-16","FOMC Rate Decision","FOMC","HIGH"),
    # CPI — Consumer Price Index (BLS, ~2nd Wed each month)
    ("2026-01-14","CPI Inflation Report","CPI","HIGH"),
    ("2026-02-11","CPI Inflation Report","CPI","HIGH"),
    ("2026-03-11","CPI Inflation Report","CPI","HIGH"),
    ("2026-04-10","CPI Inflation Report","CPI","HIGH"),
    ("2026-05-13","CPI Inflation Report","CPI","HIGH"),
    ("2026-06-10","CPI Inflation Report","CPI","HIGH"),
    ("2026-07-15","CPI Inflation Report","CPI","HIGH"),
    ("2026-08-12","CPI Inflation Report","CPI","HIGH"),
    ("2026-09-10","CPI Inflation Report","CPI","HIGH"),
    ("2026-10-14","CPI Inflation Report","CPI","HIGH"),
    ("2026-11-12","CPI Inflation Report","CPI","HIGH"),
    ("2026-12-10","CPI Inflation Report","CPI","HIGH"),
    # NFP — Non-Farm Payrolls (BLS, first Friday of month)
    ("2026-01-09","NFP Jobs Report","NFP","HIGH"),
    ("2026-02-06","NFP Jobs Report","NFP","HIGH"),
    ("2026-03-06","NFP Jobs Report","NFP","HIGH"),
    ("2026-04-03","NFP Jobs Report","NFP","HIGH"),
    ("2026-05-01","NFP Jobs Report","NFP","HIGH"),
    ("2026-06-05","NFP Jobs Report","NFP","HIGH"),
    ("2026-07-10","NFP Jobs Report","NFP","HIGH"),
    ("2026-08-07","NFP Jobs Report","NFP","HIGH"),
    ("2026-09-04","NFP Jobs Report","NFP","HIGH"),
    ("2026-10-02","NFP Jobs Report","NFP","HIGH"),
    ("2026-11-06","NFP Jobs Report","NFP","HIGH"),
    ("2026-12-04","NFP Jobs Report","NFP","HIGH"),
    # PPI — Producer Price Index (BLS, day after CPI)
    ("2026-01-15","PPI Report","PPI","MED"),
    ("2026-02-12","PPI Report","PPI","MED"),
    ("2026-03-12","PPI Report","PPI","MED"),
    ("2026-04-11","PPI Report","PPI","MED"),
    ("2026-05-14","PPI Report","PPI","MED"),
    ("2026-06-11","PPI Report","PPI","MED"),
    # GDP Advance Estimate (quarterly: Jan/Apr/Jul/Oct)
    ("2026-01-28","GDP Advance Estimate","GDP","MED"),
    ("2026-04-29","GDP Advance Estimate","GDP","MED"),
    ("2026-07-29","GDP Advance Estimate","GDP","MED"),
    ("2026-10-28","GDP Advance Estimate","GDP","MED"),
]

_EVENT_ICON  = {"FOMC":"🏦","CPI":"📊","NFP":"👷","PPI":"🏭","GDP":"📈"}
_EVENT_COLOR = {"HIGH":"#f87171","MED":"#f59e0b"}

# ── Causal-Chain News Impact Taxonomy ────────────────────────────────────────
# Each entry: (phrase_list, category, base_weight, vix_regime, spx_dir, causal_note)
# vix_regime: "any" | "high_vix" (>25) | "low_vix" (<18)
# spx_dir:    "bull" | "bear" | "context" (regime-dependent — scored at runtime)
#
# WEIGHT RATIONALE (1.0 = moderate, 2.0 = significant, 3.0+ = market-moving):
#   Oil supply disruption → oil spike → CPI → Fed hawkish → sell equity       = 3.5
#   Tariff announcement   → supply cost + retaliation → earnings hit           = 3.5
#   Bank failure          → systemic risk + credit freeze                       = 4.0
#   Fed rate cut          → risk premium compressed + multiple expansion        = 3.0
#   Geopolitical war      → safe-haven flow + oil/commodity spike               = 3.0
_NEWS_IMPACTS = [
    # ── OIL / ENERGY (causal chain: oil → CPI → Fed → equity) ───────────────
    # Hormuz closure is a 4.0 event: ~20% of global oil supply at risk immediately.
    (["strait of hormuz","hormuz block","hormuz clos","hormuz shut","hormuz seized",
      "iran closes hormuz","iran blocks hormuz","iran threatens hormuz",
      "hormuz tanker","iran oil tanker","iran seize tanker",
      "oil supply disruption","oil tanker attack","iran oil block",
      "opec cut","opec produc cut","oil embargo","oil facility attack",
      "saudi oil","pipeline attack","iran oil"],
     "OIL_SUPPLY_SHOCK", 4.0, "any", "bear",
     "Hormuz/oil supply shock → ~20% global supply risk → oil spike → CPI surge → Fed hawkish → bearish"),

    # Supply-surplus driven drop: OPEC hike, glut, oversupply → inflation relief → BULLISH
    (["oil drops","oil falls","oil tumble","oil plunge","oil collaps","crude falls",
      "crude drops","crude plunge","oil prices fall","opec increas output",
      "opec produc hike","oil glut","oil oversupply","oil supply surge",
      "oil below","wti drops","brent drops"],
     "OIL_DROP", 2.5, "any", "bull",
     "Supply-surplus oil drop → deflation relief → rate cut room → multiple expansion → bullish"),

    # Demand-driven drop: recession signals, China slowdown, global growth collapse → BEARISH
    # (different from OIL_DROP which is supply-surplus; demand destruction = recession)
    (["oil demand falls","oil demand drop","oil demand weak","demand-driven oil",
      "china oil demand weak","china demand slows","oil demand destruction",
      "global oil demand slows","recession oil demand","weak oil demand",
      "oil demand concern","demand recession","oil demand contract"],
     "OIL_DEMAND_DROP", 2.5, "any", "bear",
     "Demand-driven oil drop → recession/growth fear → earnings revisions down → bearish"),

    # Demand-driven surge: global recovery, China reopening → risk-on BULLISH
    (["oil demand surge","strong oil demand","china oil demand strong","china demand recover",
      "oil demand recover","global oil demand rise","oil demand boom","demand-driven oil rally"],
     "OIL_DEMAND_SURGE", 1.5, "any", "bull",
     "Demand-driven oil strength → global growth recovery + risk-on → bullish"),

    (["oil spike","oil surges","oil jumps","crude surges","oil hits","crude jumps",
      "oil prices rise","oil prices surge","wti rises","brent rises","oil rally"],
     "OIL_SPIKE", 2.5, "any", "bear",
     "Oil spike → inflation re-acceleration → Fed stays restrictive → bearish"),

    # ── IRAN / MIDDLE EAST ───────────────────────────────────────────────────
    # US direct military engagement with Iran is a step-change escalation:
    # oil route closure + carrier group + risk-off across all asset classes.
    (["us troops iran","us boots iran","us military iran","us ground troops iran",
      "us invad iran","us strikes iran","us attack iran","us bombs iran",
      "american troops iran","boots on the ground iran","us iran war",
      "us iran conflict","us deploys iran","pentagon iran","us forces iran",
      "troop deployment iran","troops to iran","deploy troops iran",
      "military option iran","us deployment iran","rubio iran",
      "signals deployment","troop deployments coming","ground troops",
      "boots on ground","send troops","sending troops","deploy forces",
      "carrier strike group iran","fifth fleet iran","centcom iran",
      "b-52 iran","f-35 iran","b-2 iran","stealth bomber iran",
      "warship iran","destroyer iran","guided missile iran",
      "us airstrike iran","airstrike iran","airstrikes iran",
      "us air force iran","strike package iran","iran strike order",
      "authorization to strike","authorized strike iran",
      "military escalat iran","us escalat iran","reagan iran",
      "uss iran","strike imminent","attack imminent iran"],
     "US_IRAN_WAR", 4.5, "any", "bear",
     "US direct military engagement with Iran → Hormuz closure + oil spike + war premium → sharply bearish"),

    (["iran attack","iran missile","iran nuclear","israel iran","iran war",
      "iran strikes","iran retaliat","iran threaten","iran sanction new",
      "iran oil block","hezbollah","hamas attack","iran-backed",
      "israel strikes iran","israel bombs iran","idf iran",
      "idf evacuation","evacuation tehran","strike tehran","attack tehran",
      "tehran strike","tehran evacuat","idf issues","iranian drone",
      "iranian ballistic","iranian hypersonic","iran launch",
      "iran closes strait","strait closed","hormuz closed",
      "tanker seized","iran seizes","iran fires","iran shoots down",
      "revolutionary guard","irgc attack","irgc seize","irgc fires",
      "iran nuclear site","fordow","natanz attack","nuclear facility struck"],
     "IRAN_ESCALATION", 3.5, "any", "bear",
     "Iran/Israel conflict → Hormuz risk + oil spike + safe-haven flows → bearish"),

    (["iran deal","iran nuclear deal","iran sanction lift","iran ceasefire",
      "iran agreement","iran us talks","iran diplomacy","iran negotiat",
      "iran peace","iran nuclear framework","us iran diplomacy",
      "iran withdraw","iran comply","iran backs down","iran stands down",
      "iran agrees","iran accepts","iran suspends","iran pauses",
      "hormuz open","strait reopened","tanker released","iran releases",
      "iran halts","iran stops","iran ceases","iran freeze",
      "direct talks iran","negotiations iran","iran compromise"],
     "IRAN_DEESCALATION", 3.0, "any", "bull",
     "Iran de-escalation → Hormuz open + oil supply relief + war premium unwind → bullish"),

    # ── BROADER GEOPOLITICAL ─────────────────────────────────────────────────
    (["russia attack","russia missile","russia ukraine escal","russia nato",
      "nuclear threat","nuclear strike","russia invad"],
     "RUSSIA_GEO", 2.5, "any", "bear",
     "Russia escalation → energy supply risk + European recession fears → bearish"),

    (["china taiwan","taiwan strait","taiwan tension","china threaten taiwan",
      "china military taiwan","pla taiwan","china invad taiwan"],
     "CHINA_TAIWAN", 3.0, "any", "bear",
     "Taiwan tension → semiconductor supply chain collapse + tech selloff → sharply bearish"),

    # ── PAKISTAN MEDIATION / MIDDLE EAST PEACE ───────────────────────────────
    # Pakistan is currently mediating Iran-US/Israel talks. Progress here is bullish:
    # war premium unwind + oil relief + risk-on. Treat mediator breakthrough = de-escalation.
    (["pakistan mediat","islamabad mediat","pakistan broker","pakistan peace talks",
      "pakistan facilitate","pakistan shuttle","islamabad summit","pakistan diplomacy iran",
      "pakistan iran us talks","pakistan help iran","pakistan peace deal",
      "islamabad peace","pakistan negotiat ceasefire"],
     "PAKISTAN_MEDIATION_PROGRESS", 2.5, "any", "bull",
     "Pakistan brokering Iran-US/Israel peace → war premium unwind + oil relief → bullish"),

    (["ceasefire","peace deal","de-escalat","peace agreement","hostage deal",
      "conflict ends","war ends","truce signed","peace talks succeed",
      "iran ceasefire","iran truce","middle east ceasefire","iran deal reached"],
     "GEO_DEESCALATION", 2.5, "any", "bull",
     "Conflict resolution → risk-on + commodity price relief → bullish"),

    # ── TRADE / TARIFFS ──────────────────────────────────────────────────────
    (["new tariff","tariff hike","tariff increas","tariff imposed","tariff announc",
      "trade war escal","trade war widen","tariff expand","trump tariff",
      "china tariff","reciprocal tariff","tariffs on","tariff threat",
      "section 301","section 232","import tax","import duty",
      "25% tariff","50% tariff","100% tariff","145% tariff",
      "tariff china","tariff eu","tariff canada","tariff mexico",
      "trump imposes","trump slaps tariff","trump hits","trump raises tariff",
      "universal tariff","blanket tariff","broad tariff","sweeping tariff"],
     "TARIFF_BEARISH", 3.5, "any", "bear",
     "Tariffs → supply chain costs + retaliation risk + margin compression → bearish"),

    (["tariff pause","tariff delay","tariff exempt","tariff cut","tariff reduc",
      "trade deal","trade agreement","trade truce","tariff remov","tariff drop",
      "tariff lift","trade war end","trade war resolv","tariff rollback",
      "tariff suspend","tariff waiver","tariff relief","tariff reprieve",
      "trade ceasefire","trade framework","trade pact","deal reached china",
      "deal reached eu","trump drops tariff","trump cuts tariff",
      "trump pauses tariff","90-day pause","tariff 90 day"],
     "TARIFF_BULLISH", 3.5, "any", "bull",
     "Tariff relief → supply chain normalization + margin recovery + risk-on → bullish"),

    # ── TRUMP EXECUTIVE / POLICY ACTIONS ─────────────────────────────────────
    (["trump signs","trump order","executive order","trump executive",
      "trump fires","trump dismisses","trump removes","trump ousts",
      "doge cuts","doge slash","spending cuts","federal layoffs","mass federal",
      "trump budget cut","government spending cut","trump slash",
      "trump threatens","trump warns markets","trump warns economy",
      "trump demands fed","trump calls for rate","trump attacks fed",
      "trump fires powell","trump replace powell","trump fed chair",
      "trump nationaliz","trump seize","trump invoke","trump emergency",
      "trump sanctions","trump ban","trump restrict","trump block"],
     "TRUMP_POLICY_BEAR", 3.0, "any", "bear",
     "Trump disruptive policy action → regulatory/fiscal uncertainty + market shock → bearish"),

    (["trump deal","trump agreement","trump signs deal","trump trade win",
      "trump deregulat","trump cuts regulation","trump tax cut","trump stimulus",
      "trump infrastructure","trump announces deal","trump reaches deal",
      "trump ceasefire","trump peace","trump withdraws threat","trump backs down",
      "trump lifts ban","trump removes restriction","market-friendly trump",
      "trump positive","trump economy strong","trump markets"],
     "TRUMP_POLICY_BULL", 2.5, "any", "bull",
     "Trump market-positive action → deregulation/tax/deal catalyst → bullish"),

    # ── TRUMP WAR / PEACE NEGOTIATIONS ───────────────────────────────────────
    # Trump-brokered peace deals are high-impact bullish: war premium unwind,
    # commodity relief, risk-on rotation. Failures/escalations are bearish.
    (["trump ukraine deal","trump russia deal","trump peace deal","trump brokered",
      "trump negotiat","trump mediat","trump ceasefire ukraine","trump end war",
      "trump stops war","trump halts war","trump russia ukraine","trump putin deal",
      "trump zelenskyy deal","trump zelensky deal","trump ukraine peace",
      "ukraine ceasefire","russia ceasefire","russia ukraine deal",
      "russia ukraine peace","russia ukraine truce","russia ukraine agreement",
      "russia ukraine negotiat","ukraine truce","war ends ukraine",
      "trump hostage deal","trump hostage negotiat","trump hamas deal",
      "trump gaza deal","trump gaza ceasefire","trump middle east deal",
      "trump iran deal","trump iran negotiat","trump iran peace",
      "trump calls putin","trump meets putin","trump putin call","trump xi call",
      "trump calls xi","trump meets xi","trump summit","trump meeting with",
      "trump negotiating","trump brokering","trump broker peace",
      "trump peace summit","trump peace talks","trump-brokered ceasefire",
      "trump framework","trump proposes peace","trump peace plan",
      "peace deal reached","ceasefire reached","truce reached","agreement reached",
      "negotiations succeed","deal imminent","breakthrough talks"],
     "TRUMP_PEACE_DEAL", 3.5, "any", "bull",
     "Trump peace deal/ceasefire → war premium unwind + commodity relief + risk-on → bullish"),

    (["trump war","trump escalat","trump threatens war","trump military action",
      "trump deploys troops","trump sends troops","trump airstrikes",
      "trump bombs","trump attack order","trump strikes","trump threatens china",
      "trump threatens russia","trump threatens iran","trump threatens north korea",
      "trump threatens eu","trump threatens nato","trump nato withdraw",
      "trump pulls out nato","trump nato exit","trump pulls troops",
      "peace talks fail","negotiations collapse","ceasefire breaks","truce violated",
      "deal collapses","talks break down","negotiations fail","deal falls apart",
      "ukraine rejected","russia rejected","ceasefire rejected","trump rejected"],
     "TRUMP_WAR_ESCALATION", 3.5, "any", "bear",
     "Trump war escalation/failed negotiations → geopolitical risk premium + flight to safety → bearish"),

    # ── FED / MONETARY POLICY ────────────────────────────────────────────────
    (["rate cut","fed cuts","fed lower","dovish","interest rate cut",
      "powell dovish","fed pivot","easing cycle","quantitative easing",
      "qe restart","fed ease","accommodative","cut rates"],
     "FED_DOVISH", 3.0, "any", "bull",
     "Fed dovish → lower discount rate + risk premium shrinks + liquidity → bullish"),

    (["rate hike","fed hikes","fed rais","hawkish","interest rate hike",
      "powell hawkish","tighten","quantitative tighten","qt",
      "higher for longer","rates stay high","no cut","delay cut",
      "hold rates"],
     "FED_HAWKISH", 3.0, "any", "bear",
     "Fed hawkish → higher discount rate + multiple compression + credit cost → bearish"),

    # ── INFLATION DATA ───────────────────────────────────────────────────────
    (["cpi hot","cpi above","inflation accelerat","cpi beat","inflation surges",
      "hotter than expect","core cpi rise","inflation uptick","inflation sticky",
      "pce hot","pce above","pce beat"],
     "CPI_HOT", 3.0, "any", "bear",
     "Hot CPI/PCE → Fed can't cut → discount rate stays elevated → bearish"),

    (["cpi cool","cpi below","inflation slow","cpi miss","cpi drop",
      "cooler than expect","core cpi fall","disinflation","deflation",
      "pce cool","pce below","pce miss"],
     "CPI_COOL", 3.0, "any", "bull",
     "Cool CPI/PCE → rate cut path opens → equity multiples expand → bullish"),

    # ── JOBS / LABOR (VIX-regime dependent) ─────────────────────────────────
    # High VIX = recession fear dominant: strong jobs = relief = BULL
    # Low/normal VIX = rate cycle dominant: strong jobs = Fed won't cut = BEAR
    (["jobs beat","nfp beat","payroll beat","strong jobs","jobs surge",
      "unemployment fall","jobless claims low","labor market strong",
      "adp beat","private payroll beat"],
     "JOBS_STRONG", 2.0, "any", "context",
     "Strong jobs: BULL when VIX>22 (recession relief), BEAR when VIX<22 (Fed won't cut)"),

    (["jobs miss","nfp miss","payroll miss","weak jobs","jobs disappoint",
      "unemployment rises","layoffs surge","jobless claims high",
      "jobs below","labor market weak","mass layoff","job cuts"],
     "JOBS_WEAK", 2.0, "any", "context",
     "Weak jobs: BEAR when VIX>22 (confirms recession), BULL when VIX<22 (rate cut hope)"),

    # ── BANKING / SYSTEMIC RISK ──────────────────────────────────────────────
    (["bank failure","bank collaps","bank run","credit crunch","bank crisis",
      "banking stress","fdic seizes","bank default","bank bailout needed",
      "bank insolvency","contagion","systemic risk"],
     "BANK_CRISIS", 4.0, "any", "bear",
     "Bank failure → systemic credit freeze + contagion risk → sharply bearish"),

    (["credit downgrade","us downgrade","sovereign downgrade","moody downgrade",
      "fitch downgrade","sp downgrade","debt downgrade"],
     "CREDIT_DOWNGRADE", 2.5, "any", "bear",
     "Credit downgrade → risk premium rises + dollar falls + rates spike → bearish"),

    # ── FISCAL / POLITICAL ───────────────────────────────────────────────────
    (["government shutdown","shutdown begins","congress fail","debt ceiling",
      "default risk","us default","fiscal cliff","budget impasse"],
     "FISCAL_CRISIS", 2.5, "any", "bear",
     "Fiscal crisis → policy uncertainty + credit risk premium → bearish"),

    (["debt ceiling raised","shutdown avert","budget deal","fiscal deal",
      "congress passes","stimulus package","spending bill","bipartisan"],
     "FISCAL_RESOLUTION", 2.0, "any", "bull",
     "Fiscal resolution → uncertainty removed + policy clarity → bullish"),

    # ── TREASURY YIELDS / DOLLAR ─────────────────────────────────────────────
    (["yield spike","yields surge","yields jump","10-year yield","treasury yield spike",
      "bond selloff","yield curve invert","rates spike","10yr spike"],
     "YIELD_SPIKE", 2.0, "any", "bear",
     "Yield spike → discount rate rises → equity valuation compressed → bearish"),

    (["yield falls","yields drop","yields decline","bond rally","yield drops",
      "treasury rally","rates fall","bond prices rise"],
     "YIELD_DROP", 1.5, "any", "bull",
     "Yield drop → lower discount rate → equity multiple expansion → bullish"),

    # ── EARNINGS ─────────────────────────────────────────────────────────────
    (["earnings beat","beats estimates","beats expectations","record earnings",
      "profit beats","revenue beats","guidance raised","raises guidance",
      "eps beat","above consensus"],
     "EARNINGS_BEAT", 1.5, "any", "bull",
     "Strong earnings → forward PE expansion + sector rotation → bullish"),

    (["earnings miss","misses estimates","misses expectations","profit warning",
      "guidance cut","lowers guidance","revenue miss","below estimates",
      "eps miss","below consensus","profit warning"],
     "EARNINGS_MISS", 1.5, "any", "bear",
     "Earnings miss → forward PE contraction + sector selloff → bearish"),

    # ── GENERIC MACRO (lowest weight — surface signals) ──────────────────────
    (["recession","contraction","gdp shrink","economic slowdown","stagflat"],
     "RECESSION_FEAR", 2.0, "any", "bear",
     "Recession signals → earnings revision lower + risk-off → bearish"),

    (["gdp beat","gdp surpass","economic growth","gdp above","strong growth",
      "expansion","soft landing"],
     "GROWTH_STRONG", 1.5, "any", "bull",
     "Strong growth → earnings upgrade cycle + risk-on → bullish"),
]

# Category importance multiplier — applied on top of base_weight
# Certain categories need extra amplification due to SPX correlation magnitude
_CATEGORY_AMP = {
    "BANK_CRISIS":       1.5,   # systemic events have outsized tail risk
    "OIL_SUPPLY_SHOCK":  1.2,   # Hormuz/Iran oil events are reliably market-moving
    "CHINA_TAIWAN":      1.2,   # semiconductor supply chain = instant tech selloff
    "TARIFF_BEARISH":    1.2,   # market has been hypersensitive to tariff news in 2025-26
    "TARIFF_BULLISH":    1.2,
    "TRUMP_POLICY_BEAR":    1.2,   # Trump policy actions are high-volatility market movers
    "TRUMP_POLICY_BULL":    1.1,
    "TRUMP_PEACE_DEAL":     1.3,   # war premium unwind = instant multi-asset risk-on
    "TRUMP_WAR_ESCALATION": 1.3,   # geopolitical shock = instant flight to safety
    "FED_DOVISH":        1.1,
    "FED_HAWKISH":       1.1,
}


def _keyword_impact(text, vix=0.0):
    """
    Analyze a headline using the causal-chain taxonomy.
    Returns (score, category, effective_weight, causal_note)
      score = -1..+1 (negative = bearish, positive = bullish)
      category = taxonomy key e.g. "OIL_SUPPLY_SHOCK"
      effective_weight = base_weight × category_amp (used for composite weighting)
      causal_note = human-readable causal chain explanation
    """
    t = text.lower()
    best_score  = 0.0
    best_cat    = "GENERIC"
    best_wt     = 1.0
    best_note   = ""
    total_bull  = 0.0
    total_bear  = 0.0

    for phrases, cat, base_wt, regime, direction, note in _NEWS_IMPACTS:
        matched = any(ph in t for ph in phrases)
        if not matched:
            continue

        amp = _CATEGORY_AMP.get(cat, 1.0)
        eff_wt = base_wt * amp

        # Resolve "context" direction using VIX regime
        if direction == "context":
            _vix_high = vix > 22 if vix > 0 else False
            if "JOBS_STRONG" in cat:
                direction = "bull" if _vix_high else "bear"
            elif "JOBS_WEAK" in cat:
                direction = "bear" if _vix_high else "bull"
            else:
                direction = "neutral"

        if direction == "bull":
            total_bull += eff_wt
        elif direction == "bear":
            total_bear += eff_wt

        # Track highest-weight match as the "headline" impact
        if eff_wt > best_wt or best_cat == "GENERIC":
            best_wt   = eff_wt
            best_cat  = cat
            best_note = note
            best_score = 1.0 if direction == "bull" else (-1.0 if direction == "bear" else 0.0)

    # Normalized score: weighted sentiment ratio
    _total = total_bull + total_bear
    if _total > 0:
        final_score = round((total_bull - total_bear) / _total, 3)
    else:
        # Fall back to simple surface-level keywords for unrecognized headlines
        _bear_surf = sum(w for kw, w in [
            ("tariff",1.5),("war",1.5),("attack",1.5),("sanction",1.2),
            ("default",1.5),("recession",1.5),("miss",0.8),("weak",0.8),
            ("hawkish",1.5),("decline",0.8),("shutdown",1.2),("downgrade",1.5),
        ] if kw in t)
        _bull_surf = sum(w for kw, w in [
            ("rate cut",2.0),("dovish",1.5),("deal",1.2),("beat",1.0),
            ("stimulus",1.5),("rally",1.0),("recovery",1.2),("strong",0.8),
            ("ceasefire",1.5),("truce",1.5),("boost",0.8),
        ] if kw in t)
        _st = _bear_surf + _bull_surf
        final_score = round((_bull_surf - _bear_surf) / _st, 3) if _st > 0 else 0.0
        best_cat    = "GENERIC"
        best_wt     = 1.0
        best_note   = ""

    return final_score, best_cat, best_wt, best_note


def _keyword_score(text, vix=0.0):
    """Backward-compat wrapper — returns score only."""
    return _keyword_impact(text, vix=vix)[0]

def get_todays_events(lookahead_days=4):
    """Return economic events for today + next N trading days."""
    today = date.today()
    window = {(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(lookahead_days + 1)}
    return [e for e in _ECON_CAL if e[0] in window]

def get_event_types_today():
    """Return set of event types happening today."""
    today_str = date.today().strftime("%Y-%m-%d")
    return {e[2] for e in _ECON_CAL if e[0] == today_str}

@st.cache_data(ttl=90)
def load_news(vix_now=0.0):
    """
    Fetch real-time market news with causal-chain sentiment scoring.
    Each article is scored using _NEWS_IMPACTS taxonomy with domain weights.
    Composite score is weighted by article impact weight (not just recency).
    TTL=90s — refreshes ~every 1.5 min; use the manual Refresh button for instant updates.

    Priority:
      1. Financial Juice RSS   (breaking market news, real-time)
      2. Forexlive RSS         (macro + geopolitical, fast)
      3. Reuters World News RSS (geopolitical depth)
      4. CNBC Markets RSS      (broad market coverage)
      5. Al Jazeera RSS        (Middle East / geopolitical)
      6. Alpha Vantage News Sentiment (if AV_KEY set — pre-scored)
      7. yfinance headlines (last-resort fallback)
    Returns: {articles: [...], composite_score: float, label: str,
              top_impact: {category, note, weight}, fetched_at: str}
    """
    articles = []
    _fetched_at = datetime.now(EST).strftime("%I:%M:%S %p EST")

    def _parse_rss(url, source_name, max_items=12):
        items = []
        try:
            import ssl as _ssl
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=6) as r:
                    raw = r.read().decode("utf-8", errors="ignore")
            except _ssl.SSLError:
                # Fallback: some feeds have cert chain issues on certain hosts
                _ctx = _ssl.create_default_context()
                _ctx.check_hostname = False
                _ctx.verify_mode    = _ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=6, context=_ctx) as r:
                    raw = r.read().decode("utf-8", errors="ignore")
            root = _ET.fromstring(raw)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall(".//item") or root.findall(".//atom:entry", ns)
            for entry in entries[:max_items]:
                title_el = entry.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                if not title: continue
                pub_el  = entry.find("pubDate") or entry.find("published") or entry.find("updated")
                _pub_raw = (pub_el.text or "").strip() if pub_el is not None else ""
                # Normalize to sortable "YYYY-MM-DD HH:MM" regardless of RSS/Atom/ISO source format.
                # RSS pubDate: "Sun, 29 Mar 2026 18:45:00 GMT"  → use email.utils
                # ISO Atom:    "2026-03-29T18:45:00Z"           → strip T/Z
                pub = ""
                if _pub_raw:
                    try:
                        import email.utils as _eu
                        _pt = _eu.parsedate_to_datetime(_pub_raw)
                        pub = _pt.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        # Fallback: strip T and Z from ISO format
                        pub = _pub_raw[:16].replace("T", " ").replace("Z", "")
                score, cat, wt, note = _keyword_impact(title, vix=vix_now)
                label = "🟢 Bullish" if score > 0.1 else ("🔴 Bearish" if score < -0.1 else "⚪ Neutral")
                items.append({"title": title, "source": source_name,
                               "time": pub, "score": score, "label": label,
                               "category": cat, "impact_weight": wt, "note": note})
        except Exception:
            pass
        return items

    # ── RSS feed stack — ordered by geopolitical speed ───────────────────────
    # ForexLive: fastest on Iran/US military/macro macro headlines
    articles += _parse_rss("https://www.forexlive.com/feed/news",               "ForexLive",    max_items=15)
    # FinancialJuice: real-time breaking market
    articles += _parse_rss("https://www.financialjuice.com/feed.aspx?q=market", "FinancialJuice",max_items=15)
    # Reuters Politics: fastest on Trump executive orders, White House policy actions
    articles += _parse_rss("https://feeds.reuters.com/reuters/politicsNews",    "Reuters-Politics", max_items=15)
    # Reuters Business/Finance: macro + corporate + tariff headlines
    articles += _parse_rss("https://feeds.reuters.com/reuters/businessNews",    "Reuters-Biz",  max_items=12)
    # Politico: White House policy, executive orders, Trump announcements
    articles += _parse_rss("https://www.politico.com/rss/politicopicks.xml",    "Politico",     max_items=12)
    # BBC Middle East: dedicated Iran/Israel/Houthi/Gulf feed (verified)
    articles += _parse_rss("https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "BBC-ME", max_items=12)
    # Jerusalem Post: IDF/Iran/Israel breaking news — fastest on IDF/Hormuz events (verified)
    articles += _parse_rss("https://www.jpost.com/Rss/RssFeedsHeadlines.aspx",  "JPost",        max_items=12)
    # BBC World: broader geopolitical context
    articles += _parse_rss("https://feeds.bbci.co.uk/news/world/rss.xml",       "BBC",          max_items=10)
    # Al Jazeera: Middle East / Hormuz regional depth (verified)
    articles += _parse_rss("https://www.aljazeera.com/xml/rss/all.xml",         "AlJazeera",    max_items=10)
    # OilPrice.com: fastest dedicated energy/oil/Hormuz feed (verified)
    articles += _parse_rss("https://oilprice.com/rss/main",                     "OilPrice",     max_items=10)
    # CNBC: broad market coverage
    articles += _parse_rss(
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        "CNBC", max_items=10)
    # MarketWatch: macro/market headlines
    articles += _parse_rss("https://www.marketwatch.com/rss/topstories",        "MarketWatch",  max_items=8)
    # AP Top/World: wire-speed — DNS issues on some hosts but works on Streamlit Cloud
    articles += _parse_rss("https://feeds.apnews.com/rss/topnews",              "AP",           max_items=10)
    articles += _parse_rss("https://feeds.apnews.com/rss/worldnews",            "AP-World",     max_items=10)
    # Deduplicate by title prefix to avoid same headline from multiple feeds
    _seen = set()
    _deduped = []
    for _a in articles:
        _key = _a["title"][:60].lower().strip()
        if _key not in _seen:
            _seen.add(_key)
            _deduped.append(_a)
    articles = _deduped

    # ── GNews API: targeted geopolitical keyword search (free 100/day, real-time) ──
    # Set GNEWS_KEY in st.secrets. Queries Iran/Hormuz/oil/war even when RSS feeds miss it.
    if _GNEWS_KEY:
        try:
            _gq  = "Iran OR Hormuz OR \"US troops\" OR \"oil prices\" OR OPEC OR Israel"
            _gurl = (f"https://gnews.io/api/v4/search?q={urllib.parse.quote(_gq)}"
                     f"&lang=en&max=10&token={_GNEWS_KEY}")
            with urllib.request.urlopen(_gurl, timeout=8) as _r:
                _gdata = _json.loads(_r.read())
            for _gi in _gdata.get("articles", [])[:10]:
                _gt = _gi.get("title", "")
                _sc, _cat, _wt, _note = _keyword_impact(_gt, vix=vix_now)
                _lbl = "🟢 Bullish" if _sc > 0.1 else ("🔴 Bearish" if _sc < -0.1 else "⚪ Neutral")
                _pub = (_gi.get("publishedAt", "")[:16]).replace("T", " ")
                articles.append({
                    "title": _gt, "source": "GNews",
                    "time": _pub, "score": _sc, "label": _lbl,
                    "category": _cat, "impact_weight": _wt, "note": _note,
                })
        except Exception:
            pass

    if _AV_KEY and len(articles) < 5:
        try:
            url = (f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
                   f"&tickers=SPY,QQQ&topics=financial_markets,economy_macro"
                   f"&sort=LATEST&limit=15&apikey={_AV_KEY}")
            with urllib.request.urlopen(url, timeout=8) as r:
                data = _json.loads(r.read())
            for item in data.get("feed", [])[:10]:
                sc  = float(item.get("overall_sentiment_score", 0))
                ttl = item.get("title","")
                _, cat, wt, note = _keyword_impact(ttl, vix=vix_now)
                lbl = "🟢 Bullish" if sc > 0.15 else ("🔴 Bearish" if sc < -0.15 else "⚪ Neutral")
                articles.append({
                    "title": ttl, "source": item.get("source","AV"),
                    "time":  item.get("time_published","")[:8],
                    "score": round(sc, 3), "label": lbl,
                    "category": cat, "impact_weight": wt, "note": note,
                })
        except Exception:
            pass

    if not articles:
        try:
            for item in (yf.Ticker("^GSPC").news or [])[:10]:
                title = item.get("title","")
                sc, cat, wt, note = _keyword_impact(title, vix=vix_now)
                ts  = item.get("providerPublishTime", None)
                # Guard against Unix epoch timestamp (0 or missing) — display "N/A" not "1970"
                _time_str = (datetime.fromtimestamp(ts).strftime("%I:%M %p")
                             if (ts and ts > 86400) else "N/A")
                lbl = "🟢 Bullish" if sc > 0.1 else ("🔴 Bearish" if sc < -0.1 else "⚪ Neutral")
                articles.append({
                    "title": title, "source": item.get("publisher","yf"),
                    "time":  _time_str,
                    "score": sc, "label": lbl,
                    "category": cat, "impact_weight": wt, "note": note,
                })
        except Exception:
            pass

    if not articles:
        return {"articles": [], "composite_score": 0.0, "label": "⚪ Unavailable",
                "bull_pct": 0, "bear_pct": 0, "top_impact": None, "fetched_at": _fetched_at}

    # ── Final dedupe pass across ALL feeds (RSS + GNews + AV + yf) ───────────
    # First pass deduped only the RSS block; GNews/AV/fallback could still add duplicates.
    # Use lowercased 60-char title prefix as the dedup key.
    _seen2 = set()
    _deduped2 = []
    for _a in articles:
        _k = _a["title"][:60].lower().strip()
        if _k not in _seen2:
            _seen2.add(_k)
            _deduped2.append(_a)
    articles = _deduped2

    # ── Sort by timestamp before recency weighting so earlier feeds don't ────
    # structurally dominate. Articles with parseable ISO timestamps are sorted
    # newest-first; articles with no timestamp go to the end (lowest recency weight).
    def _ts_sort_key(a):
        t = a.get("time", "")
        try:
            # Accept ISO-ish strings: "2026-03-29 18:45" or "2026-03-29T18:45:00Z"
            return -int(t.replace("T","").replace(":","").replace("-","").replace("Z","").replace(" ","")[:14])
        except Exception:
            return 0   # unknown time → treat as oldest (lowest recency)
    articles.sort(key=_ts_sort_key)

    # ── Composite score: impact-weighted (high-weight articles matter more) ──
    # Recency weight now reflects true time order after the sort above.
    scores   = [a["score"]         for a in articles]
    imp_wts  = [a["impact_weight"] for a in articles]
    rec_wts  = [1.0 / (i + 1)      for i in range(len(scores))]
    comb_wts = [iw * rw for iw, rw in zip(imp_wts, rec_wts)]
    total_w  = sum(comb_wts) or 1.0
    comp     = sum(s * w for s, w in zip(scores, comb_wts)) / total_w

    # Require minimum 5 articles before trusting the composite — fewer than that
    # means a single headline can swing ±0.5 which unfairly shifts SSR ±5 pts.
    # Below threshold, dampen the composite proportionally.
    if len(articles) < 5:
        comp = comp * (len(articles) / 5.0)

    label    = "🟢 Bullish" if comp > 0.10 else ("🔴 Bearish" if comp < -0.10 else "⚪ Neutral")
    # Guard against empty articles list (no feeds returned data)
    if articles:
        bull_pct = int(sum(1 for a in articles if a["score"] > 0.1)  / len(articles) * 100)
        bear_pct = int(sum(1 for a in articles if a["score"] < -0.1) / len(articles) * 100)
        top = max(articles, key=lambda a: a["impact_weight"] * abs(a["score"]) if a["score"] != 0 else 0)
        top_impact = {"category": top["category"], "note": top["note"],
                      "weight": top["impact_weight"], "title": top["title"][:80]} if top["score"] != 0 else None
    else:
        bull_pct   = 0
        bear_pct   = 0
        top_impact = None

    return {
        "articles":        articles[:25],   # show up to 25 with 10+ feeds
        "composite_score": round(comp, 3),
        "label":           label,
        "bull_pct":        bull_pct,
        "bear_pct":        bear_pct,
        "top_impact":      top_impact,
        "fetched_at":      _fetched_at,
    }


def to_ampm(hhmm):
    """Convert '13:30' → '1:30 PM'"""
    if hhmm == "--":
        return "—"
    h, m = map(int, hhmm.split(":"))
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def fetch_data():
    spx = yf.download("^GSPC", period="100d", interval="1d", progress=False, auto_adjust=True)
    vix = yf.download("^VIX",  period="30d",  interval="1d", progress=False, auto_adjust=True)
    sectors = {}
    for t in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
        try:
            sectors[t] = yf.download(t, period="60d", interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[t] = pd.DataFrame()
    old_err = sys.stderr; sys.stderr = io.StringIO()
    try:
        pcr = yf.download("^CPC", period="60d", interval="1d", progress=False, auto_adjust=True)
    except Exception:
        pcr = pd.DataFrame()
    sys.stderr = old_err
    return spx, vix, pcr, sectors


@st.cache_data(ttl=60)
def fetch_live():
    """Live ES futures + SPX — refreshes every 60 seconds."""
    results = {"es_price": None, "es_change": None, "es_pct": None, "es_ts": None,
               "spx_price": None, "spx_change": None, "spx_pct": None, "spx_ts": None}
    def _close_scalar(df, idx=-1):
        """Safely extract a scalar close value handling MultiIndex columns."""
        c = df["Close"] if "Close" in df.columns else df.iloc[:, 0]
        c = c.squeeze()
        if isinstance(c, pd.DataFrame): c = c.iloc[:, 0]
        return float(c.iloc[idx])

    try:
        es_df = yf.download("ES=F", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not es_df.empty:
            # Convert index to EST first — avoids UTC offset confusion with yfinance timestamps
            es_df.index = es_df.index.tz_convert(EST)
            _last_bar_est  = es_df.index[-1]
            _now_est_check = datetime.now(EST)
            _staleness_min = (_now_est_check - _last_bar_est).total_seconds() / 60
            # ES trades 23h/day (closed 5–6 PM ET). Accept bars up to 30 min stale.
            # Reject if last bar date is >1 calendar day behind (catches Friday bars on Sunday).
            _bar_date = _last_bar_est.date()
            _now_date = _now_est_check.date()
            _day_gap  = (_now_date - _bar_date).days
            _is_fresh = _staleness_min <= 30 and _day_gap <= 1
            if not _is_fresh:
                pass  # stale — show "—" rather than wrong price
            else:
                results["es_price"] = round(_close_scalar(es_df, -1), 2)
                prev_close = _close_scalar(es_df, -2) if len(es_df) > 1 else results["es_price"]
                results["es_change"] = round(results["es_price"] - prev_close, 2)
                results["es_pct"]    = round((results["es_change"] / prev_close) * 100, 2) if prev_close else 0.0
                results["es_ts"]     = _last_bar_est.strftime("%I:%M %p EST")
            # Overnight range: 4 PM yesterday → 9:30 AM today (ES pre-market)
            # Position in this range = where price sits (0=overnight low, 1=overnight high)
            # Use wall-clock date (not last-bar date) so Sunday pre-6 PM doesn't anchor
            # to Thursday's overnight session (last bar = Friday, would pull the wrong night).
            try:
                # index already converted to EST above
                _today = datetime.now(EST).date()
                _yesterday = _today - timedelta(days=1)
                _on = es_df[
                    ((es_df.index.date == _today) & (es_df.index.hour < 9)) |
                    ((es_df.index.date == _today) & (es_df.index.hour == 9) & (es_df.index.minute <= 30)) |
                    ((es_df.index.date == _yesterday) & (es_df.index.hour >= 16))
                ]
                if len(_on) >= 4:
                    _h = _on["High"].squeeze()
                    _l = _on["Low"].squeeze()
                    if isinstance(_h, pd.DataFrame): _h = _h.iloc[:, 0]
                    if isinstance(_l, pd.DataFrame): _l = _l.iloc[:, 0]
                    _on_high = float(_h.max()); _on_low = float(_l.min())
                    _on_rng  = max(_on_high - _on_low, 0.1)
                    _on_pos  = round((_close_scalar(es_df, -1) - _on_low) / _on_rng, 3)
                    results["overnight_high"] = round(_on_high, 1)
                    results["overnight_low"]  = round(_on_low, 1)
                    results["overnight_pos"]  = max(0.0, min(1.0, _on_pos))
            except Exception:
                pass
    except Exception:
        pass
    try:
        spx_df = yf.download("^GSPC", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not spx_df.empty:
            results["spx_price"] = round(_close_scalar(spx_df, -1), 2)
            prev_close = _close_scalar(spx_df, -2) if len(spx_df) > 1 else results["spx_price"]
            results["spx_change"] = round(results["spx_price"] - prev_close, 2)
            results["spx_pct"]    = round((results["spx_change"] / prev_close) * 100, 2) if prev_close else 0.0
            results["spx_ts"]     = spx_df.index[-1].astimezone(EST).strftime("%I:%M %p EST")
    except Exception:
        pass

    # ── NQ/ES divergence ─────────────────────────────────────────────────────
    # NQ outperforming ES = tech/growth risk-on (bull lean).
    # NQ lagging ES = distribution in leadership (bear lean).
    # divergence = nq_pct − es_pct. Threshold ±0.15% separates signal from noise.
    try:
        nq_df = yf.download("NQ=F", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not nq_df.empty:
            nq_df.index = nq_df.index.tz_convert(EST)
            _nq_last  = _last_bar_est if results.get("es_price") else nq_df.index[-1]
            _staleness = (datetime.now(EST) - nq_df.index[-1]).total_seconds() / 60
            if _staleness <= 30:
                nq_price = round(_close_scalar(nq_df, -1), 2)
                nq_prev  = _close_scalar(nq_df, -2) if len(nq_df) > 1 else nq_price
                nq_pct   = round((nq_price - nq_prev) / nq_prev * 100, 3) if nq_prev else 0.0
                es_pct   = results.get("es_pct") or 0.0
                results["nq_price"]    = nq_price
                results["nq_pct"]      = nq_pct
                results["nq_es_div"]   = round(nq_pct - es_pct, 3)   # + = NQ leading, − = NQ lagging
    except Exception:
        pass

    # ── ES pre-market momentum (last 30 min slope) ───────────────────────────
    # Compares ES price now to 30 min ago.  Rising = momentum bull, falling = bear.
    # Only meaningful pre-market (ES is the forward price signal pre-9:30).
    # Uses the same es_df fetched above (still in scope via closure over results).
    try:
        if results.get("es_price") and "es_df" not in dir():
            pass  # es_df already processed above; reference via re-fetch only if needed
        # Re-use es_df if it was set above (it is in outer try scope via yfinance call)
        # We access it through a separate fetch to keep the logic self-contained.
        _es_mom_df = yf.download("ES=F", period="1d", interval="1m", progress=False, auto_adjust=True)
        if not _es_mom_df.empty:
            _es_mom_df.index = _es_mom_df.index.tz_convert(EST)
            _now_est = datetime.now(EST)
            _t30_ago = _now_est - timedelta(minutes=30)
            _recent  = _es_mom_df[_es_mom_df.index >= _t30_ago]
            if len(_recent) >= 4:
                _mom_start = _close_scalar(_es_mom_df, _es_mom_df.index.searchsorted(_t30_ago))
                _mom_end   = _close_scalar(_es_mom_df, -1)
                _mom_chg   = round(_mom_end - _mom_start, 2)
                _mom_pct   = round(_mom_chg / _mom_start * 100, 3) if _mom_start else 0.0
                results["es_mom_30m"]     = _mom_chg    # pts change last 30 min
                results["es_mom_30m_pct"] = _mom_pct    # % change last 30 min
    except Exception:
        pass

    return results


@st.cache_data(ttl=3600)
def fetch_macro_signals():
    """
    Fetch macro regime signals not captured in price/vol data:
      1. Yield Curve: 10-year minus 3-month Treasury spread (^TNX - ^IRX).
         Positive = normal/bull; Negative = inverted = recession warning.
      2. Credit Spread proxy: HYG (high-yield bond ETF) vs TLT (long Treasury).
         HYG/TLT ratio rising = credit spreads compressing = risk-on.
      3. A/D Line proxy: Advance/Decline ratio (^ADVN / ^DECL).
         Ratio > 1.0 = more stocks advancing = broad bull; < 1 = breadth deteriorating.
    Returns dict with signal values, or defaults on failure.
    """
    out = {"yield_curve_pts": 0.0, "hyhg_ratio": 1.0, "ad_ratio": 1.0}
    try:
        tnx = yf.download("^TNX", period="5d", interval="1d", progress=False, auto_adjust=True)
        irx = yf.download("^IRX", period="5d", interval="1d", progress=False, auto_adjust=True)
        if not tnx.empty and not irx.empty:
            _tnx = float(tnx["Close"].squeeze().iloc[-1])
            _irx = float(irx["Close"].squeeze().iloc[-1])
            out["yield_curve_pts"] = round(_tnx - _irx, 3)
    except Exception:
        pass
    try:
        hyg = yf.download("HYG", period="10d", interval="1d", progress=False, auto_adjust=True)
        tlt = yf.download("TLT", period="10d", interval="1d", progress=False, auto_adjust=True)
        if not hyg.empty and not tlt.empty:
            _h = float(hyg["Close"].squeeze().iloc[-1])
            _t = float(tlt["Close"].squeeze().iloc[-1])
            _h1 = float(hyg["Close"].squeeze().iloc[-6]) if len(hyg) >= 6 else _h
            _t1 = float(tlt["Close"].squeeze().iloc[-6]) if len(tlt) >= 6 else _t
            # Ratio of HYG/TLT: rising = risk-on (credit spreads calm)
            _cur = _h / _t if _t else 1.0
            _ref = _h1 / _t1 if _t1 else _cur
            out["hyg_tlt_rising"] = _cur > _ref
    except Exception:
        pass
    try:
        adv = yf.download("^ADVN", period="5d", interval="1d", progress=False, auto_adjust=True)
        dec = yf.download("^DECL", period="5d", interval="1d", progress=False, auto_adjust=True)
        if not adv.empty and not dec.empty:
            _a = float(adv["Close"].squeeze().iloc[-1])
            _d = float(dec["Close"].squeeze().iloc[-1])
            out["ad_ratio"] = round(_a / _d, 2) if _d > 0 else 1.0
    except Exception:
        pass
    return out


@st.cache_data(ttl=60)
def fetch_intraday_rsi():
    """
    14-period RSI computed on 5-minute SPX bars (last 5 trading days).
    Replaces daily RSI for "RSI Above 50" and "RSI Trend Zone" during RTH —
    daily RSI is hours stale by mid-session; 5-min RSI reflects live momentum.
    Returns float RSI value, or None on failure.
    """
    try:
        df = yf.download("^GSPC", period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        c = df["Close"].squeeze()
        if isinstance(c, pd.DataFrame): c = c.iloc[:, 0]
        d   = c.diff()
        g   = d.clip(lower=0).rolling(14).mean()
        l   = (-d.clip(upper=0)).rolling(14).mean()
        _rv = 100 - (100 / (1 + g / (l + 1e-10)))
        val = float(_rv.iloc[-1])
        if pd.isna(val) or np.isinf(val): return None
        return round(max(0.0, min(100.0, val)), 1)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def rsi(series, n=14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))


def macd(series, f=12, s=26, sig=9):
    m = series.ewm(span=f).mean() - series.ewm(span=s).mean()
    return m, m.ewm(span=sig).mean()


def atr(df, n=14):
    h = df["High"].squeeze(); l = df["Low"].squeeze(); c = df["Close"].squeeze()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compute_ssr(spx, vix, pcr, sectors, macro=None, as_of_dt=None):
    close = spx["Close"].squeeze(); vol = spx["Volume"].squeeze()
    high  = spx["High"].squeeze();  low = spx["Low"].squeeze()
    if isinstance(close, pd.DataFrame): close = close.iloc[:, 0]
    if isinstance(vol,   pd.DataFrame): vol   = vol.iloc[:, 0]
    if isinstance(high,  pd.DataFrame): high  = high.iloc[:, 0]
    if isinstance(low,   pd.DataFrame): low   = low.iloc[:, 0]

    # Guard: need at least 6 bars for Higher Close (5d) and 200 for SMA200
    if len(close) < 7:
        return 50, 0, 0, {}

    rsi_v = rsi(close); macd_l, macd_s = macd(close); atr_v = atr(spx)
    vix_c = vix["Close"].squeeze()
    if isinstance(vix_c, pd.DataFrame): vix_c = vix_c.iloc[:, 0]
    sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
    sma200= close.rolling(200).mean()
    bb_mid= close.rolling(20).mean(); bb_std = close.rolling(20).std()

    # Stochastic: when high==low for 14 bars (halt/circuit breaker), set to 50 (neutral).
    # Replacing 0 denom with 1e-10 causes huge values even after clipping because
    # clip happens after the giant multiplication. Instead, default those bars to 50.
    _stoch_range = high.rolling(14).max() - low.rolling(14).min()
    _stoch_safe  = _stoch_range.where(_stoch_range > 0)   # NaN where range = 0
    stoch_k = (100 * (close - low.rolling(14).min()) / _stoch_safe).fillna(50).clip(0, 100)
    stoch_d = stoch_k.rolling(3).mean().fillna(50)

    c  = close.iloc[-1]
    c1 = close.iloc[-2] if len(close) >= 2 else c
    c5 = close.iloc[-6] if len(close) >= 6 else c

    sigs = {}
    # ── Trend group ──────────────────────────────────────────────────────────
    sigs["Above 20 SMA"]      = int(c > sma20.iloc[-1])
    sigs["Above 50 SMA"]      = int(c > sma50.iloc[-1])
    sigs["Above 200 SMA"]     = int(c > sma200.iloc[-1])
    sigs["20 SMA > 50 SMA"]   = int(sma20.iloc[-1] > sma50.iloc[-1])

    # ── Momentum group ───────────────────────────────────────────────────────
    sigs["Higher Close (1d)"] = int(c > c1)
    sigs["Higher Close (5d)"] = int(c > c5)
    sigs["RSI Above 50"]      = int(rsi_v.iloc[-1] > 50)
    sigs["MACD Bullish"]      = int(macd_l.iloc[-1] > macd_s.iloc[-1])

    # RSI Strong Trend: RSI 60-75 = strong momentum without overbought extreme.
    # Pairs with "RSI Above 50" to create a gradient: 50-60 fires one signal,
    # 60-75 fires both, giving the Momentum group a natural 2-tier RSI score.
    sigs["RSI Strong Trend"]  = int(60 <= rsi_v.iloc[-1] <= 75)

    # ── Volatility group ─────────────────────────────────────────────────────
    sigs["VIX Below 20"]      = int(vix_c.iloc[-1] < 20)
    # VIX Falling: 5-day trend — VIX today below VIX 5 sessions ago.
    # Uses the same formula as backtest_export.py so the live score and the
    # exporter validation measure exactly the same signal.  Gated by market
    # hours so it doesn't fire pre/post-session (consistent with prior behaviour).
    # VIX 1d Down (below) covers the single-session version without a gate.
    _ref_dt   = as_of_dt if as_of_dt is not None else datetime.now(EST)
    _now_wd   = _ref_dt.weekday()
    _now_h    = _ref_dt.hour
    _mkt_open = (_now_wd < 5 and 9 <= _now_h < 16)
    sigs["VIX Falling"] = (int(len(vix_c) >= 6 and float(vix_c.iloc[-1]) < float(vix_c.iloc[-6]))
                           if (len(vix_c) >= 6 and _mkt_open) else 0)
    # ATR Contracting: need >= 20 bars for ATR(14) to stabilize + 5 for comparison
    sigs["ATR Contracting"]   = int(len(atr_v.dropna()) >= 20 and atr_v.iloc[-1] < atr_v.iloc[-5])
    # VIX Below 15: ultra-calm regime tier. Pairs with "VIX Below 20" for gradient:
    # VIX 16-20 fires one signal; VIX <15 fires both — stronger low-vol bull context.
    sigs["VIX Below 15"]      = int(vix_c.iloc[-1] < 15)

    # Note: VIX Below 20 / VIX Below 15 are NOT blanket-removed in hi-VIX.
    # They are correctly bearish during genuine sustained bear trends.
    # When they persistently misfire (e.g. catalyst-driven reversals while VIX stays
    # elevated), the drift monitor flags them and the re-score block below neutralizes
    # them to 0.5 (abstain) — surgical per-signal dampening, not a static rule.

    # VIX rate-of-change signals: capture fear acceleration/deceleration
    # that the point-in-time VIX level misses. 3-day window balances noise vs signal.
    if len(vix_c) >= 4:
        _vix_3d_chg = (float(vix_c.iloc[-1]) - float(vix_c.iloc[-4])) / max(float(vix_c.iloc[-4]), 1)
        # Fear unwinding: VIX down >8% over 3 days = relief rally context = bull
        sigs["VIX 3d Relief"]  = int(_vix_3d_chg < -0.08)
        # VIX No Spike: INVERTED — fires 1 when no fear spike (calm = bull), 0 when spike (fear = bear).
        # Threshold 8%: VIX rising 8%+ over 3 days (e.g. 18→19.4) signals building fear.
        # Prior 15% threshold was too permissive — let VIX rise 15% and still voted "calm".
        sigs["VIX No Spike"]   = int(_vix_3d_chg <= 0.08)
    else:
        sigs["VIX 3d Relief"]  = 0
        sigs["VIX No Spike"]   = 1   # default: assume calm if insufficient history

    # VIX 1d Down: single-session VIX decline, no market-hours gate.
    # Complements VIX Falling (5-day trend): VIX 1d Down captures same-day vol relief
    # while VIX Falling captures a multi-day fear-unwind trend.  The two are now
    # genuinely independent signals with different time horizons.
    sigs["VIX 1d Down"] = int(len(vix_c) >= 2 and float(vix_c.iloc[-1]) < float(vix_c.iloc[-2]))

    # Gap/ATR ratio: is today's gap large relative to recent volatility?
    # A gap > 0.5× daily ATR is high-conviction and should amplify window bias.
    # We use Open vs prior Close from the daily DataFrame when available.
    _day_gap_pts = 0.0   # default: no gap context available
    _daily_atr   = 0.0   # default: no ATR context available
    _gap_atr_ratio = 0.0
    if "Open" in spx.columns and len(atr_v.dropna()) >= 14:
        _open_s = spx["Open"].squeeze()
        if isinstance(_open_s, pd.DataFrame): _open_s = _open_s.iloc[:, 0]
        if len(_open_s) >= 2:
            _day_gap_pts = float(_open_s.iloc[-1]) - float(close.iloc[-2])
            _daily_atr   = float(atr_v.dropna().iloc[-1])
            _gap_atr_ratio = abs(_day_gap_pts) / max(_daily_atr, 1)
    # Gap/ATR Normal: fires 1 only on small POSITIVE gap (0 to +0.5 ATR).
    # Direction-sensitive: a small down gap or flat open is NOT a bull signal.
    # Large gaps (>0.5 ATR) or negative gaps get 0 — reduces bull score on those days.
    # When gap context is unavailable (_daily_atr=0), omit the signal entirely
    # so unknown context does not bias the score bullish.
    _signed_gap_atr = _day_gap_pts / _daily_atr if _daily_atr > 0 else None
    if _signed_gap_atr is not None:
        sigs["Gap/ATR Normal"] = int(0.0 <= _signed_gap_atr < 0.5)

    # Gap Up Day: large positive opening gap from daily OHLC Open.
    # Fires = 1 when open > prev_close + GAP_THRESHOLD (>= 25 pts).
    # Unlike Gap/ATR Normal (which fires for SMALL gaps), this fires for LARGE gap-ups.
    # Core signal: computable from daily OHLC when Open is available.
    # Only set when Open data is present — avoids biasing Context group when gap is unknown.
    if "Open" in spx.columns and len(atr_v.dropna()) >= 14:
        _open_s_gu = spx["Open"].squeeze()
        if isinstance(_open_s_gu, pd.DataFrame): _open_s_gu = _open_s_gu.iloc[:, 0]
        if len(_open_s_gu) >= 2:
            sigs["Gap Up Day"] = int(_day_gap_pts > GAP_THRESHOLD)

    # ── Breadth group ────────────────────────────────────────────────────────
    # Volume directional: requires BOTH above-average volume AND a positive close
    # (accumulation = institutions buying into strength).  Raw high-volume alone
    # can be panic selling — that is not a bull signal.
    _vol_20_mean = vol.rolling(20).mean().iloc[-1]
    _vol_above_avg = len(vol.dropna()) >= 20 and vol.iloc[-1] > _vol_20_mean
    _price_up_today = len(close) >= 2 and close.iloc[-1] > close.iloc[-2]
    sigs["Volume Above Average"] = int(_vol_above_avg and _price_up_today)

    # Sector breadth — only 50% threshold kept (30% and 70% were redundant)
    _sec_closes = {}
    for t, df in sectors.items():
        if df.empty: continue
        try:
            _c = df["Close"].squeeze()
            if isinstance(_c, pd.DataFrame): _c = _c.iloc[:, 0]
            if len(_c) >= 50: _sec_closes[t] = _c
        except Exception:
            pass
    # Denominator = full sector universe (11), not just sectors with enough bars.
    # Missing or failed sector downloads are treated as "below SMA" (conservative).
    # This prevents inflated breadth when some ETFs fail to download.
    _total_s = len(sectors)   # always 11 (full universe)
    if _total_s and _sec_closes:
        _above = sum(1 for _c in _sec_closes.values()
                     if _c.iloc[-1] > _c.rolling(50).mean().iloc[-1])
        sigs["Sector Breadth ≥ 50%"] = int((_above / _total_s) >= 0.5)
        # Gradient breadth tiers: 50% / 70% / 85%.
        # Each tier adds a signal point, creating a 3-step gradient:
        #   >50% = broad participation; >70% = strong; >85% = near-full breadth.
        sigs["Sector Breadth ≥ 70%"] = int((_above / _total_s) >= 0.7)
        sigs["Sector Breadth ≥ 85%"] = int((_above / _total_s) >= 0.85)

    # ── Extremes group ───────────────────────────────────────────────────────
    sigs["Stoch Bullish"]   = int(stoch_k.iloc[-1] > stoch_d.iloc[-1])
    # RSI Trend Zone: RSI 45–65 = healthy continuation zone (not extreme either way).
    # More predictive than "RSI < 70" which fires at RSI=51 and tells you nothing.
    _rsi_last = rsi_v.iloc[-1]
    sigs["RSI Trend Zone"]  = int(45 <= _rsi_last <= 65)

    # ── Options group — CONTRARIAN direction ─────────────────────────────────
    # PCR > 1 = more puts than calls bought = fear premium = retail bearish.
    # Historically this is a CONTRARIAN BULLISH signal (smart money buys when retail fears).
    # PCR < 1 = complacency = contrarian bearish (but less reliable — retail can be right).
    if not pcr.empty and len(pcr) >= 2:
        pc = pcr["Close"].squeeze()
        sigs["Put/Call Fear Premium"] = int(pc.iloc[-1] > 1.0)       # HIGH fear = contrarian BULL
        # Fear Abating: PCR was above 1 (fear) AND is now falling = fear unwinding = bullish
        sigs["Put/Call Fear Abating"] = int(pc.iloc[-1] > 0.85 and pc.iloc[-1] < pc.iloc[-2])

    # ── A/D Breadth signal (from macro dict if provided) ─────────────────────
    if macro:
        _ad = macro.get("ad_ratio", 1.0)
        sigs["A/D Line Positive"] = int(_ad > 1.0)   # more advancing than declining = broad bull

    # ── Overnight range position (from macro dict, ES pre-market) ─────────────
    # overnight_pos: 0.0 = at overnight low, 1.0 = at overnight high.
    # Live-only — injected from fetch_live() ES=F overnight range computation.
    # Above 0.6 = price holding in the upper 40% of the overnight range = bull lean.
    if macro and macro.get("overnight_pos") is not None:
        sigs["Above Overnight Midpoint"] = int(macro["overnight_pos"] > 0.5)
        sigs["Overnight Upper Third"]    = int(macro["overnight_pos"] > 0.67)

    # ── NQ/ES divergence ─────────────────────────────────────────────────────
    # NQ outperforming ES (div > +0.15%) = tech leadership = risk-on bull signal.
    # NQ lagging ES (div < −0.15%) = distribution in leadership = bear signal.
    # Threshold 0.15% filters out tick noise on quiet pre-market sessions.
    if macro and macro.get("nq_es_div") is not None:
        _div = macro["nq_es_div"]
        sigs["NQ Bull Divergence"] = int(_div > 0.15)   # NQ leading ES = risk-on
        sigs["NQ Bear Divergence"] = int(_div < -0.15)  # NQ lagging ES = risk-off

    # ── ES pre-market momentum ────────────────────────────────────────────────
    # Rising ES over the last 30 min = pre-market momentum confirms bull bias.
    # Falling ES over 30 min dampens any bull score — supply entering pre-open.
    # Threshold ±0.05% (≈ ±3 pts on ES at 5800) filters micro-noise.
    if macro and macro.get("es_mom_30m_pct") is not None:
        _ep = macro["es_mom_30m_pct"]
        sigs["ES Pre-Market Momentum Bull"] = int(_ep >  0.05)
        sigs["ES Pre-Market Momentum Bear"] = int(_ep < -0.05)

    # ── Overnight range compression ───────────────────────────────────────────
    # overnight_range_atr: overnight range expressed as a fraction of the daily ATR.
    # Narrow range (<0.30× ATR) = compression = breakout risk either direction.
    # Wide range (>0.70× ATR) = expansion = most of the day's move may be done.
    # Compressed overnight = score dampening applied downstream via projection confidence.
    # Here we fire a signal only for the expansion case (most move done = mean-revert lean).
    if macro and macro.get("overnight_range_atr") is not None:
        _ora = macro["overnight_range_atr"]
        sigs["Overnight Range Compressed"] = int(_ora < 0.30)  # tight range = pending breakout
        sigs["Overnight Range Expanded"]   = int(_ora > 0.70)  # big overnight = exhaustion lean

    # ── Macro / regime signals ────────────────────────────────────────────────
    # Yield Curve: 10yr − 3mo > 0 = normal (bull context); < 0 = inverted = recession warning
    # Credit Spread proxy: HYG/TLT ratio rising = spreads compressing = risk-on
    if macro:
        _yc = macro.get("yield_curve_pts", 0.0)
        sigs["Yield Curve Positive"]  = int(_yc > 0)   # non-inverted = macro tailwind
        _hyg_rising = macro.get("hyg_tlt_rising", True)
        sigs["Credit Spread Calm"]    = int(_hyg_rising)  # HYG/TLT rising = credit risk-on

    # ── Distance and positioning signals ─────────────────────────────────────
    # These capture "where are we in the range" — not captured by trend or momentum above.
    if len(close) >= 252:
        _high_52w = close.rolling(252).max().iloc[-1]
        _low_52w  = close.rolling(252).min().iloc[-1]
        _range_52w = max(_high_52w - _low_52w, 1)
        # Position in 52-week range: >80% of range = near highs (bull), <20% = near lows (bear)
        _range_pos = (c - _low_52w) / _range_52w
        sigs["52w Range Upper Half"] = int(_range_pos > 0.5)   # above midpoint of annual range
        sigs["52w Range Top 20%"]    = int(_range_pos > 0.80)  # near yearly highs = momentum
    elif len(close) >= 20:
        # Use 20-day range as fallback
        _high_20 = high.rolling(20).max().iloc[-1]
        _low_20  = low.rolling(20).min().iloc[-1]
        _r20     = max(_high_20 - _low_20, 1)
        _pos_20  = (c - _low_20) / _r20
        sigs["52w Range Upper Half"] = int(_pos_20 > 0.5)
        sigs["52w Range Top 20%"]    = int(_pos_20 > 0.80)

    # Bollinger Band position: price above midline = mild bull context
    sigs["Above BB Mid"]         = int(c > bb_mid.iloc[-1])

    # Above Prior Day High: today's close exceeds yesterday's intraday high.
    # Signals trend continuation / breakout above prior resistance.
    # ph and pl are defined earlier: ph = high.iloc[-2], pl = low.iloc[-2]
    sigs["Above Prior Day High"] = int(c > float(high.iloc[-2])) if len(high) >= 2 else 0

    # Above Pivot: classic pivot point = (prev_high + prev_low + prev_close) / 3.
    # Price above pivot = bias is bullish for the session; below = bearish lean.
    if len(high) >= 2 and len(low) >= 2:
        _pivot = (float(high.iloc[-2]) + float(low.iloc[-2]) + float(close.iloc[-2])) / 3.0
        sigs["Above Pivot"] = int(c > _pivot)

    # Above 5d High: breaking above the prior 5 bars' highest high = weekly breakout.
    # More immediate than 52w range — captures near-term breakout momentum.
    if len(high) >= 6:
        _5d_high_raw = high.iloc[-6:-1].max()   # prior 5 bars (excludes today)
        if not pd.isna(_5d_high_raw):
            sigs["Above 5d High"] = int(c > float(_5d_high_raw))

    buys  = sum(1 for v in sigs.values() if v == 1)
    sells = sum(1 for v in sigs.values() if v == 0)

    # Weighted group score — each category contributes equally to the final SSR.
    # Prevents over-represented categories (VIX has 4 signals, MACD has 1) from
    # dominating the raw buys/total ratio.
    group_scores = []
    for grp_signals in SIGNAL_GROUPS.values():
        present = [sigs[k] for k in grp_signals if k in sigs]
        if present:
            group_scores.append(sum(present) / len(present))
    score = round(sum(group_scores) / len(group_scores) * 100) if group_scores else round((buys / (buys + sells)) * 100) if (buys + sells) > 0 else 50

    return score, buys, sells, sigs


def compute_levels(spx):
    close = spx["Close"].squeeze(); high = spx["High"].squeeze(); low = spx["Low"].squeeze()
    if isinstance(close, pd.DataFrame): close = close.iloc[:, 0]
    if isinstance(high,  pd.DataFrame): high  = high.iloc[:, 0]
    if isinstance(low,   pd.DataFrame): low   = low.iloc[:, 0]
    if len(close) < 6:
        return {k: 0.0 for k in ["current","atr","pivot","resistance_1","resistance_2",
                                   "resistance_3","support_1","support_2","support_3",
                                   "target_up_1","target_up_2","target_down_1","target_down_2",
                                   "week_high","week_low","prev_high","prev_low","rsi"]}
    c = close.iloc[-1]; ph = high.iloc[-2]; pl = low.iloc[-2]; pc = close.iloc[-2]
    pivot = (ph+pl+pc)/3
    atr14_raw = atr(spx).iloc[-1]
    atr14 = float(atr14_raw) if not pd.isna(atr14_raw) else float(high.iloc[-1] - low.iloc[-1])
    # Fallback: if ATR is still 0 or NaN (data gap / partial bar), estimate as 1% of price
    if not atr14 or pd.isna(atr14):
        atr14 = round(float(c) * 0.010, 1)
    return {
        "current":       round(c,1),   "atr":           round(atr14,1),
        "pivot":         round(pivot,1),
        "resistance_1":  round(2*pivot-pl,1),  "resistance_2": round(pivot+(ph-pl),1),
        "resistance_3":  round(ph+2*(pivot-pl),1),
        "support_1":     round(2*pivot-ph,1),   "support_2":    round(pivot-(ph-pl),1),
        "support_3":     round(pl-2*(ph-pivot),1),
        "target_up_1":   round(c+atr14,1),      "target_up_2":  round(c+2*atr14,1),
        "target_down_1": round(c-atr14,1),      "target_down_2":round(c-2*atr14,1),
        "week_high":     round(high.iloc[-5:].max(),1), "week_low": round(low.iloc[-5:].min(),1),
        "prev_high":     round(ph,1),  "prev_low": round(pl,1),
        "rsi":           round(rsi(close).iloc[-1],1),
    }


def ssr_meta(score):
    if   score <= 35: return "🔴🔴 STRONG SELL", "HIGH CONVICTION PUTS",  "puts",    "#b91c1c"
    elif score <= 44: return "🔴 SELL",           "PUTS — STANDARD",        "puts",    "#ef4444"
    elif score <= 54: return "⚪ NEUTRAL",         "NO EDGE — WAIT",         "neutral", "#6b7280"
    elif score <= 65: return "🟢 WEAK BUY",        "LIGHT CALLS",            "calls",   "#22c55e"
    else:             return "🟢🟢 STRONG BUY",   "HIGH CONVICTION CALLS",  "calls",   "#15803d"


def get_current_window():
    now = datetime.now(EST); hhmm = now.strftime("%H:%M")
    for start, end, label, bias in TIME_WINDOWS:
        if start <= hhmm < end:
            return label, bias, start, end
    return "Outside Market Hours", "neutral", "--", "--"


def get_opex_friday(ref_date=None):
    """Return the 3rd Friday (standard monthly OpEx) for the month of ref_date."""
    if ref_date is None:
        ref_date = datetime.now(EST).date()
    first = ref_date.replace(day=1)
    # weekday() 4 = Friday
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)   # first Friday + 2 weeks = 3rd Friday


def is_opex_week(ref_date=None):
    """
    True if the current week contains the 3rd Friday of the month (standard monthly OpEx).
    OpEx weeks see gamma pinning (range compression mid-week) and sharp directional
    unwinds into Friday close as market makers delta-hedge expiring positions.
    """
    if ref_date is None:
        ref_date = datetime.now(EST).date()
    third_fri = get_opex_friday(ref_date)
    week_start = ref_date - timedelta(days=ref_date.weekday())   # Monday
    week_end   = week_start + timedelta(days=4)                  # Friday
    return week_start <= third_fri <= week_end


def is_opex_friday(ref_date=None):
    """True if today IS the 3rd Friday (EOD unwind day)."""
    if ref_date is None:
        ref_date = datetime.now(EST).date()
    return ref_date == get_opex_friday(ref_date)


def window_bias_at(hhmm, gap=0.0, vix=0.0, news_score=0.0, orb_status="inside", opex=False,
                   event_types=None, weekday=None, orb_range_atr=0.0, atr=0.0,
                   gap_confirmed=False):
    """
    Return (bias, label) for a given HH:MM.
    gap           = today's open − prior close (positive = gap-up, negative = gap-down).
    vix           = current VIX reading (0 = unknown / skip VIX override).
    news_score    = composite news sentiment −1..+1 (from load_news composite_score).
    orb_status    = "above" | "below" | "inside" (Opening Range Breakout status).
    opex          = True if current week is standard monthly options expiration week.
    orb_range_atr = ORB width / daily ATR. When > 0 and < 0.12, the ORB is too narrow
                    to be a reliable breakout signal — override is suppressed.
    atr           = daily ATR in points — used to classify gap size relative to volatility.

    Override hierarchy (highest priority first):
      1. Gap-up > 25pts  → Pre-Bull Fade & Afternoon Trend become chop
      2. Hi-VIX + small gap-up (gap/ATR < 0.4) → Bull Window stays bull (gap-fade bounce)
      3. VIX > 25 (fear) → chop windows become bear; bull windows become chop;
                           EOD Trend neutralised to chop (late reversal risk is high)
      4. VIX < 18 (calm) → bear windows soften to chop (range-bound low-vol)
      5. Economic event  → FOMC/CPI/NFP time-of-day overrides
      6. OpEx week       → mid-session chop reinforced; EOD Trend directional preserved
      7. ORB breakout    → price outside opening range shifts chop→directional (post 10 AM)
      8. News sentiment  → strong news (|score|≥0.25) shifts chop→directional
    """
    # Pre-compute ATR-relative gap size (0 = unknown ATR, skip ratio logic)
    _gap_atr_ratio = abs(gap) / atr if atr > 0 else 0.0
    _small_gap_up  = gap > GAP_THRESHOLD and _gap_atr_ratio < 0.4   # gap-up but < 0.4× ATR
    # Catalyst-aligned gap: strong news (|news_score| ≥ 0.25) that confirms the gap direction.
    # A macro catalyst (e.g. geopolitical de-escalation, surprise Fed pivot) means the gap
    # is fundamentally driven — it should NOT be faded and should NOT be suppressed by the
    # hi-VIX bull→chop override.  Both the gap-fade and the hi-VIX demotion are bypassed.
    _gap_catalyst_aligned = (
        (gap > GAP_THRESHOLD  and news_score >=  0.25) or
        (gap < -GAP_THRESHOLD and news_score <= -0.25)
    )
    for start, end, label, bias in TIME_WINDOWS:
        if start <= hhmm < end:
            # ── Gap-conditional overrides ──────────────────────────────────
            # Skip gap-fade when a macro catalyst confirms the gap direction.
            if label == "Pre-Bull Fade" and gap > GAP_THRESHOLD and not _gap_catalyst_aligned:
                return "chop", label + " (gap-up→chop)"
            if label == "Afternoon Trend" and gap > GAP_THRESHOLD and not _gap_catalyst_aligned:
                return "chop", label + " (gap-up→chop)"
            # Gap-down override: Bull Window loses reliability on large gap-down opens.
            # On gap-down >25pts, the 10:00-10:30 bounce attempt is uncertain — treat as chop.
            # (VIX > 25 already handles this via hi-VIX→chop, but gap-down at VIX 18-25 is missed.)
            if label == "Bull Window" and gap < -GAP_THRESHOLD and not _gap_catalyst_aligned:
                return "chop", label + " (gap-down→chop)"

            # ── Hi-VIX small gap-up: Bull Window stays bull (gap-fade bounce) ──
            # When VIX > 25 and gap-up is small relative to ATR (< 0.4×),
            # the 10:00–10:30 Bull Window reflects a reliable gap-fade bounce attempt
            # before the downtrend resumes. Keep it as bull — don't override to chop.
            if vix > VIX_FEAR_THRESHOLD and _small_gap_up and label == "Bull Window":
                return "bull", "Bull Window (hi-VIX gap-fade bounce)"

            # ── VIX fear regime (VIX > 25) ─────────────────────────────────
            # In high-fear trending markets, intraday "chop" rarely materialises;
            # selling pressure bleeds through. Bull windows lose reliability.
            # EOD Trend (15:30–16:00) is neutralised to chop in hi-VIX: late
            # reversals are common and a clean bear trend in the last 30m is unreliable.
            # Exception: catalyst-aligned gap-up preserves bull-biased windows even in
            # hi-VIX — a macro catalyst is a stronger signal than the regime override.
            if vix > VIX_FEAR_THRESHOLD and label not in ("Open Volatility", "AH Bull Window (ES)"):
                if label == "EOD Trend":
                    return "chop", label + " (hi-VIX→reversal risk)"
                if bias == "chop":
                    # Gap confirmed after 9:45 AM: the gap is NOT fading — keep
                    # chop windows as chop rather than converting to bear in the
                    # first 2 hours.  The market is holding its ground despite VIX.
                    if gap_confirmed and gap > GAP_THRESHOLD and hhmm < "11:30":
                        return "chop", label + " (gap-confirmed→chop)"
                    # Gap-down bounce protection: gap-down + hi-VIX mornings bounce
                    # >50% of the time historically.  Keep chop windows as chop until
                    # 11:30 instead of forcing bear — trend confirmation is unreliable
                    # before midday after a large gap-down open.
                    if gap < -GAP_THRESHOLD and hhmm < "11:30" and not _gap_catalyst_aligned:
                        return "chop", label + " (gap-dn bounce zone)"
                    return "bear", label + " (hi-VIX→bear)"
                if bias == "bull" and not _gap_catalyst_aligned:
                    return "chop", label + " (hi-VIX→chop)"
                if bias == "bull" and _gap_catalyst_aligned:
                    return "bull", label + " (catalyst-confirmed)"

            # ── VIX calm regime (VIX < 18) ─────────────────────────────────
            # Low-vol rangebound: bear windows tend to mean-revert instead of trend.
            if vix > 0 and vix < VIX_CALM_THRESHOLD and bias == "bear":
                if label not in ("EOD Trend", "Bear Window / Peak"):
                    return "chop", label + " (lo-VIX→chop)"

            # ── Economic event overrides ────────────────────────────────────
            # event_types may be injected by caller (backtest uses historical calendar);
            # falls back to today's live lookup only when not provided.
            _today_events = event_types if event_types is not None else get_event_types_today()
            if "FOMC" in _today_events:
                # Pre-2PM: everyone waits → forced chop
                if "09:30" <= hhmm < "14:00":
                    return "chop", label + " (FOMC-wait)"
                # 2PM-3PM: violent move post-announcement — amplify direction
                if "14:00" <= hhmm < "15:00":
                    return bias, label + " ⚡FOMC"
            if ("CPI" in _today_events or "NFP" in _today_events) and "09:30" <= hhmm < "10:00":
                # First 30 min after data release: knee-jerk then reversal — treat as chop
                return "chop", label + " (CPI/NFP reversal zone)"

            # ── OpEx week overrides ──────────────────────────────────────────
            # Monthly options expiration (3rd Friday) patterns:
            #   1. Mon-Tue: early gamma pinning — reinforce chop windows as genuinely flat
            #   2. Wed-Thu: hedges re-price and fast money re-positions → directional allowed
            #   3. EOD Friday: gamma delta-hedge unwind = sharp directional move — keep bias
            if opex:
                _today_wd = weekday if weekday is not None else datetime.now(EST).weekday()
                if bias == "chop" and "10:45" <= hhmm <= "14:00" and _today_wd <= 1:
                    # Mon/Tue only — early pinning is real; Wed-Thu don't suppress direction
                    return "chop", label + " (OpEx pin)"
                if label == "EOD Trend":
                    # Override any VIX-based softening on OpEx week — gamma unwind = directional
                    return bias, label + " (OpEx unwind)"

            # ── ORB breakout override ───────────────────────────────────────
            # Only applies post 10:00 AM — opening volatility (9:30–10:00) is
            # too noisy for ORB confirmation. After 10 AM, price outside the
            # 9:30–9:45 range is a statistically meaningful breakout.
            # Narrow ORB guard: if ORB width < 0.12× daily ATR (e.g. ATR=40pts → ORB<4.8pts),
            # the range is too tight for the breakout to mean anything — skip override.
            # orb_range_atr=0.0 (unknown) passes through as before.
            _orb_wide_enough = (orb_range_atr > 0.0 and orb_range_atr >= 0.12)
            if hhmm >= "10:00" and bias == "chop" and _orb_wide_enough:
                if orb_status == "above":
                    return "bull", label + " (ORB↑ bull)"
                if orb_status == "below":
                    return "bear", label + " (ORB↓ bear)"

            # ── News sentiment override (lowest priority) ───────────────────
            # Strong news flow shifts "chop" windows into directional.
            # Threshold 0.25 = meaningful consensus, not just 1 headline.
            _NEWS_THRESH = 0.25
            if bias == "chop" and abs(news_score) >= _NEWS_THRESH:
                if news_score > 0:
                    return "bull", label + " (news→bull)"
                else:
                    return "bear", label + " (news→bear)"

            return bias, label
    return "neutral", "Outside Hours"


def ssr_direction(score):
    """
    Normalised directional factor from SSR: -1.0 … +1.0
    Smooth piecewise-linear interpolation — eliminates step-function cliff edges
    that caused 0.60-point jumps at score boundaries (35/36, 44/45, 54/55, 65/66).
    Breakpoints preserve the same regime anchors as the old step function:
      ≤35 → -1.0 (strong bear), 40 → -0.6, 50 → 0.0, 60 → +0.6, ≥65 → +1.0
    """
    return float(np.interp(score,
                           [0,   35,   40,   50,  60,  65,  100],
                           [-1.0,-1.0, -0.6,  0.0, 0.6, 1.0, 1.0]))


def is_es_active(dt_est):
    """True if ES is trading at the given EST datetime (24x5, skip 5–6 PM daily + weekends)."""
    wd = dt_est.weekday()          # 0=Mon … 6=Sun
    h  = dt_est.hour
    # Weekend maintenance: Fri 5 PM → Sun 6 PM
    if wd == 5: return False       # all Saturday
    if wd == 6 and h < 18: return False   # Sunday before 6 PM
    if wd == 4 and h >= 17: return False  # Friday 5 PM+
    # Daily maintenance 5–6 PM ET
    if h == 17: return False
    return True


def next_es_open(now):
    """Return the datetime of the next ES opening bell from now."""
    # ES opens at 6:00 PM EST Sun–Thu (after daily maintenance)
    # If currently in a session, start from the next session open
    t = now.replace(second=0, microsecond=0)
    # Round up to next 30-min boundary so we start clean
    t += timedelta(minutes=1)
    for _ in range(14 * 48):          # scan up to 14 days in 30-min steps
        if t.minute not in (0, 30):
            t += timedelta(minutes=30 - t.minute % 30)
            continue
        wd = t.weekday()              # 0=Mon … 6=Sun
        h  = t.hour
        # Valid ES open = 6 PM on Sun(6) through Thu(3), not Sat(5), not Fri≥5PM
        is_open_bell = (h == 18 and t.minute == 0 and wd != 5
                        and not (wd == 4 and h >= 17))
        if is_open_bell and t > now:
            return t
        t += timedelta(minutes=30)
    return now  # fallback


def generate_es_projections(base_price, daily_atr, score, gap=0.0, vix=0.0, news_score=0.0, orb_status="inside", opex=False, orb_range_atr=0.0, orb_distance_atr=0.0, gap_confirmed=False):
    """30-minute ES projections for 23 hours starting from the next opening bell."""
    if not daily_atr or daily_atr < 1.0:
        daily_atr = round(base_price * 0.010, 1)
    direction = ssr_direction(score)

    # VIX regime scaling — smoothly interpolated to avoid cliff-edge jumps at 20/25/30/35
    _vx = float(np.interp(vix, [0, 20, 25, 30, 35, 100], [1.0, 1.15, 1.35, 1.60, 2.0, 2.0]))

    # OpEx gamma-pinning compression: mid-week RTH ranges ~15% tighter
    # (market makers hold price near max-pain strike, suppressing ATR)
    _opex_factor = 0.85 if opex else 1.0

    # ATR per 30-minute ES slot — front-loaded to match real intraday vol distribution.
    # Each hour bucket holds two 30-min slots; fractions mirror the SPX hourly ATR profile
    # (sum of all 13 RTH slots = 1.00× daily_atr, identical budget to SPX projection).
    # 9 AM:  2×0.14 = 0.28  (open volatility spike)
    # 10 AM: 2×0.09 = 0.18  (settling from open)
    # 11 AM: 2×0.06 = 0.12  (morning fade)
    # 12 PM: 2×0.04 = 0.08  (lunch — minimal movement)
    # 13 PM: 2×0.045= 0.09  (PM setup)
    # 14 PM: 2×0.055= 0.11  (PM trend)
    # 15 PM: 1×0.09 = 0.09  (power-hour close)  → total = 1.00×
    _ES_RTH_PROFILE = {9: 0.14, 10: 0.09, 11: 0.06, 12: 0.04, 13: 0.045, 14: 0.055, 15: 0.09}
    def slot_atr(h, is_open_bell=False):
        if is_open_bell:
            # 6 PM opening bell = first price discovery after market close.
            # Weekend or post-close gaps materialise here — treat like a mini open volatility.
            # 0.07× daily_atr ≈ half the RTH 9:30 multiplier; VIX scaling amplifies further
            # (e.g. VIX=25 → 0.07×1.35=0.095×ATR ≈ 5-6 pts on a 60-pt ATR day).
            return daily_atr * 0.07 * _vx
        if 9 <= h < 16:  return daily_atr * _ES_RTH_PROFILE.get(h, 0.077) * _vx * _opex_factor
        if 16 <= h < 17: return daily_atr * 0.035 * _vx * _opex_factor
        return           daily_atr * 0.025 * _vx

    now    = datetime.now(EST)
    open_t = next_es_open(now)        # next 6:00 PM opening bell
    rows   = []
    price  = base_price
    t      = open_t
    total_session_minutes = 23 * 60   # ES runs 23 h per day
    elapsed = 0

    while elapsed < total_session_minutes:
        if is_es_active(t):
            hhmm     = t.strftime("%H:%M")
            _is_open_bell = (t == open_t)  # first slot of the session
            win_bias, win_label = window_bias_at(hhmm, gap=gap, vix=vix, news_score=news_score, orb_status=orb_status, opex=opex, orb_range_atr=orb_range_atr, atr=daily_atr, gap_confirmed=gap_confirmed)
            wf       = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
            satr     = slot_atr(t.hour, is_open_bell=_is_open_bell)

            # Direction confidence: neutral SSR (score≈50) should produce minimal moves
            # even when a directional window is active. The window only has edge when
            # the daily conviction (SSR) agrees with it.
            _dir_conf = min(1.0, abs(direction) + 0.15)   # 0→0.15, 0.6→0.75, 1→1.0

            # Regime-aware blend: how much SSR direction vs window bias drives the move.
            # High-VIX trending days: direction dominates (windows are noisy).
            # Low-VIX range days: window timing is more reliable.
            # Large gap days: gap direction pressure lifts SSR weight.
            # OpEx: balanced (gamma pins both SSR and window edge).
            # Borderline score (35–65) in hi-VIX: reduce direction dominance so a
            # near-neutral score doesn't project as if it were high-conviction.
            _es_score_borderline = 35 < score < 65
            if vix > VIX_FEAR_THRESHOLD:
                # Gap-down + hi-VIX: reduce SSR conviction weight — bounce risk is high.
                if gap < -GAP_THRESHOLD:
                    _dir_w, _win_w = (0.40, 0.60) if _es_score_borderline else (0.50, 0.50)
                else:
                    _dir_w, _win_w = (0.55, 0.45) if _es_score_borderline else (0.70, 0.30)
            elif 0 < vix < VIX_CALM_THRESHOLD:
                _dir_w, _win_w = 0.40, 0.60
            elif gap < -GAP_THRESHOLD:
                _dir_w, _win_w = 0.65, 0.35
            elif gap > GAP_THRESHOLD:
                _dir_w, _win_w = 0.60, 0.40
            elif opex:
                _dir_w, _win_w = 0.50, 0.50
            else:
                _dir_w, _win_w = 0.55, 0.45

            # ORB momentum boost: when price has traveled past the ORB edge,
            # the breakout has follow-through — scale the window weight up slightly.
            # Only applies during RTH (9–16 EST) when ORB status is confirmed.
            # Capped at 1.3× to prevent runaway projection on large ORB gaps.
            if orb_distance_atr > 0.0 and orb_status in ("above", "below") and 9 <= t.hour < 16:
                wf *= min(1.3, 1.0 + orb_distance_atr * 0.5)

            # 6 PM opening bell: gap has already materialized in the price feed.
            # SSR direction dominates — window label is "Outside Hours"/neutral here.
            # No reversion at open (drift=0 at t=open_t).
            if _is_open_bell:
                move = satr * direction * _dir_conf
            # Outside-hours slots: no window bias, SSR direction drives overnight drift
            elif win_bias == "neutral":
                move = satr * direction * 0.60 * _dir_conf
            else:
                move = satr * (direction * _dir_w + wf * _win_w) * _dir_conf

            # Regime-aware mean-reversion dampener: drift-from-base pull-back.
            # High-VIX trending days allow trend to run (less reversion).
            # Low-VIX range-bound days revert more firmly.
            _drift    = price - base_price
            _rev_rate = 0.008 if vix > VIX_FEAR_THRESHOLD else (0.020 if (0 < vix < VIX_CALM_THRESHOLD) else 0.015)
            _revert   = -_drift * _rev_rate
            price     = round(price + move + _revert, 1)
            sess     = "RTH" if 9 <= t.hour < 16 else ("AH" if 16 <= t.hour < 17 else "Overnight")
            rows.append({
                "time":      t.strftime("%a %-I:%M %p"),
                "session":   sess,
                "price":     price,
                "move":      round(move, 1),
                "rng_lo":    round(price - satr * 0.5, 1),
                "rng_hi":    round(price + satr * 0.5, 1),
                "win_bias":  win_bias,
                "win_label": win_label,
            })
            elapsed += 30
        # always advance 30 min; maintenance window is skipped via is_es_active
        t += timedelta(minutes=30)

    return rows


def next_trading_day(from_date):
    """Return the next NYSE trading day on or after from_date (skips weekends + holidays)."""
    d = from_date
    for _ in range(10):   # max 10-day scan covers any holiday cluster
        if d.weekday() < 5 and d not in US_MARKET_HOLIDAYS:
            return d
        d += timedelta(days=1)
    return d  # fallback


def generate_spx_projections(base_price, daily_atr, score, gap=0.0, vix=0.0, news_score=0.0, orb_status="inside", opex=False, orb_range_atr=0.0, orb_distance_atr=0.0, gap_confirmed=False):
    """Hourly SPX projections for the next/current RTH session (9:30 AM – 4:00 PM)."""
    if not daily_atr or daily_atr < 1.0:
        daily_atr = round(base_price * 0.010, 1)
    direction = ssr_direction(score)
    # VIX regime scaling — smoothly interpolated to avoid cliff-edge jumps at 20/25/30/35
    _vx = float(np.interp(vix, [0, 20, 25, 30, 35, 100], [1.0, 1.15, 1.35, 1.60, 2.0, 2.0]))
    _opex_factor = 0.85 if opex else 1.0

    # Adaptive intraday ATR profile: market open is front-loaded (~30% of day's ATR
    # in first hour), then decays into lunch, picks up into close.
    # Weights sum to 1.0 × daily_atr. Slots: 9:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30, 16:00
    _atr_profile = [0.28, 0.18, 0.12, 0.08, 0.09, 0.11, 0.09, 0.05]  # sums to 1.0
    slots     = ["09:30","10:30","11:30","12:30","13:30","14:30","15:30","16:00"]
    now       = datetime.now(EST)

    # Use today if it's a weekday and market hasn't fully closed yet (before 4 PM)
    # Otherwise use the next trading day — no "past" rows shown
    today = now.date()
    if today.weekday() >= 5 or now.hour >= 16:
        # weekend or after close → show next trading day, nothing is "past"
        session_date = next_trading_day(today + timedelta(days=1))
        all_future   = True
    else:
        session_date = today
        all_future   = False

    rows  = []
    price = base_price
    for idx, slot in enumerate(slots):
        sh, sm = map(int, slot.split(":"))
        t        = EST.localize(datetime(session_date.year, session_date.month, session_date.day, sh, sm))
        is_past  = (not all_future) and (t < now)
        win_bias, win_label = window_bias_at(slot, gap=gap, vix=vix, news_score=news_score, orb_status=orb_status, opex=opex, orb_range_atr=orb_range_atr, atr=daily_atr, gap_confirmed=gap_confirmed)
        win_factor  = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
        _dir_conf   = min(1.0, abs(direction) + 0.15)
        _drift      = price - base_price
        _rev_rate   = 0.008 if vix > VIX_FEAR_THRESHOLD else (0.020 if (0 < vix < VIX_CALM_THRESHOLD) else 0.015)
        _revert     = -_drift * _rev_rate
        # Adaptive slot ATR: front-loaded to match real intraday vol distribution
        _slot_atr   = daily_atr * _atr_profile[min(idx, len(_atr_profile)-1)] * _vx * _opex_factor
        # Regime-aware blend (mirrors ES logic above)
        # Borderline score (35–65) means SSR is near-neutral — reduce direction weight
        # in hi-VIX to avoid projecting strong directional moves from a weak signal.
        _score_borderline = 35 < score < 65
        # Large gap-up: prior-day SSR captures yesterday's close regime, not the gap.
        # When the gap is >0.5× daily ATR, window bias is a better intraday guide.
        _gap_atr_ratio = gap / max(daily_atr, 1.0)
        _large_gap_up  = gap > GAP_THRESHOLD and _gap_atr_ratio > 0.5
        if vix > VIX_FEAR_THRESHOLD:
            # Gap-down + hi-VIX: reduce SSR conviction weight — bounce risk is high.
            if gap < -GAP_THRESHOLD:
                _dir_w, _win_w = (0.40, 0.60) if _score_borderline else (0.50, 0.50)
            elif _large_gap_up:
                # Large gap-up in hi-VIX: SSR reflects fear from prior close; let windows lead.
                _dir_w, _win_w = (0.35, 0.65) if _score_borderline else (0.45, 0.55)
            else:
                _dir_w, _win_w = (0.55, 0.45) if _score_borderline else (0.70, 0.30)
        elif 0 < vix < VIX_CALM_THRESHOLD:
            _dir_w, _win_w = 0.40, 0.60
        elif gap < -GAP_THRESHOLD:
            _dir_w, _win_w = 0.65, 0.35
        elif _large_gap_up:
            # Large gap-up in normal VIX: SSR is a poor predictor of intraday direction
            # from the gap-up open. Reduce SSR weight so model doesn't project reversal.
            _dir_w, _win_w = (0.40, 0.60) if _score_borderline else (0.50, 0.50)
        elif gap > GAP_THRESHOLD:
            _dir_w, _win_w = 0.60, 0.40
        elif opex:
            _dir_w, _win_w = 0.50, 0.50
        else:
            _dir_w, _win_w = 0.55, 0.45
        # ORB momentum boost: scale window factor when price has moved past ORB edge.
        # Same logic as ES: capped at 1.3×, only during RTH, only when confirmed.
        _wf_boosted = win_factor
        if orb_distance_atr > 0.0 and orb_status in ("above", "below"):
            _wf_boosted *= min(1.3, 1.0 + orb_distance_atr * 0.5)
        move        = _slot_atr * (direction * _dir_w + _wf_boosted * _win_w) * _dir_conf + _revert
        price = round(price + move, 1)
        day_label = session_date.strftime("%a") if all_future else ""
        rows.append({
            "time":      f"{day_label} {to_ampm(slot)}".strip(),
            "price":     price,
            "move":      round(move, 1),
            "rng_lo":    round(price - _slot_atr * 0.4, 1),
            "rng_hi":    round(price + _slot_atr * 0.4, 1),
            "win_bias":  win_bias,
            "win_label": win_label,
            "past":      is_past,
        })
    return rows


# Day-of-week historical tendencies vs SSR direction (0=Mon … 4=Fri).
# Positive = amplifies SSR direction (continuation).
# Negative = opposes SSR direction (mean-reversion / bounce tendency).
# Based on SPX intraweek seasonality research:
#   Mon: gap-fill / overnight-position squaring → mild reversion
#   Tue: strongest continuation day of the week
#   Wed: mid-week pivot / reversal common
#   Thu: pre-expiry positioning, tends to extend Thursday–Friday trend
#   Fri: profit-taking / OpEx hedging unwind → fades extreme moves
_DOW_TENDENCY = {0: -0.18, 1: 0.12, 2: -0.10, 3: 0.08, 4: -0.22}


def generate_weekly_projections(base_price, daily_atr, score, vix=0.0):
    """
    Daily projections for the next 5 trading days with:
      • Day-of-week tendency (Mon/Wed/Fri mean-revert; Tue/Thu continue)
      • Exhaustion dampener: extreme SSR fades toward neutral over the week
        (disabled when VIX > 25 — regime-driven moves don't mean-revert in 5 days)
      • VIX regime scaling on daily ATR (same interpolation as intraday projections)
      • Per-day directional confidence (not just magnitude decay)
    """
    if not daily_atr or daily_atr < 1.0:
        daily_atr = round(base_price * 0.010, 1)
    # Scale ATR by VIX regime so high-fear weeks show wider daily ranges
    _vx_weekly = float(np.interp(vix, [0, 20, 25, 30, 35, 100], [1.0, 1.10, 1.25, 1.45, 1.75, 1.75]))
    daily_atr = round(daily_atr * _vx_weekly, 1)

    base_dir = ssr_direction(score)

    # Exhaustion factor [0–1]: kicks in beyond ±15 SSR units from 50
    # Score 50 → 0.0 (no exhaustion), score 20 or 80 → ~1.0 (full reversion expected)
    # GATE: when VIX > 25 the market is in a fear/momentum regime — exhaustion doesn't apply.
    # Forced multi-day moves (crash / squeeze) should not be faded by the weekly model.
    ssr_extreme = 0.0 if vix > 25 else max(0.0, min(1.0, (abs(score - 50) - 15) / 35.0))

    # How much exhaustion pressure accumulates each successive day (day 0 = none, day 4 = strong)
    _exhaust_weight = [0.0, 0.10, 0.30, 0.60, 0.90]

    # Magnitude decay — how far out we can project prices
    _mag_decay = [1.0, 0.85, 0.72, 0.60, 0.50]

    rows  = []
    price = base_price
    check = datetime.now(EST).date() + timedelta(days=1)
    added = 0

    while added < 5:
        if check.weekday() < 5:   # Mon–Fri only
            dow = check.weekday()
            d   = _mag_decay[added]
            ew  = _exhaust_weight[added]

            # Day-of-week modifier: amplifies or opposes SSR direction
            dow_factor = _DOW_TENDENCY.get(dow, 0.0)

            # Exhaustion pull: after a strong directional run, market reverts
            # e.g. score=25 (extreme bear) → by day 4, pull is +0.72 (strong bull reversion)
            exhaust_pull = -base_dir * ssr_extreme * ew * 0.8

            # Combined per-day direction
            day_dir = base_dir * (1.0 + dow_factor) + exhaust_pull
            day_dir = max(-1.0, min(1.0, day_dir))

            move  = round(daily_atr * day_dir * 0.65 * d, 1)
            price = round(price + move, 1)
            lo    = round(price - daily_atr * 0.55 * d, 1)
            hi    = round(price + daily_atr * 0.55 * d, 1)

            # Per-day bias icon — uses the adjusted direction, not raw SSR
            if   day_dir < -0.15: day_bias, icon = "bear",    "🔴"
            elif day_dir >  0.15: day_bias, icon = "bull",    "🟢"
            else:                 day_bias, icon = "neutral", "⚪"

            # Confidence = magnitude decay, reduced when DOW opposes SSR
            # and further reduced as exhaustion pressure builds
            dow_conflict  = 1.0 if (dow_factor * base_dir >= 0) else (1.0 - abs(dow_factor) * 0.8)
            conf_raw      = d * dow_conflict * (1.0 - ssr_extreme * ew * 0.45)
            conf_pct      = max(25, min(100, int(conf_raw * 100)))

            rows.append({
                "date":    check.strftime("%b %d"),
                "day":     check.strftime("%A"),
                "weekday": dow,
                "price":   price,
                "lo":      lo,
                "hi":      hi,
                "move":    move,
                "bias":    day_bias,
                "icon":    icon,
                "conf":    f"{conf_pct}%",
            })
            added += 1
        check += timedelta(days=1)
    return rows


def suggest_trade(score, levels):
    _, _, bias, _ = ssr_meta(score)
    c = levels["current"]; atr14 = levels["atr"]
    today = date.today()
    friday = today + timedelta(days=(4-today.weekday())%7)
    next_w = today + timedelta(days=7+(4-today.weekday())%7)
    if bias == "puts":
        # OTM put: nearest 5-point strike at or below current price (SPX near-term have 5pt strikes)
        strike = int(c / 5) * 5
        return {"direction":"PUT","strike":strike,
                "expiry": str(friday) if score<=35 else str(next_w),
                "entry": f"{levels['resistance_1']} – {levels['resistance_2']}",
                "target1":levels["target_down_1"],"target2":levels["target_down_2"],
                "stop":round(levels["resistance_2"]+atr14*0.5,1),
                "sizing":"2–4 contracts" if score<=35 else "1–2 contracts"}
    elif bias == "calls":
        # OTM call: nearest 5-point strike above current price (SPX near-term have 5pt strikes)
        strike = (int(c / 5) + 1) * 5
        return {"direction":"CALL","strike":strike,
                "expiry": str(friday) if score>=66 else str(next_w),
                "entry": f"{levels['support_1']} – {levels['support_2']}",
                "target1":levels["target_up_1"],"target2":levels["target_up_2"],
                "stop":round(levels["support_2"]-atr14*0.5,1),
                "sizing":"2–4 contracts" if score>=66 else "1–2 contracts"}
    return None


def change_html(val, pct):
    """Render price change with color."""
    if val is None:
        return '<span style="color:#64748b">—</span>'
    sign  = "+" if val >= 0 else ""
    color = "#4ade80" if val >= 0 else "#f87171"
    return f'<span style="color:{color};font-size:14px;font-weight:600">{sign}{val} ({sign}{pct}%)</span>'


@st.cache_data(ttl=60)
def compute_orb():
    """
    Opening Range Breakout — first 15 minutes (9:30–9:45 AM EST).
    The ORB high/low act as intraday breakout levels: price sustaining above ORB high
    after 10 AM = bull bias; below ORB low = bear bias; inside = chop/neutral.
    """
    try:
        df = yf.download("^GSPC", period="1d", interval="5m", progress=False, auto_adjust=True)
        if df.empty: return {"valid": False}
        df.index = df.index.tz_convert(EST)
        today = df.index[0].date()
        # Only today's bars
        df = df[df.index.date == today]
        # ORB = 9:30–9:44 (first 15 min — 3 × 5-min bars)
        orb = df[(df.index.hour == 9) & (df.index.minute < 45)]
        if len(orb) < 2: return {"valid": False}
        h = orb["High"].squeeze(); l = orb["Low"].squeeze()
        if isinstance(h, pd.DataFrame): h = h.iloc[:, 0]
        if isinstance(l, pd.DataFrame): l = l.iloc[:, 0]
        orb_high = round(float(h.max()), 1)
        orb_low  = round(float(l.min()), 1)
        # Current close
        cur_c = df["Close"].squeeze()
        if isinstance(cur_c, pd.DataFrame): cur_c = cur_c.iloc[:, 0]
        current = round(float(cur_c.iloc[-1]), 1)
        if current > orb_high:
            status = "above"
        elif current < orb_low:
            status = "below"
        else:
            status = "inside"
        return {"valid": True, "high": orb_high, "low": orb_low,
                "current": current, "status": status,
                "range_pts": round(orb_high - orb_low, 1)}
    except Exception:
        return {"valid": False}


@st.cache_data(ttl=86400)
def run_extended_window_backtest():
    """
    2-year hourly SPX backtest — validates each TIME_WINDOW bias against actual
    hourly direction, broken down by VIX regime and gap type.
    Uses 1-hour bars (max ~730 days on yfinance).
    Returns dict: window_label → {bias, total, correct, vix_low, vix_mid, vix_high,
                                   gap_up, gap_flat, gap_down, suggested_bias}
    """
    try:
        spx_1h = yf.download("^GSPC", period="2y", interval="1h",
                              progress=False, auto_adjust=True)
        if spx_1h.empty: return {}
        spx_1h.index = spx_1h.index.tz_convert(EST)

        # Daily VIX for regime tagging
        vix_d  = yf.download("^VIX",  period="2y", interval="1d",
                              progress=False, auto_adjust=True)
        vix_c  = vix_d["Close"].squeeze()
        if isinstance(vix_c, pd.DataFrame): vix_c = vix_c.iloc[:, 0]
        vix_map = {d.date(): float(v) for d, v in zip(vix_d.index, vix_c) if not pd.isna(v)}

        # Daily gap = open − prior close
        spx_d  = yf.download("^GSPC", period="2y", interval="1d",
                              progress=False, auto_adjust=True)
        _dc    = spx_d["Close"].squeeze()
        _do    = spx_d["Open"].squeeze()
        if isinstance(_dc, pd.DataFrame): _dc = _dc.iloc[:, 0]
        if isinstance(_do, pd.DataFrame): _do = _do.iloc[:, 0]
        gap_map = {spx_d.index[i].date(): round(float(_do.iloc[i]) - float(_dc.iloc[i-1]), 1)
                   for i in range(1, len(spx_d))}

        # Extract 1h close series
        close_1h = spx_1h["Close"].squeeze()
        if isinstance(close_1h, pd.DataFrame): close_1h = close_1h.iloc[:, 0]

        # Pre-compute rolling 20-period ATR on 1h closes for a stable chop threshold.
        # Single-bar proxy (abs(diff)*4) is circular and overstates chop accuracy.
        _h1 = spx_1h["High"].squeeze(); _l1 = spx_1h["Low"].squeeze()
        if isinstance(_h1, pd.DataFrame): _h1 = _h1.iloc[:, 0]
        if isinstance(_l1, pd.DataFrame): _l1 = _l1.iloc[:, 0]
        _tr1 = pd.concat([_h1 - _l1,
                          (_h1 - close_1h.shift()).abs(),
                          (_l1 - close_1h.shift()).abs()], axis=1).max(axis=1)
        _atr1h = _tr1.rolling(20).mean().fillna(_tr1)   # fallback to raw TR before 20 bars

        stats = {}
        for i in range(1, len(spx_1h)):
            try:
                ts   = spx_1h.index[i]
                dt   = ts.date()
                hhmm = ts.strftime("%H:%M")
                # RTH only
                if not (9 <= ts.hour < 16): continue

                # Resolve historical context BEFORE window_bias_at() call.
                # vix_val/gap_val were previously assigned after the call → NameError
                # on every iteration, making the 2-year table return empty stats.
                vix_val = vix_map.get(dt, 20.0)
                gap_val = gap_map.get(dt, 0.0)
                vix_key = ("vix_high" if vix_val > VIX_FEAR_THRESHOLD
                           else "vix_low" if vix_val < VIX_CALM_THRESHOLD
                           else "vix_mid")
                gap_key = ("gap_up"   if gap_val > GAP_THRESHOLD else
                           "gap_down" if gap_val < -GAP_THRESHOLD else "gap_flat")

                # Match window — use window_bias_at() with fully resolved historical context.
                _hist_dt_str = dt.strftime("%Y-%m-%d")
                _hist_evts   = {ev[2] for ev in _ECON_CAL if ev[0] == _hist_dt_str}
                _is_opex_h   = is_opex_week(dt)
                mbias, mlabel = window_bias_at(
                    hhmm, gap=gap_val, vix=vix_val,
                    event_types=_hist_evts, weekday=dt.weekday(),
                    opex=_is_opex_h, atr=0.0)  # no historical ATR map; skip ratio logic
                if mlabel == "Outside Hours": continue

                prev_c = float(close_1h.iloc[i-1])
                curr_c = float(close_1h.iloc[i])
                diff   = curr_c - prev_c
                # Chop threshold: 25% of rolling 20-period hourly ATR.
                # More stable than single-bar proxy which was circular (used same bar's move).
                _hatr  = float(_atr1h.iloc[i]) if not pd.isna(_atr1h.iloc[i]) else 5.0
                _flat  = abs(diff) < _hatr * 0.25

                if   mbias == "bull":  correct = diff > 0
                elif mbias == "bear":  correct = diff < 0
                elif mbias == "chop":  correct = _flat
                else: continue

                if mlabel not in stats:
                    stats[mlabel] = {
                        "bias": mbias, "correct": 0, "total": 0,
                        "vix_low":  {"c": 0, "t": 0},
                        "vix_mid":  {"c": 0, "t": 0},
                        "vix_high": {"c": 0, "t": 0},
                        "gap_up":   {"c": 0, "t": 0},
                        "gap_flat": {"c": 0, "t": 0},
                        "gap_down": {"c": 0, "t": 0},
                    }
                s = stats[mlabel]
                s["total"] += 1
                if correct: s["correct"] += 1
                s[vix_key]["t"] += 1
                if correct: s[vix_key]["c"] += 1
                s[gap_key]["t"] += 1
                if correct: s[gap_key]["c"] += 1
            except Exception:
                continue

        # Add data-driven bias suggestion per window
        for lbl, s in stats.items():
            t = s["total"]
            if t < 20:
                s["suggested_bias"] = s["bias"]
                s["flip"] = False
                continue
            acc = s["correct"] / t
            # If accuracy < 40%, the current bias is likely wrong — suggest flip
            if acc < 0.40:
                flip_map = {"bull": "bear", "bear": "bull", "chop": "chop"}
                s["suggested_bias"] = flip_map.get(s["bias"], s["bias"])
                s["flip"] = True
            else:
                s["suggested_bias"] = s["bias"]
                s["flip"] = False
        return stats
    except Exception:
        return {}


def windows_html(now_hhmm, win_acc=None, cur_vix=0.0, cur_gap=0.0):
    """Render window strip. Shows regime-specific hit rate when cur_vix/cur_gap are known."""
    rows = []
    # Determine current regime keys to look up context-specific accuracy
    _vix_key = ("vix_high" if cur_vix > VIX_FEAR_THRESHOLD
                else "vix_low" if (0 < cur_vix < VIX_CALM_THRESHOLD)
                else "vix_mid")
    _gap_key = ("gap_up"   if cur_gap > GAP_THRESHOLD
                else "gap_down" if cur_gap < -GAP_THRESHOLD
                else "gap_flat")
    _regime_known = cur_vix > 0

    for s, e, lbl, b in TIME_WINDOWS:
        is_now = s <= now_hhmm < e
        now_badge = '<span class="win-now">NOW</span>' if is_now else ""
        row_style = 'background:#1a2744;border-radius:6px;' if is_now else ""
        acc_badge = ""
        # Look up by full label first (exact override match); fall back to base label.
        # When multiple override variants share the same base label (e.g. "Morning Trend
        # (hi-VIX→bear)" vs "Morning Trend (hi-VIX→chop)"), prefer the variant whose
        # key contains the current VIX regime suffix so the badge reflects the actual
        # live regime, not a random first match (peer review finding #5).
        _lookup_key = lbl if (win_acc and lbl in win_acc) else None
        if _lookup_key is None and win_acc:
            _candidates = [(k, v) for k, v in win_acc.items() if v.get("base_label") == lbl]
            if _candidates:
                # Build ordered list of regime suffixes to try — most specific first.
                # Covers all labels emitted by window_bias_at():
                #   gap-confirmed→chop  (hi-VIX + gap_confirmed + large gap-up, before 11:30)
                #   catalyst-confirmed  (hi-VIX + bull bias + gap_catalyst_aligned)
                #   hi-VIX→bear/chop/reversal risk/gap-dn bounce zone
                #   lo-VIX→chop  (VIX < 18 calm regime)
                #   gap-up→chop, gap-down→chop, gap-dn bounce zone
                # The two gap-specific hi-VIX variants are listed before the generic "hi-VIX"
                # bucket so the most specific historical bucket is preferred when available.
                _sfxs = []
                if cur_vix > VIX_FEAR_THRESHOLD:
                    # Prepend the two hi-VIX+gap variants that window_bias_at() can emit.
                    # These are mutually exclusive (chop vs bull), so at most one will match
                    # any given candidate set. Order: gap-confirmed first (chop), then
                    # catalyst-confirmed (bull), then fall through to generic hi-VIX.
                    if cur_gap > GAP_THRESHOLD:
                        _sfxs.extend(["gap-confirmed", "catalyst-confirmed"])
                    _sfxs.append("hi-VIX")
                elif 0 < cur_vix < VIX_CALM_THRESHOLD:
                    _sfxs.append("lo-VIX")
                if cur_gap > GAP_THRESHOLD:
                    _sfxs.append("gap-up")
                elif cur_gap < -GAP_THRESHOLD:
                    _sfxs.extend(["gap-down", "gap-dn"])
                _regime_match = None
                for _sfx in _sfxs:
                    _regime_match = next((k for k, _ in _candidates if _sfx in k), None)
                    if _regime_match:
                        break
                _lookup_key = _regime_match if _regime_match else _candidates[0][0]
        if win_acc and _lookup_key and _lookup_key in win_acc:
            _ws  = win_acc[_lookup_key]
            _tot = _ws.get("total", 0)
            if _tot >= 20:
                _avg_pct = round(_ws["correct"] / _tot * 100)
                # Prefer regime-specific hit rate when current VIX+gap are known
                _regime_pct = None
                if _regime_known:
                    _vk = _ws.get(_vix_key, {})
                    _gk = _ws.get(_gap_key, {})
                    # Use whichever sub-regime has enough samples (prefer VIX since it's more powerful)
                    if _vk.get("t", 0) >= 10:
                        _regime_pct = round(_vk["c"] / _vk["t"] * 100)
                    elif _gk.get("t", 0) >= 10:
                        _regime_pct = round(_gk["c"] / _gk["t"] * 100)
                if _regime_pct is not None:
                    _rc = "#4ade80" if _regime_pct >= 60 else ("#f87171" if _regime_pct < 45 else "#94a3b8")
                    _ac = "#475569"
                    _regime_lbl = _vix_key.replace("vix_","VIX ").replace("_"," ") if _vk.get("t",0) >= 10 else _gap_key.replace("_"," ")
                    acc_badge = (f'<span class="win-acc" style="color:{_rc};font-weight:700">{_regime_pct}%</span>'
                                 f'<span class="win-acc" style="color:{_ac};font-size:9px"> ({_avg_pct}% avg)</span>')
                else:
                    _ac = "#4ade80" if _avg_pct >= 60 else ("#f87171" if _avg_pct < 45 else "#94a3b8")
                    acc_badge = f'<span class="win-acc" style="color:{_ac}">{_avg_pct}% avg</span>'
        rows.append(
            f'<div class="window-row" style="{row_style}">'
            f'<span class="win-time">{to_ampm(s)}–{to_ampm(e)}</span>'
            f'<span class="win-label">{BIAS_COLOR.get(b,"⚪")} {lbl}</span>'
            f'{now_badge}{acc_badge}</div>'
        )
    return "".join(rows)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="SPX Algo", page_icon="📈", layout="wide")

st.markdown("""
<style>
  body, .main { background:#0f1117; color:#f1f5f9; }
  .block-container { padding-top:1rem; padding-left:1.5rem; padding-right:1.5rem; }
  .card {
    background:#1e2130; border-radius:12px; padding:18px 20px;
    margin-bottom:14px; border:1px solid #2d3250; height:100%;
  }
  .card h3 { margin:0 0 10px 0; font-size:11px; letter-spacing:1.5px;
              text-transform:uppercase; color:#64748b; font-weight:600; }
  .metric-tile {
    background:#1e2130; border-radius:10px; padding:12px 16px;
    border:1px solid #2d3250; text-align:center;
  }
  .metric-label { font-size:10px; color:#64748b; letter-spacing:1px;
                  text-transform:uppercase; margin-bottom:4px; }
  .metric-val   { font-size:22px; font-weight:800; line-height:1; }
  .metric-sub   { font-size:11px; margin-top:3px; }
  .ssr-score    { font-size:64px; font-weight:800; line-height:1; }
  .level-row    { display:flex; justify-content:space-between;
                  padding:4px 0; border-bottom:1px solid #2d3250; font-size:13px; }
  .level-row:last-child { border-bottom:none; }
  .level-label  { color:#94a3b8; }
  .level-val    { font-weight:600; color:#f1f5f9; }
  .window-row   { display:flex; align-items:center; gap:8px;
                  padding:5px 6px; font-size:13px; border-bottom:1px solid #161b2e; }
  .window-row:last-child { border-bottom:none; }
  .win-time     { color:#64748b; width:130px; flex-shrink:0; font-family:monospace; font-size:11px; }
  .win-label    { flex:1; font-size:13px; }
  .win-now      { background:#1e3a8a; border-radius:4px; padding:1px 6px;
                  font-size:10px; color:#93c5fd; font-weight:700; }
  .win-acc      { background:#1c2135; border-radius:3px; padding:1px 5px;
                  font-size:10px; color:#64748b; margin-left:auto; flex-shrink:0; }
  .sig-grid     { display:grid; grid-template-columns:1fr 1fr; gap:0; }
  .sig-row      { display:flex; justify-content:space-between; align-items:center;
                  padding:3px 6px; font-size:12px; border-bottom:1px solid #1a1f33; }
  .sig-row:last-child { border-bottom:none; }
  .trade-field  { display:flex; justify-content:space-between; padding:5px 0; font-size:14px; }
  .tf-label     { color:#64748b; }
  .tf-val       { font-weight:600; }
  .divider      { border:none; border-top:1px solid #2d3250; margin:10px 0; }
  .proj-table   { width:100%; border-collapse:collapse; color:#f1f5f9; font-size:13px; }
  .proj-table th { padding:6px 8px; text-align:left; font-size:10px; color:#64748b;
                   letter-spacing:.8px; background:#0f1117; position:sticky; top:0; }
  .proj-table td { padding:5px 8px; }
</style>
""", unsafe_allow_html=True)

# ── AUTO-REFRESH every 60 seconds ────────────────────────────────────────────
# Injects a hidden JS timer that reloads the Streamlit page.
# Combined with fetch_live() ttl=60 and fetch_data() ttl=300, every reload
# gets fresh ES/SPX prices and fresh SSR every 5th reload.
_REFRESH_SECS = 60
_components.html(f"""
<script>
(function() {{
  var remaining = {_REFRESH_SECS};
  var badge = window.parent.document.getElementById('spx-refresh-badge');
  var tick = setInterval(function() {{
    remaining--;
    if (badge) badge.textContent = remaining + 's';
    if (remaining <= 0) {{
      clearInterval(tick);
      window.parent.location.reload();
    }}
  }}, 1000);
}})();
</script>
""", height=0)

# ── DATA ─────────────────────────────────────────────────────────────────────
now_est  = datetime.now(EST)
now_hhmm = now_est.strftime("%H:%M")

with st.spinner("Fetching market data..."):
    spx, vix, pcr, sectors = fetch_data()
    today_events = get_todays_events(lookahead_days=4)
    live = fetch_live()
    orb_data = compute_orb()
    macro_data = fetch_macro_signals()

# Guard against empty VIX data (yfinance can return empty on Streamlit Cloud).
# Fall back to 20.0 (neutral VIX) so the app keeps running and shows a warning.
try:
    _vix_c = vix["Close"].squeeze() if not vix.empty else pd.Series(dtype=float)
    if isinstance(_vix_c, pd.DataFrame): _vix_c = _vix_c.iloc[:, 0]
    _vix_c = _vix_c.dropna()
    vix_now = round(float(_vix_c.iloc[-1]), 2) if len(_vix_c) > 0 else 20.0
except Exception:
    vix_now = 20.0  # safe neutral fallback — VIX unavailable

# Live pre-market VIX: CBOE publishes VIX starting ~3:15 AM ET.
# After 3 AM, fetch 1-minute intraday VIX to replace the stale prior-day close.
# Cached 60s — same as app refresh — so it stays current through the pre-open session.
@st.cache_data(ttl=120)
def _fetch_premarket_vix():
    try:
        _v = yf.download("^VIX", period="1d", interval="1m", progress=False, auto_adjust=True)
        if _v.empty:
            return None
        _c = _v["Close"].squeeze()
        if isinstance(_c, pd.DataFrame): _c = _c.iloc[:, 0]
        _c = _c.dropna()
        if len(_c) == 0:
            return None
        return round(float(_c.iloc[-1]), 2)
    except Exception:
        return None

_now_est_vix = datetime.now(EST)
_vix_premarket_active = (
    _now_est_vix.weekday() < 5 and
    (_now_est_vix.hour > 3 or (_now_est_vix.hour == 3 and _now_est_vix.minute >= 15)) and
    _now_est_vix.hour < 16
)
if _vix_premarket_active:
    _live_vix = _fetch_premarket_vix()
    if _live_vix and _live_vix > 0:
        vix_now = _live_vix

# Data quality flags — shown as badges in the UI so traders know which signals are live
_pcr_ok       = not pcr.empty
_sector_count = sum(1 for df in sectors.values() if not df.empty)
_sector_total = len(sectors)  # 11

# Load news with live VIX so VIX-conditional items (jobs data) score correctly
news_data = load_news(vix_now=vix_now)

# Enrich macro_data with overnight ES range position from live feed.
# overnight_pos: 0.0 = at overnight low, 1.0 = at overnight high.
# Live-only — not available in backtest paths (those skip macro entirely).
if live.get("overnight_pos") is not None:
    macro_data["overnight_pos"] = live["overnight_pos"]

# NQ/ES divergence and ES pre-market momentum from live feed.
# overnight_range_atr is injected later (after levels = compute_levels) since it needs ATR.
if live.get("nq_es_div") is not None:
    macro_data["nq_es_div"] = live["nq_es_div"]
if live.get("es_mom_30m_pct") is not None:
    macro_data["es_mom_30m_pct"] = live["es_mom_30m_pct"]

_base_score, buys, sells, signals = compute_ssr(spx, vix, pcr, sectors, macro=macro_data)

# ── Intraday RSI override: replace daily RSI signals with 5-min RSI during RTH ──
# Daily RSI is computed on close-to-close bars; by mid-afternoon it reflects
# yesterday's close and is hours stale. 5-min RSI captures live momentum.
_intra_rsi = fetch_intraday_rsi()
_is_rth_now = (now_est.weekday() < 5 and
               now_est.hour < 16 and
               not (now_est.hour < 9 or (now_est.hour == 9 and now_est.minute < 30)))
if _intra_rsi is not None and _is_rth_now:
    signals["RSI Above 50"]   = int(_intra_rsi > 50)
    signals["RSI Trend Zone"] = int(45 <= _intra_rsi <= 65)
    # Recompute buys/sells to reflect override
    buys  = sum(1 for v in signals.values() if v == 1)
    sells = sum(1 for v in signals.values() if v == 0)

# ── OpEx detection ────────────────────────────────────────────────────────────
_opex_week   = is_opex_week()
_opex_friday = is_opex_friday()

# ── Data-driven SSR group weights from recent backtest performance ───────────
# Each signal group is weighted by how well it correlated with actual SPX
# direction over the last 60 trading days.  Groups with >70% hit rate get
# boosted (up to 1.8×); groups with <50% hit rate get penalised (down to 0.4×).
@st.cache_data(ttl=3600, show_spinner=False)
def compute_group_weights(today_date=None):  # today_date param IS the cache key — different date = new cache entry = midnight bust
    """Derive per-group weights by correlating each group's vote with actual day direction.

    Calibration sample: last 60 trading days from daily data (not constrained to ~20 5m days).
    Actual direction: 5m intraday (close vs open) when available, daily close-to-close fallback.
    Neutral threshold: days where SPX moved < 5 pts are skipped — flat days are not reliable
    training signal and previously forced all near-flat sessions into bear (−1).
    """
    try:
        _spx_d, _vix_d, _sec_d, _day_s, _days_5m, _ = load_backtest_data()
        _dl = list(_spx_d.index.date)
        _td = len(_spx_d)
        if _td < 20:
            return {g: 1.0 for g in SIGNAL_GROUPS}

        _gc = {g: 0.0 for g in SIGNAL_GROUPS}
        _gt = {g: 0.0 for g in SIGNAL_GROUPS}

        # Iterate the last 60 daily bars (not just 5m days which is ~20).
        # Recent 20 days count 2×; older days count 1× (regime decay weighting).
        _eval_days = _dl[max(0, _td - 61): _td - 1]  # exclude today (no next-bar outcome yet)
        for _i, _day in enumerate(_eval_days):
            _day_weight = 2.0 if _i >= len(_eval_days) - 20 else 1.0
            try:
                _pos = _dl.index(_day)
                _off = _td - _pos
                _sb  = _spx_d.iloc[:-_off] if _off > 0 else _spx_d
                # Use strict-less-than so target-day VIX and sector closes are excluded.
                # Using <= previously leaked same-day data into calibration (peer review finding).
                _vb  = _vix_d[_vix_d.index < _spx_d.index[_pos]]
                _eb  = {k: v[v.index < _spx_d.index[_pos]] for k, v in _sec_d.items()}

                # Always use next-day close-to-close for calibration target.
                # Previously mixed intraday (close-open) vs close-to-close targets depending
                # on 5m availability — blending two different horizons into one sample made
                # group weights unstable and harder to interpret (peer review finding).
                # Close-to-close matches the exporter's validation target exactly.
                if _pos + 1 < _td:
                    _this_c = float(_spx_d["Close"].squeeze().iloc[_pos])
                    _next_c = float(_spx_d["Close"].squeeze().iloc[_pos + 1])
                    _day_move = _next_c - _this_c
                else:
                    continue

                # Skip flat days — moves < 5 pts are noise, not reliable training signal.
                # Previously these were forced into bear (−1), biasing weights in bear markets.
                if abs(_day_move) < 5.0:
                    continue
                _act = 1 if _day_move > 0 else -1

                _as_of = EST.localize(datetime(_day.year, _day.month, _day.day, 12, 0))
                _, _, _, _sigs = compute_ssr(_sb, _vb, pd.DataFrame(), _eb, as_of_dt=_as_of)
                for _gn, _gs in SIGNAL_GROUPS.items():
                    _pr = [_sigs.get(k, 0) for k in _gs if k in _sigs]
                    if not _pr: continue
                    _vote = 1 if (sum(_pr) / len(_pr)) > 0.5 else -1
                    _gt[_gn] += _day_weight
                    if _vote == _act: _gc[_gn] += _day_weight
            except Exception:
                continue

        # acc → weight: 50%=0.7, 60%=1.3, 70%=1.9 (capped at 2.0); <50%→0.3 floor
        # Require effective n >= 10 (sum of weights) before trusting accuracy.
        _out = {}
        for _gn in SIGNAL_GROUPS:
            _t = _gt[_gn]
            if _t < 10.0:
                _out[_gn] = 1.0
            else:
                _acc = _gc[_gn] / _t
                _out[_gn] = round(max(0.3, min(2.0, (_acc - 0.5) * 6.0 + 0.7)), 2)
        return _out
    except Exception:
        return {g: 1.0 for g in SIGNAL_GROUPS}

@st.cache_data(ttl=86400)
def compute_historical_analysis():
    """
    2-year daily walk-forward study (Priority 2 + 3).
    For each trading day from bar 200 onward:
      - Compute core SSR (closed-bar signals only) on that day's historical slice
      - Compare predicted direction (score ≥55=bull, ≤44=bear) to next-day SPX move
      - Bin by VIX regime / gap regime / weekday / event day / OpEx week
      - Ablation: for each core signal, measure accuracy with vs without it
    Returns: {regime, ablation, baseline_hits, baseline_total}
    All computations use equal group weights (1.0) for stability across cache windows.
    """
    try:
        _spx = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
        _vix = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
        _sec = {}
        for _t in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
            try:
                _sec[_t] = yf.download(_t, period="2y", interval="1d", progress=False, auto_adjust=True)
            except Exception:
                _sec[_t] = pd.DataFrame()

        _close = _spx["Close"].squeeze()
        _open  = _spx["Open"].squeeze()
        if isinstance(_close, pd.DataFrame): _close = _close.iloc[:, 0]
        if isinstance(_open,  pd.DataFrame): _open  = _open.iloc[:, 0]
        _n = len(_spx)

        # Helper: compute weighted group score from a signals dict (equal weights)
        def _grp_score(sigs_dict):
            _ws, _ww = [], []
            for _gn, _gs in SIGNAL_GROUPS.items():
                _pr = [sigs_dict[k] for k in _gs if k in sigs_dict]
                if _pr:
                    _ws.append(sum(_pr) / len(_pr))
                    _ww.append(1.0)
            return round(sum(_ws) / len(_ws) * 100) if _ws else 50

        # Regime accumulators
        _regime = {
            "vix":   {"low":{"h":0,"t":0}, "mid":{"h":0,"t":0}, "high":{"h":0,"t":0}},
            "gap":   {"up": {"h":0,"t":0}, "flat":{"h":0,"t":0}, "down": {"h":0,"t":0}},
            "dow":   {d: {"h":0,"t":0} for d in range(5)},
            "event": {"event":{"h":0,"t":0}, "normal":{"h":0,"t":0}},
            "opex":  {"opex":{"h":0,"t":0},  "normal":{"h":0,"t":0}},
        }
        _base_h = 0; _base_t = 0
        # Ablation: per core signal — hits with all signals vs hits without this signal
        _core_sigs = [s for s, tr in SIGNAL_TIERS.items() if tr == "core"]
        # Ablation tracks:
        #   t      = total directional calls made WITH this signal (denominator)
        #   h_all  = correct directional calls WITH this signal
        #   h_excl = correct directional calls WITHOUT this signal (same denominator)
        #   t_excl = directional calls that remain directional WITHOUT this signal
        #            (used for coverage rate: t_excl/t = fraction of calls preserved)
        _abl = {s: {"h_all":0, "h_excl":0, "t":0, "t_excl":0} for s in _core_sigs}

        for _i in range(200, _n - 1):
            try:
                _spx_sl = _spx.iloc[:_i + 1]
                _vix_sl = _vix.iloc[:_i + 1]
                _dt  = _spx.index[_i].date()
                # Align sector slices by date (not row index) to avoid misalignment
                # when sector history is shorter or has different trading day count.
                _cutoff_ts = pd.Timestamp(_dt)
                _sec_sl = {k: v[v.index <= _cutoff_ts]
                           for k, v in _sec.items() if not v.empty}
                _aof = EST.localize(datetime(_dt.year, _dt.month, _dt.day, 12, 0))
                _sc, _, _, _sigs = compute_ssr(_spx_sl, _vix_sl, pd.DataFrame(), _sec_sl, as_of_dt=_aof)

                # Use equal-weight group score for core signals only (ablation-consistent)
                _core_sc = _grp_score({k: v for k, v in _sigs.items() if SIGNAL_TIERS.get(k) == "core"})

                _nxt = float(_close.iloc[_i + 1])
                _cur = float(_close.iloc[_i])
                _up  = _nxt > _cur + 5      # >5pt = directional bull
                _dn  = _nxt < _cur - 5      # >5pt drop = directional bear
                _bull_call = _core_sc >= 55
                _bear_call = _core_sc <= 44
                if not _bull_call and not _bear_call:
                    continue   # neutral call — no directional claim, skip
                _correct = (_bull_call and _up) or (_bear_call and _dn)

                _base_t += 1
                if _correct: _base_h += 1

                # VIX regime
                _vv = float(_vix_sl["Close"].squeeze().iloc[-1]) if not _vix_sl.empty else 20
                _vk = "high" if _vv > 25 else ("low" if _vv < 18 else "mid")
                _regime["vix"][_vk]["t"] += 1
                if _correct: _regime["vix"][_vk]["h"] += 1

                # Gap regime (today open vs prior close)
                if _i > 0:
                    _gp = float(_open.iloc[_i]) - float(_close.iloc[_i - 1])
                    _gk = "up" if _gp > GAP_THRESHOLD else ("down" if _gp < -GAP_THRESHOLD else "flat")
                    _regime["gap"][_gk]["t"] += 1
                    if _correct: _regime["gap"][_gk]["h"] += 1

                # Weekday
                _wd = _spx.index[_i].weekday()
                _regime["dow"][_wd]["t"] += 1
                if _correct: _regime["dow"][_wd]["h"] += 1

                # Event day
                _dt_s = _dt.strftime("%Y-%m-%d")
                _ek = "event" if any(ev[0] == _dt_s for ev in _ECON_CAL) else "normal"
                _regime["event"][_ek]["t"] += 1
                if _correct: _regime["event"][_ek]["h"] += 1

                # OpEx week
                _ok = "opex" if is_opex_week(_dt) else "normal"
                _regime["opex"][_ok]["t"] += 1
                if _correct: _regime["opex"][_ok]["h"] += 1

                # Signal ablation — recompute core score without each signal.
                # Only count rows where the FULL model made a directional call.
                # If removing the signal makes the score neutral → record as coverage
                # loss (t_excl stays at t, but we skip adding to h_excl for that row).
                for _sig in _core_sigs:
                    if _sig not in _sigs: continue
                    _sigs_excl = {k: v for k, v in _sigs.items()
                                  if SIGNAL_TIERS.get(k) == "core" and k != _sig}
                    _sc_excl   = _grp_score(_sigs_excl)
                    _bull_excl = _sc_excl >= 55
                    _bear_excl = _sc_excl <= 44
                    _neutral_excl = not _bull_excl and not _bear_excl
                    _abl[_sig]["t"]     += 1          # always: full model was directional
                    _abl[_sig]["h_all"] += 1 if _correct else 0
                    if not _neutral_excl:
                        # Excluded model still makes a call — measure accuracy
                        _c_excl = (_bull_excl and _up) or (_bear_excl and _dn)
                        _abl[_sig]["h_excl"] += 1 if _c_excl else 0
                        _abl[_sig]["t_excl"] += 1
                    # else: removing signal → neutral → coverage loss; h_excl unchanged
            except Exception:
                continue

        return {
            "regime": _regime,
            "ablation": _abl,
            "baseline_hits": _base_h,
            "baseline_total": _base_t,
        }
    except Exception:
        return {}


@st.cache_data(ttl=3600)
def _signal_drift_check(n_days: int = 10, flag_threshold: float = 0.70):
    """
    Scan each core signal over the last n_days completed trading days.

    A signal is "drifting" when its direction has been WRONG on ≥ flag_threshold
    fraction of recent evaluable days:
      • stuck_bull  — signal = 1 (bullish) but next-day SPX fell > 5 pts
      • stuck_bear  — signal = 0 (bearish) but next-day SPX rose  > 5 pts

    Only days where the outcome is unambiguous (±5 pt threshold) are counted.
    Returns a list of dicts sorted by wrong_pct desc:
      [{name, wrong_days, total_days, wrong_pct, direction}, ...]
    """
    try:
        _spx = yf.download("^GSPC", period="60d", interval="1d",
                           progress=False, auto_adjust=True)
        _vix = yf.download("^VIX",  period="60d", interval="1d",
                           progress=False, auto_adjust=True)
        _sec = {}
        for _t in ["XLF", "XLK", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]:
            try:
                _sec[_t] = yf.download(_t, period="60d", interval="1d",
                                        progress=False, auto_adjust=True)
            except Exception:
                _sec[_t] = pd.DataFrame()

        _close = _spx["Close"].squeeze()
        if isinstance(_close, pd.DataFrame): _close = _close.iloc[:, 0]
        _n = len(_spx)
        if _n < n_days + 15:
            return []

        # Per-signal trackers: {sig_name: {"wrong": 0, "total": 0, "stuck": ""}}
        _core_sigs = [s for s, tr in SIGNAL_TIERS.items() if tr == "core"]
        _tracker   = {s: {"wrong": 0, "total": 0, "stuck": ""} for s in _core_sigs}

        # We evaluate on the last n_days bars that have a "next day" outcome
        _eval_start = _n - 1 - n_days   # bar index of first evaluation day
        if _eval_start < 30:
            _eval_start = 30             # need at least 30 bars for reliable indicators

        for _i in range(_eval_start, _n - 1):
            _spx_sl = _spx.iloc[:_i + 1]
            _vix_sl = _vix.iloc[:_i + 1]
            _dt     = _spx.index[_i].date()
            _cutoff = pd.Timestamp(_dt)
            _sec_sl = {k: v[v.index <= _cutoff] for k, v in _sec.items() if not v.empty}
            _aof    = EST.localize(datetime(_dt.year, _dt.month, _dt.day, 12, 0))

            try:
                _, _, _, _sigs = compute_ssr(_spx_sl, _vix_sl, pd.DataFrame(), _sec_sl,
                                             as_of_dt=_aof)
            except Exception:
                continue

            _nxt = float(_close.iloc[_i + 1])
            _cur = float(_close.iloc[_i])
            _next_up = _nxt > _cur + 5    # clear bull outcome
            _next_dn = _nxt < _cur - 5    # clear bear outcome
            if not _next_up and not _next_dn:
                continue   # ambiguous day — skip to avoid noise

            for _sig in _core_sigs:
                if _sig not in _sigs:
                    continue
                _val = _sigs[_sig]   # 1 = bullish call, 0 = bearish call
                _wrong = False
                _dir   = ""
                if _val == 1 and _next_dn:       # signal said bull, market fell
                    _wrong = True; _dir = "stuck_bull"
                elif _val == 0 and _next_up:     # signal said bear, market rose
                    _wrong = True; _dir = "stuck_bear"
                _tracker[_sig]["total"] += 1
                if _wrong:
                    _tracker[_sig]["wrong"] += 1
                    if not _tracker[_sig]["stuck"]:
                        _tracker[_sig]["stuck"] = _dir

        # Collect drifting signals
        _drifting = []
        for _sig, _d in _tracker.items():
            if _d["total"] < 5:
                continue   # not enough evaluable days — skip
            _pct = _d["wrong"] / _d["total"]
            if _pct >= flag_threshold:
                _drifting.append({
                    "name":       _sig,
                    "wrong_days": _d["wrong"],
                    "total_days": _d["total"],
                    "wrong_pct":  round(_pct * 100),
                    "direction":  _d["stuck"],
                })

        _drifting.sort(key=lambda x: x["wrong_pct"], reverse=True)
        return _drifting
    except Exception:
        return []


_grp_weights = compute_group_weights(today_date=now_est.date())
# Stamp when weights were computed so the UI can show a version, not silently recalculate
_grp_weights_ts = now_est.strftime("%b %d %I:%M %p")   # frozen for this session (1h cache)

# ── Levels and price feeds needed before scoring ─────────────────────────────
levels  = compute_levels(spx)
es_price   = live["es_price"]  or levels["current"]
spx_price  = live["spx_price"] or levels["current"]
# Guard: if daily SPX data failed, levels["current"] = 0.
# Patch with live spx_price so gap, delta, and signal calcs don't use 0 as prior close.
if levels["current"] <= 0 and spx_price > 0:
    levels["current"] = spx_price

# ── Pre-market implied gap injection (MUST happen before SSR scoring) ────────
# Outside RTH (before 9:30, after 16:00, weekend): SPX open hasn't happened.
# Implied gap = ES price − last SPX close. Feeds projections + gap overrides.
_implied_gap     = round(es_price - levels["current"], 1) if live["es_price"] else 0.0
_implied_gap_pct = round(_implied_gap / levels["current"] * 100, 2) if levels["current"] else 0.0
_pre_market      = not _is_rth_now
live_gap         = 0.0  # default; overwritten below when ES is live or RTH gap is known
# Inject implied gap into Gap/ATR Normal BEFORE scoring so score is gap-aware.
# A small gap-up (<0.5 ATR) fires 1; a large gap-up or any gap-down fires 0.
if _pre_market and live["es_price"]:
    live_gap = _implied_gap
    _daily_atr_val = levels["atr"]
    if _daily_atr_val > 0:
        _impl_gap_atr = _implied_gap / _daily_atr_val
        signals["Gap/ATR Normal"] = int(0.0 <= _impl_gap_atr < 0.5)
        buys  = sum(1 for v in signals.values() if v == 1)
        sells = sum(1 for v in signals.values() if v == 0)

# ── Overnight range quality (needs levels["atr"] — injected here post-levels) ──
# Compressed range (<0.30× ATR) = breakout pending; expanded (>0.70×) = exhaustion lean.
_on_high_v = live.get("overnight_high")
_on_low_v  = live.get("overnight_low")
if _on_high_v is not None and _on_low_v is not None and levels["atr"] > 0:
    _ora = round((_on_high_v - _on_low_v) / levels["atr"], 3)
    macro_data["overnight_range_atr"] = _ora
    signals["Overnight Range Compressed"] = int(_ora < 0.30)
    signals["Overnight Range Expanded"]   = int(_ora > 0.70)

# ── NQ/ES divergence and ES pre-market momentum (direct signal injection) ────
# These are already in macro_data but need a direct signals[] write so the
# re-scorer below picks them up without another full compute_ssr pass.
if live.get("nq_es_div") is not None:
    _div = live["nq_es_div"]
    signals["NQ Bull Divergence"] = int(_div >  0.15)
    signals["NQ Bear Divergence"] = int(_div < -0.15)
if live.get("es_mom_30m_pct") is not None:
    _ep = live["es_mom_30m_pct"]
    signals["ES Pre-Market Momentum Bull"] = int(_ep >  0.05)
    signals["ES Pre-Market Momentum Bear"] = int(_ep < -0.05)

# ── Drift dampening: neutralize persistently-wrong signals before re-score ────
# _signal_drift_check() is defined below; its @st.cache_data wrapper means
# this call is near-free after the first invocation in this session (1h TTL).
# Drifting signals are set to 0.5 (abstain) so they stop pulling the group
# score in the wrong direction.  The original binary value is preserved in
# the UI display — only the scoring copy (signals dict) is modified here.
try:
    _live_drift_flags = _signal_drift_check(n_days=10, flag_threshold=0.70)
    _drift_dampened_names = set()
    for _ldf in _live_drift_flags:
        _ldsig = _ldf["name"] if isinstance(_ldf, dict) else _ldf
        if _ldsig in signals:
            signals[_ldsig] = 0.5       # abstain — not 0 (bear) or 1 (bull)
            _drift_dampened_names.add(_ldsig)
except Exception:
    _live_drift_flags    = []
    _drift_dampened_names = set()

# ── Re-score SSR using data-driven group weights ─────────────────────────────
# (Gap/ATR Normal and drift dampening are both applied before this block runs)
_wg_s, _wg_w = [], []
for _gn, _gs in SIGNAL_GROUPS.items():
    _pr = [signals.get(k, 0) for k in _gs if k in signals]
    if _pr:
        _w = _grp_weights.get(_gn, 1.0)
        _wg_s.append((sum(_pr) / len(_pr)) * _w)
        _wg_w.append(_w)
_weighted_base = round(sum(_wg_s) / sum(_wg_w) * 100) if _wg_s else _base_score

# ── News sentiment nudge: causal-chain weighted composite → ±10 SSR pts ─────
_news_comp  = news_data.get("composite_score", 0.0)
# Cap scales with top event weight: weight ≥ 4.0 (Hormuz/US-Iran war) allows ±8 pts
_top_news_wt = news_data.get("top_impact", {}) or {}
_top_wt      = _top_news_wt.get("weight", 1.0)
_nudge_cap   = 8 if _top_wt >= 4.0 else (6 if _top_wt >= 3.0 else 5)
_news_nudge  = max(-_nudge_cap, min(_nudge_cap, int(round(_news_comp * _nudge_cap))))
score        = max(0, min(100, _weighted_base + _news_nudge))
# ── end nudge ───────────────────────────────────────────────────────────────

# ── Core SSR: weighted score from backtestable closed-bar signals only ───────
# Excludes session context (Gap/ATR Normal) and live-only signals (PCR, macro, overnight).
# NOTE ON MODEL ALIGNMENT: the standalone backtest exporter (scripts/backtest_export.py)
# validates an equal-weight static clone of these 28 signals.  The live Core SSR below
# uses dynamic per-group weights (compute_group_weights) and drift dampening applied
# above — meaning the two paths can diverge in trending regimes.  Do not treat
# exporter accuracy numbers as a full validation of this dynamically-weighted score.
# Live-Adj SSR = score (above) includes all signals for the richest real-time estimate.
_core_wg_s, _core_wg_w = [], []
for _gn, _gs in SIGNAL_GROUPS.items():
    _cpr = [signals.get(k, 0) for k in _gs if SIGNAL_TIERS.get(k) == "core" and k in signals]
    if _cpr:
        # Equal weights (1.0) for all groups so Core SSR is directly comparable to the
        # equal_weight_static_core exporter accuracy numbers (peer review finding #3).
        # Live-Adj SSR (score) still uses dynamic _grp_weights + news nudge for richer real-time use.
        _cw = 1.0
        _core_wg_s.append((sum(_cpr) / len(_cpr)) * _cw)
        _core_wg_w.append(_cw)
_core_ssr      = round(sum(_core_wg_s) / sum(_core_wg_w) * 100) if _core_wg_s else _weighted_base
_live_adj_delta = score - _core_ssr   # positive = live overlay is bullish; negative = bearish overlay

# ── Score driver narrative: top 2 bullish drivers + top drag group ────────
_grp_pct = {}
for _gn, _gs in SIGNAL_GROUPS.items():
    _pr = [signals.get(k, 0) for k in _gs if k in signals]
    if _pr:
        _grp_pct[_gn] = round(sum(_pr) / len(_pr) * 100)
# Sort by weighted contribution (score% × group_weight)
_grp_sorted = sorted(_grp_pct.items(),
                     key=lambda x: x[1] * _grp_weights.get(x[0], 1.0), reverse=True)
_top_drivers  = [f"{g} ({p}%)" for g, p in _grp_sorted[:2] if p >= 50]
_top_drags    = [f"{g} ({p}%)" for g, p in reversed(_grp_sorted) if p < 40][:1]
_driver_line  = ("Top drivers: " + " · ".join(_top_drivers)) if _top_drivers else ""
_drag_line    = ("Drag: " + ", ".join(_top_drags)) if _top_drags else ""

# ── Shadow ledger write path REMOVED (was Path 1) ────────────────────────────
# This early write path has been removed (peer review finding #6).
# It wrote actual_dir as "U"/"D" which is not recognized by the display logic
# (which expects "bull"/"bear"/"flat"). Because both paths shared a duplicate-date
# guard, the early writer would block the canonical Path 2 (line ~5245) from
# ever writing that date, and _ledger_fill_actuals() would skip "U"/"D" rows
# since they are non-empty. The unified write is now Path 2 only, which writes
# actual_dir="" and lets _ledger_fill_actuals() normalize retroactively.

es_display = f"{es_price:,.2f}" if live["es_price"] else "—"
spx_display= f"{spx_price:,.2f}" if live["spx_price"] else f"{levels['current']:,.1f}"

rating, action, bias, color = ssr_meta(score)
trade   = suggest_trade(score, levels)
cur_win, cur_bias, cur_start, cur_end = get_current_window()

# Projected SPX open = last close + implied gap (fallback if overnight anchor not yet computed)
_proj_spx_open = round(levels["current"] + _implied_gap, 1) if _pre_market and live["es_price"] else None

# Countdown to next 6 PM ES open (shown pre-market)
_next_open_dt = next_es_open(datetime.now(EST))
_mins_to_open = int((_next_open_dt - datetime.now(EST)).total_seconds() / 60)

# Key level proximity — warn when SPX is within 15 pts of a major level
_watch_levels = {
    "R2": levels["resistance_2"], "R1": levels["resistance_1"],
    "Pivot": levels["pivot"],
    "S1": levels["support_1"],   "S2": levels["support_2"],
}
_nearest_name, _nearest_val = min(_watch_levels.items(),
                                   key=lambda x: abs(spx_price - x[1]))
_nearest_dist = round(abs(spx_price - _nearest_val), 1)
_proximity_alert = (
    _nearest_dist <= 15 and spx_price > 0 and _nearest_val > 0
)

BIAS_BG   = {"bull":"#14532d","bear":"#7f1d1d","chop":"#1e293b","neutral":"#1e293b"}
BIAS_TEXT = {"bull":"#4ade80","bear":"#f87171","chop":"#94a3b8","neutral":"#94a3b8"}

# ── HEADER ───────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
  <div>
    <h1 style="margin:0;font-size:24px;font-weight:800">📈 SPX Algo</h1>
    <p style="margin:2px 0 0;color:#64748b;font-size:12px">Player224-style · Signal Strength Rating · Trade Plan · Projections</p>
  </div>
  <div style="text-align:right;color:#64748b;font-size:12px">
    {now_est.strftime('%A, %B %d %Y')} &nbsp;·&nbsp;
    <b style="color:#94a3b8">{now_est.strftime('%I:%M %p EST')}</b>
    &nbsp;·&nbsp;
    <span style="background:#1e2130;border:1px solid #2d3250;border-radius:5px;
                 padding:2px 8px;font-size:11px;color:#64748b">
      🔄 refresh in <b id="spx-refresh-badge" style="color:#94a3b8">{_REFRESH_SECS}s</b>
    </span>
  </div>
</div>
<div style="background:#1a1f35;border:1px solid #2d3250;border-left:3px solid #f59e0b;
            border-radius:6px;padding:6px 14px;margin-bottom:6px;font-size:11px;color:#94a3b8">
  ⚠️ <b style="color:#f59e0b">Educational model only.</b>
  The SSR score and projections are algorithmic outputs — <b>not financial advice.</b>
  Past backtest accuracy does not guarantee future results.
  All trading decisions are solely your responsibility.
</div>
""", unsafe_allow_html=True)

# ── UI Trust Surface ──────────────────────────────────────────────────────────
# Compact status bar showing data freshness, model version, and provider health.
# Investors and operators can see at a glance: is the data live? which model?
# when was the backtest run? are all providers healthy?
_es_status_color = "#4ade80" if live.get("es_price") else "#f87171"
_es_status_txt   = f"ES live @ {live['es_ts']}" if live.get("es_price") and live.get("es_ts") else "ES stale"
_spx_status_color = "#4ade80" if live.get("spx_price") else "#f59e0b"
_spx_status_txt   = f"SPX @ {live['spx_ts']}" if live.get("spx_price") and live.get("spx_ts") else "SPX delayed"
_sectors_ok       = _sector_count == _sector_total
_sector_status_color = "#4ade80" if _sectors_ok else "#f59e0b"
_sector_status_txt   = f"Sectors {_sector_count}/{_sector_total}" + (" ✓" if _sectors_ok else " ⚠")
_vix_status_color = "#4ade80" if vix_now and vix_now > 0 else "#f87171"
_vix_status_txt   = f"VIX {vix_now}" if vix_now and vix_now > 0 else "VIX unavail"
_model_ver  = "SSR-v3 · 29 core signals · Core=equal-wt / Live-Adj=dynamic"
_weights_ts = _grp_weights_ts

def _trust_chip(label, color, title=""):
    return (
        f'<span title="{title}" style="background:#0f1117;border:1px solid {color}44;'
        f'border-radius:4px;padding:2px 7px;font-size:10px;color:{color};'
        f'white-space:nowrap">{label}</span>'
    )

_trust_html = (
    '<div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;'
    'margin-bottom:12px;padding:5px 10px;background:#0d1117;border:1px solid #1e293b;'
    'border-radius:6px">'
    f'<span style="font-size:9px;color:#475569;letter-spacing:.8px;margin-right:4px">PROVIDER STATUS</span>'
    + _trust_chip(_es_status_txt,   _es_status_color,   "ES Futures last bar timestamp")
    + _trust_chip(_spx_status_txt,  _spx_status_color,  "SPX last price timestamp")
    + _trust_chip(_vix_status_txt,  _vix_status_color,  "VIX fear index")
    + _trust_chip(_sector_status_txt, _sector_status_color, "Sector data coverage")
    + f'<span style="font-size:9px;color:#334155;margin:0 4px">│</span>'
    + _trust_chip(f"Model: {_model_ver}", "#475569", "Scoring model version and backtest scope")
    + _trust_chip(f"Weights: {_weights_ts}", "#334155", "Group weight cache timestamp (refreshes hourly)")
    + '</div>'
)
st.markdown(_trust_html, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 1 — METRICS STRIP  (8 tiles)
# ═══════════════════════════════════════════════════════════════════════════════
def metric_tile(label, value, sub="", val_color="#f1f5f9", sub_color="#94a3b8"):
    return f"""
    <div class="metric-tile">
      <div class="metric-label">{label}</div>
      <div class="metric-val" style="color:{val_color}">{value}</div>
      {"" if not sub else f'<div class="metric-sub" style="color:{sub_color}">{sub}</div>'}
    </div>"""

def chg_color(val): return "#4ade80" if val and val >= 0 else "#f87171"
def chg_str(val, pct):
    if val is None: return "—"
    s = "+" if val >= 0 else ""
    return f"{s}{val} ({s}{pct}%)"

es_chg_color  = chg_color(live["es_change"])
spx_chg_color = chg_color(live["spx_change"])
win_icon = BIAS_COLOR.get(cur_bias, "⚪")
ts1 = live.get("es_ts") or "delayed"
ts2 = live.get("spx_ts") or "delayed"

mc1,mc2,mc3,mc4,mc5,mc6,mc7,mc8,mc9 = st.columns(9)

# SSR Action: split into short direction + conviction subtitle
# e.g. "HIGH CONVICTION PUTS" → big="PUTS", conv="HIGH CONVICTION"
#      "PUTS — STANDARD"      → big="PUTS", conv="STANDARD"
#      "NO EDGE — WAIT"       → big="WAIT", conv="NO EDGE"
_act_parts = action.split("—")
if len(_act_parts) == 2:
    _act_conv, _act_dir = _act_parts[0].strip(), _act_parts[1].strip()
elif "PUTS" in action:
    _act_dir  = "PUTS"
    _act_conv = action.replace("PUTS","").strip()
elif "CALLS" in action:
    _act_dir  = "CALLS"
    _act_conv = action.replace("CALLS","").strip()
else:
    _act_dir  = action
    _act_conv = ""

_news_lbl = news_data["label"].split()[-1]   # "Bullish" / "Bearish" / "Neutral"
_news_vc  = "#4ade80" if "Bullish" in news_data["label"] else ("#f87171" if "Bearish" in news_data["label"] else "#94a3b8")
_news_sub = f'{news_data["bull_pct"]}% bull · {news_data["bear_pct"]}% bear'

# Intraday RSI display: show 5-min RSI during RTH, fall back to daily
_rsi_display = f"{_intra_rsi}" if (_intra_rsi is not None and _is_rth_now) else str(levels['rsi'])
_rsi_label   = "RSI (5m)" if (_intra_rsi is not None and _is_rth_now) else "RSI (1d)"
_rsi_vc      = "#4ade80" if float(_rsi_display) > 55 else ("#f87171" if float(_rsi_display) < 45 else "#94a3b8")

# OpEx: replace "ES Last Tick" tile with OpEx context when active
_mc4_lbl = "OpEx Week" if _opex_week else "ES Last Tick"
_mc4_val = ("3rd Fri" if _opex_friday else "Active") if _opex_week else ts1
_mc4_sub = "⚡ Exp. Friday" if _opex_friday else ("Pin + Unwind" if _opex_week else "ES=F  24×5")
_mc4_vc  = "#f59e0b" if _opex_week else "#94a3b8"
_mc4_sc  = "#f59e0b" if _opex_friday else ("#64748b" if _opex_week else "#475569")

# ── Pre-market banner: implied gap + countdown when outside RTH ──────────
# _es_rth_anchor (overnight-drift-adjusted projected open) is computed ~260 lines
# below after live_gap and ORB vars are ready. Reserve a placeholder here so the
# banner stays visually above the metrics tiles; fill it after anchor is known.
# Initialize banner vars with safe defaults so the fill block never hits NameError
# even if the condition logic diverges in future refactors.
_gap_color      = "#f59e0b"   # neutral amber default
_gap_regime_lbl = "FLAT OPEN"
_open_label     = "—"
_banner_placeholder = st.empty()
if _pre_market and live["es_price"]:
    _gap_color  = "#f87171" if _implied_gap < -GAP_THRESHOLD else ("#4ade80" if _implied_gap > GAP_THRESHOLD else "#f59e0b")
    _gap_regime_lbl = ("GAP DOWN" if _implied_gap < -GAP_THRESHOLD
                       else "GAP UP" if _implied_gap > GAP_THRESHOLD else "FLAT OPEN")
    # If ES is already trading (past 6 PM), show countdown to RTH open (9:30 AM) instead
    _now_est_pm = datetime.now(EST)
    _is_es_trading_now = is_es_active(_now_est_pm)
    if _is_es_trading_now:
        _next_rth = _now_est_pm.replace(hour=9, minute=30, second=0, microsecond=0)
        if _now_est_pm >= _next_rth or _now_est_pm.weekday() >= 5:
            _next_rth += timedelta(days=1)
            while _next_rth.weekday() >= 5:
                _next_rth += timedelta(days=1)
        _mins_to_rth = int((_next_rth - _now_est_pm).total_seconds() / 60)
        _open_label = f"{_mins_to_rth}m to RTH Open (9:30)"
    else:
        _open_label = (f"{_mins_to_open}m to ES Open" if _mins_to_open > 0
                       else "ES Session Active")

for col, lbl, val, sub, vc, sc, fsize in [
    (mc1, "Pre-Mkt SSR" if _pre_market else "Live-Adj SSR", str(score), f"Core: {_core_ssr} &nbsp;·&nbsp; {rating.split()[0]}",  color,  "#94a3b8", "22px"),
    (mc2, "SSR Action",   _act_dir,        f"<span style='font-size:9px;letter-spacing:.5px'>{_act_conv}</span>&nbsp; {buys}✅{sells}❌", color, "#64748b", "20px"),
    (mc3, "ES Futures",   es_display,      chg_str(live["es_change"],live["es_pct"]),  "#f1f5f9", es_chg_color,  "18px"),
    (mc4, _mc4_lbl,       _mc4_val,        _mc4_sub,                                  _mc4_vc,  _mc4_sc,       "13px"),
    (mc5, "SPX",          spx_display,     chg_str(live["spx_change"],live["spx_pct"]),"#f1f5f9", spx_chg_color, "18px"),
    (mc6, "VIX",          str(vix_now),    "Fear Index",                               "#f59e0b" if vix_now>20 else "#4ade80", "#64748b", "22px"),
    (mc7, _rsi_label,     _rsi_display,    f"ATR: {levels['atr']}",                   _rsi_vc,   "#64748b",     "22px"),
    (mc8, "News",         _news_lbl,       _news_sub,                                  _news_vc,  "#64748b",     "16px"),
    (mc9, "Now",          win_icon,        cur_win[:18],                               BIAS_TEXT.get(cur_bias,"#94a3b8"), "#64748b", "24px"),
]:
    with col:
        st.markdown(f'<div class="metric-tile"><div class="metric-label">{lbl}</div>'
                    f'<div class="metric-val" style="color:{vc};font-size:{fsize};line-height:1.15">{val}</div>'
                    f'<div class="metric-sub" style="color:{sc};margin-top:3px">{sub}</div></div>',
                    unsafe_allow_html=True)

st.markdown("<div style='margin-bottom:6px'></div>", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 2 — SSR DETAIL | INTRADAY WINDOWS | KEY LEVELS + TRADE PLAN
# ═══════════════════════════════════════════════════════════════════════════════
# ── Regime-aware accuracy badge (shown in SSR card) ──────────────────────────
# Pull the current VIX+gap regime accuracy from the historical analysis so the
# investor can see: "In past days like today (hi-VIX gap-up), model was right X%"
_regime_acc_html = ""
try:
    _ha_live = compute_historical_analysis()
    if _ha_live:
        _vk_live = "high" if vix_now > VIX_FEAR_THRESHOLD else ("low" if vix_now < VIX_CALM_THRESHOLD else "mid")
        _vk_lbl  = {"high": "Hi-VIX", "mid": "Mid-VIX", "low": "Lo-VIX"}[_vk_live]
        _vk_data = _ha_live["regime"]["vix"].get(_vk_live, {"h": 0, "t": 0})
        _gk_live = "up" if live_gap > GAP_THRESHOLD else ("down" if live_gap < -GAP_THRESHOLD else "flat")
        _gk_lbl  = {"up": "Gap-Up", "down": "Gap-Dn", "flat": "Flat"}[_gk_live]
        _gk_data = _ha_live["regime"]["gap"].get(_gk_live, {"h": 0, "t": 0})
        def _acc_badge(lbl, d):
            if d["t"] < 10: return ""
            pct = int(d["h"] / d["t"] * 100)
            c   = "#4ade80" if pct >= 60 else ("#f59e0b" if pct >= 50 else "#f87171")
            return (f'<span style="background:#0f1117;border:1px solid {c}33;border-radius:4px;'
                    f'padding:2px 7px;font-size:10px;color:{c}">'
                    f'{lbl}: <b>{pct}%</b> <span style="color:#475569">n={d["t"]}</span></span>')
        _vk_badge = _acc_badge(_vk_lbl, _vk_data)
        _gk_badge = _acc_badge(_gk_lbl, _gk_data)
        if _vk_badge or _gk_badge:
            _regime_acc_html = (
                f'<div style="margin:4px 0 6px">'
                f'<div style="font-size:9px;color:#475569;letter-spacing:.8px;margin-bottom:4px">REGIME ACCURACY (2yr backtest)</div>'
                f'<div style="display:flex;gap:5px;flex-wrap:wrap">{_vk_badge}{_gk_badge}</div>'
                f'</div>'
            )
except Exception:
    pass

# ── Multi-timeframe alignment warning ────────────────────────────────────────
# Daily SSR vs weekly trend (200 SMA proxy). A bear SSR below a bullish 200 SMA
# = counter-trend trade — lower conviction. Flag it visibly on the score card.
_mtf_warning_html = ""
try:
    _above_200  = signals.get("Above 200 SMA", 1)     # 1 = price above 200 SMA (weekly bull)
    _above_50   = signals.get("Above 50 SMA",  1)     # 1 = medium-term trend bull
    _daily_bull = score >= 55
    _daily_bear = score <= 44
    _weekly_bull = bool(_above_200)
    _weekly_bear = not bool(_above_200)
    if _daily_bear and _weekly_bull:
        _mtf_warning_html = (
            '<div style="background:#1c1408;border:1px solid #854d0e;border-radius:5px;'
            'padding:5px 8px;font-size:10px;color:#fbbf24;margin:4px 0">'
            '⚠️ <b>Counter-trend signal</b> — SSR is bearish but price is above 200 SMA '
            '(weekly trend is bullish). Lower conviction on downside calls.</div>'
        )
    elif _daily_bull and _weekly_bear:
        _mtf_warning_html = (
            '<div style="background:#1a0a0a;border:1px solid #7f1d1d;border-radius:5px;'
            'padding:5px 8px;font-size:10px;color:#fca5a5;margin:4px 0">'
            '⚠️ <b>Counter-trend signal</b> — SSR is bullish but price is below 200 SMA '
            '(weekly trend is bearish). Lower conviction on upside calls.</div>'
        )
    elif _daily_bull and _weekly_bull and _above_50:
        _mtf_warning_html = (
            '<div style="background:#0d1f0d;border:1px solid #166534;border-radius:5px;'
            'padding:5px 8px;font-size:10px;color:#86efac;margin:4px 0">'
            '✅ <b>Aligned</b> — SSR bullish + above 50 & 200 SMA (all timeframes agree).</div>'
        )
except Exception:
    pass

# ── VIX-implied expected day range ────────────────────────────────────────────
# ATR × VIX-scaling factor gives the probabilistic day range expectation.
# Shown on the banner so traders can size positions before the open.
_exp_range_html = ""
try:
    if levels["atr"] > 0 and spx_price > 0:
        import numpy as _np
        _vx_scale  = float(_np.interp(vix_now, [0, 20, 25, 30, 35, 100], [1.0, 1.15, 1.35, 1.60, 2.0, 2.0]))
        _exp_range = round(levels["atr"] * _vx_scale, 0)
        _mid_price = _proj_spx_open if _proj_spx_open else spx_price
        _exp_lo    = round(_mid_price - _exp_range / 2, 0)
        _exp_hi    = round(_mid_price + _exp_range / 2, 0)
        _exp_range_html = (
            f'<span style="font-size:11px;color:#94a3b8"> &nbsp;·&nbsp; '
            f'Exp range: <b style="color:#f59e0b">{int(_exp_lo):,}–{int(_exp_hi):,}</b>'
            f' <span style="color:#475569">({int(_exp_range)} pts · ATR×{_vx_scale:.2f})</span></span>'
        )
except Exception:
    pass

# ── Signal drift monitor ──────────────────────────────────────────────────────
# Flag any core signal that has been WRONG on 7+ of the last 10 evaluable days.
# "Wrong" = signal said bull (1) but market fell >5 pts the next day, or
#           signal said bear (0) but market rose >5 pts the next day.
# Drifting signals get a red-amber alert box inside the SSR card so the analyst
# knows the signal is temporarily unreliable and should be discounted.
_drift_alert_html = ""
try:
    _drifting_sigs = _signal_drift_check(n_days=10, flag_threshold=0.70)
    if _drifting_sigs:
        _drift_rows = ""
        for _ds in _drifting_sigs:
            _stuck_lbl = ("📈 stuck bull" if _ds["direction"] == "stuck_bull"
                          else "📉 stuck bear")
            _drift_rows += (
                f'<div style="display:flex;justify-content:space-between;'
                f'border-bottom:1px solid #3f1515;padding:2px 0">'
                f'<span style="color:#fca5a5">{_ds["name"]}</span>'
                f'<span style="color:#f87171">{_ds["wrong_days"]}/{_ds["total_days"]} wrong '
                f'({_ds["wrong_pct"]}%) &nbsp; <span style="color:#9ca3af">{_stuck_lbl}</span></span>'
                f'</div>'
            )
        _drift_alert_html = (
            '<div style="background:#1f0808;border:1px solid #7f1d1d;border-radius:6px;'
            'padding:7px 10px;margin:6px 0;font-size:10px">'
            '<div style="color:#fca5a5;font-weight:700;letter-spacing:.5px;margin-bottom:4px">'
            '⚠️ SIGNAL DRIFT ALERT (last 10 days)</div>'
            f'{_drift_rows}'
            '<div style="color:#6b7280;font-size:9px;margin-top:5px">'
            'These signals have been systematically wrong recently. '
            'Treat their contribution to SSR with lower weight until drift resolves.</div>'
            '</div>'
        )
except Exception:
    pass

cL, cM, cR = st.columns([1, 1.5, 1.5])

# ── Left: SSR Detail ──────────────────────────────────────────────────────────
with cL:
    st.markdown(f"""
    <div class="card">
      <h3>SSR — Signal Strength Rating</h3>
      <div class="ssr-score" style="color:{color}">{score}</div>
      <div style="font-size:11px;color:#94a3b8;margin:2px 0 8px">out of 100</div>
      <div style="background:#2d3250;border-radius:6px;height:8px;margin-bottom:10px">
        <div style="background:{color};width:{score}%;height:8px;border-radius:6px"></div>
      </div>
      <div style="font-size:14px;font-weight:700;color:{color}">{rating}</div>
      <div style="font-size:12px;color:#94a3b8;margin:3px 0 6px">{action}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:8px">
        <div style="background:#0f1f0f;border-radius:6px;padding:5px 8px;text-align:center">
          <div style="font-size:9px;color:#64748b;letter-spacing:.8px">CORE SSR</div>
          <div style="font-size:18px;font-weight:800;color:{'#22c55e' if _core_ssr>=55 else '#ef4444' if _core_ssr<=44 else '#94a3b8'}">{_core_ssr}</div>
          <div style="font-size:9px;color:#475569">backtested · 28 signals</div>
        </div>
        <div style="background:#0f1117;border-radius:6px;padding:5px 8px;text-align:center;border:1px solid #1e3a5f">
          <div style="font-size:9px;color:#64748b;letter-spacing:.8px">LIVE-ADJ SSR</div>
          <div style="font-size:18px;font-weight:800;color:{color}">{score}</div>
          <div style="font-size:9px;color:#475569">+session/live overlay
            <span style="color:{'#4ade80' if _live_adj_delta>0 else '#f87171' if _live_adj_delta<0 else '#64748b'}">
              ({'+' if _live_adj_delta>=0 else ''}{_live_adj_delta})
            </span>
          </div>
        </div>
      </div>
      <div style="font-size:10px;color:#475569;margin-bottom:4px">
        Base: {_base_score} → Wt: {_weighted_base} &nbsp;·&nbsp;
        News: <span style="color:{'#4ade80' if _news_nudge>0 else '#f87171' if _news_nudge<0 else '#64748b'}">{'+' if _news_nudge>0 else ''}{_news_nudge}</span>
        &nbsp;·&nbsp; Final: <b style="color:{color}">{score}</b>
      </div>
      {(f'<div style="font-size:10px;color:#4ade80;margin-bottom:3px">▲ {_driver_line}</div>') if _driver_line else ''}
      {(f'<div style="font-size:10px;color:#f87171;margin-bottom:3px">▼ {_drag_line}</div>') if _drag_line else ''}
      <div style="font-size:9px;margin-bottom:5px;display:flex;gap:5px;flex-wrap:wrap">
        {'' if _pcr_ok else '<span style="background:#7f1d1d;color:#fca5a5;padding:1px 5px;border-radius:3px;font-size:9px">⚠ PCR unavailable</span>'}
        {'' if _sector_count == _sector_total else f'<span style="background:#1c1f2e;color:#f59e0b;padding:1px 5px;border-radius:3px;font-size:9px">⚠ Sectors: {_sector_count}/{_sector_total}</span>'}
      </div>
      {(f'<div style="background:#1c1408;border:1px solid #854d0e;border-radius:5px;padding:4px 8px;font-size:10px;color:#fbbf24;margin-bottom:6px">⚡ {_nearest_dist} pts from <b>{_nearest_name}</b> ({_nearest_val:,})</div>') if _proximity_alert else ''}
      {("" if not (_intra_rsi is not None and _is_rth_now) else
        f'<div style="font-size:9px;color:{"#4ade80" if _intra_rsi>50 else "#f87171"};margin-bottom:4px">'
        f'📡 Intraday RSI (5m): {_intra_rsi} — live signal active</div>')}
      <div style="font-size:9px;color:#374151;margin-bottom:8px">
        {"&nbsp;".join(f'<span style="color:{"#4ade80" if w>=1.2 else "#f87171" if w<=0.6 else "#64748b"}">{g[:4]}:{w}×</span>'
          for g, w in _grp_weights.items())}
      <div style="font-size:9px;color:#374151;margin-top:3px">
        Weights v{_grp_weights_ts} · 252d backtest · frozen 1h
      </div>
      </div>
      {_regime_acc_html}
      {_mtf_warning_html}
      {_drift_alert_html}
      <hr class="divider">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;margin-bottom:10px">
        <div style="background:#0d2010;border-radius:6px;padding:6px 8px;text-align:center">
          <div style="color:#64748b;font-size:10px">BUY SIGNALS</div>
          <div style="color:#22c55e;font-size:20px;font-weight:800">{buys}</div>
        </div>
        <div style="background:#200d0d;border-radius:6px;padding:6px 8px;text-align:center">
          <div style="color:#64748b;font-size:10px">SELL SIGNALS</div>
          <div style="color:#ef4444;font-size:20px;font-weight:800">{sells}</div>
        </div>
      </div>
      <hr class="divider">
      <h3 style="margin-bottom:6px">SSR Scale</h3>
      <div style="font-size:11px;line-height:1.9;color:#94a3b8">
        <div><span style="color:#b91c1c">■</span> 0–35 &nbsp;Strong Sell — Puts</div>
        <div><span style="color:#ef4444">■</span> 36–44 Sell — Puts std</div>
        <div><span style="color:#6b7280">■</span> 45–54 Neutral — Wait</div>
        <div><span style="color:#22c55e">■</span> 55–65 Weak Buy — Calls</div>
        <div><span style="color:#15803d">■</span> 66–100 Strong Buy — Calls</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ── Middle: Single Intraday Windows card ──────────────────────────────────────
with cM:
    now_badge_html = f"""
    <div style="background:{BIAS_BG.get(cur_bias,'#1e293b')};border-radius:8px;
                padding:8px 12px;margin-bottom:10px;display:flex;
                justify-content:space-between;align-items:center">
      <div>
        <div style="font-size:11px;color:#64748b;letter-spacing:1px">NOW</div>
        <div style="font-size:16px;font-weight:700;color:{BIAS_TEXT.get(cur_bias,'#94a3b8')}">
          {win_icon} {cur_win}
        </div>
      </div>
      <div style="text-align:right;font-size:12px;color:#64748b">
        {to_ampm(cur_start)} – {to_ampm(cur_end)}
      </div>
    </div>"""
    _live_win_acc = run_extended_window_backtest()
    st.markdown(f"""
    <div class="card">
      <h3>Intraday Windows — ES &amp; SPX (EST)</h3>
      {now_badge_html}
      {windows_html(now_hhmm, win_acc=_live_win_acc, cur_vix=vix_now, cur_gap=live_gap)}
    </div>
    """, unsafe_allow_html=True)

_orb_status = orb_data.get("status", "inside") if orb_data.get("valid") else "inside"
# ORB width / daily ATR — used to guard narrow ORB breakout signals.
_orb_range_atr = (round(orb_data["range_pts"] / max(levels["atr"], 1), 3)
                  if orb_data.get("valid") and levels.get("atr", 0) > 0 else 0.0)
# ORB distance / daily ATR — how far price has traveled beyond the ORB edge.
if orb_data.get("valid") and levels.get("atr", 0) > 0:
    _cur = orb_data["current"]; _atr_ref = max(levels["atr"], 1)
    if _orb_status == "above":
        _orb_distance_atr = round((_cur - orb_data["high"]) / _atr_ref, 3)
    elif _orb_status == "below":
        _orb_distance_atr = round((orb_data["low"] - _cur) / _atr_ref, 3)
    else:
        _orb_distance_atr = 0.0
else:
    _orb_distance_atr = 0.0

# ── Right: Key Levels (top) + Trade Plan (bottom) ─────────────────────────────
with cR:
    # Key Levels — compact 2-column grid layout
    def lrow(label, val, vc="#f1f5f9"):
        return f'<div class="level-row"><span class="level-label">{label}</span><span class="level-val" style="color:{vc}">{val}</span></div>'

    # ── Key Levels card ──
    st.markdown(f"""
    <div class="card" style="margin-bottom:8px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 20px">
        <div>
          <h3 style="margin-bottom:8px">Resistance</h3>
          {lrow("R3", levels['resistance_3'], "#f87171")}
          {lrow("R2", levels['resistance_2'], "#f87171")}
          {lrow("R1", levels['resistance_1'], "#f87171")}
          <div class="level-row" style="background:#2d3250;border-radius:4px;padding:3px 6px">
            <span class="level-label">📍 Pivot</span>
            <span class="level-val">{levels['pivot']}</span>
          </div>
          {lrow("S1", levels['support_1'], "#4ade80")}
          {lrow("S2", levels['support_2'], "#4ade80")}
          {lrow("S3", levels['support_3'], "#4ade80")}
        </div>
        <div>
          <h3 style="margin-bottom:8px">Targets &amp; Stats</h3>
          {lrow("&uarr; Target 1", levels['target_up_1'])}
          {lrow("&uarr; Target 2", levels['target_up_2'])}
          {lrow("&darr; Target 1", levels['target_down_1'])}
          {lrow("&darr; Target 2", levels['target_down_2'])}
          <hr class="divider">
          {lrow("Week High", levels['week_high'])}
          {lrow("Week Low",  levels['week_low'])}
          {lrow("Prev High", levels['prev_high'])}
          {lrow("Prev Low",  levels['prev_low'])}
          <hr class="divider">
          <h3 style="margin-bottom:6px">ORB (9:30–9:44)</h3>
          {(lambda o: lrow("ORB High", o["high"], "#f87171") + lrow("ORB Low", o["low"], "#4ade80") +
            lrow("Status",
                 f'{"↑ Above" if o["status"]=="above" else "↓ Below" if o["status"]=="below" else "Inside"}',
                 "#4ade80" if o["status"]=="above" else "#f87171" if o["status"]=="below" else "#94a3b8") +
            lrow("Range", f'{o["range_pts"]} pts  ({round(o["range_pts"]/max(levels["atr"],1)*100)}% ATR)',
                 "#f59e0b" if _orb_range_atr >= 0.12 else "#475569") +
            (lrow("Dist/ATR", f'{_orb_distance_atr:.2f}× ATR {"↑" if o["status"]=="above" else "↓"}',
                  "#4ade80" if o["status"]=="above" else "#f87171")
             if o["status"] in ("above","below") and _orb_distance_atr > 0 else "")
          )(orb_data) if orb_data.get("valid") else lrow("ORB", "Pre-market / unavailable", "#475569")}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Trade Plan card — separate call to avoid f-string interpolation issues ──
    if trade:
        dc = "#ef4444" if trade["direction"] == "PUT" else "#22c55e"
        st.markdown(
            f'<div class="card">'
            f'<h3 style="margin-bottom:8px">Trade Plan</h3>'
            f'<div style="font-size:20px;font-weight:800;color:{dc};margin-bottom:12px">'
            f'SPX {trade["strike"]}{trade["direction"][0]} &nbsp;&middot;&nbsp; {trade["expiry"]}'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:14px;border-bottom:1px solid #2d3250">'
            f'<span style="color:#64748b">Entry Zone</span><span style="font-weight:600">{trade["entry"]}</span></div>'
            f'<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:14px;border-bottom:1px solid #2d3250">'
            f'<span style="color:#64748b">Target 1</span><span style="font-weight:600;color:#4ade80">{trade["target1"]}</span></div>'
            f'<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:14px;border-bottom:1px solid #2d3250">'
            f'<span style="color:#64748b">Target 2</span><span style="font-weight:600;color:#4ade80">{trade["target2"]}</span></div>'
            f'<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:14px;border-bottom:1px solid #2d3250">'
            f'<span style="color:#64748b">Stop Loss</span><span style="font-weight:600;color:#f87171">{trade["stop"]}</span></div>'
            f'<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:14px">'
            f'<span style="color:#64748b">Size</span><span style="font-weight:600">{trade["sizing"]}</span></div>'
            f'</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div class="card"><h3 style="margin-bottom:8px">Trade Plan</h3>'
            '<div style="color:#64748b;font-size:13px;text-align:center;padding:16px 0">'
            '⚪ Neutral — No edge today. Wait for clearer setup.</div></div>',
            unsafe_allow_html=True
        )

# ── Compute live_gap, prior_close, and ORB status at module level
#    (used in Why This Bias, projections, and live accuracy — before tabs)
# Pre-market: use ES-implied gap (already set at line ~2308); do NOT overwrite with
# yesterday's session gap (open-prev_close) which is a stale completed bar.
try:
    _spx_open_  = spx["Open"].squeeze()
    _spx_close_ = spx["Close"].squeeze()
    if isinstance(_spx_open_,  pd.DataFrame): _spx_open_  = _spx_open_.iloc[:,  0]
    if isinstance(_spx_close_, pd.DataFrame): _spx_close_ = _spx_close_.iloc[:, 0]
    if len(_spx_open_) >= 2 and len(_spx_close_) >= 2:
        _rth_gap             = round(float(_spx_open_.iloc[-1]) - float(_spx_close_.iloc[-2]), 1)
        _session_prior_close = float(_spx_close_.iloc[-2])
    else:
        _rth_gap             = 0.0
        _session_prior_close = spx_price
except Exception:
    _rth_gap             = 0.0
    _session_prior_close = spx_price
# Use implied ES gap when pre-market; RTH session open gap otherwise
if _pre_market and live["es_price"]:
    live_gap = _implied_gap          # already set above; re-assert to be explicit
else:
    live_gap = _rth_gap
# ── Gap confirmation: after 9:45 AM during RTH, check whether the gap is holding.
# If the live SPX price is still ≥ prior close + 80% of the implied gap, the gap
# has NOT been faded and morning bear calls should be suppressed (chop not bear).
# This prevents the hi-VIX regime from projecting a selloff on a day when the
# market is clearly holding its overnight gains.
_gap_confirmed = (
    _is_rth_now and
    live_gap > GAP_THRESHOLD and
    (now_est.hour > 9 or (now_est.hour == 9 and now_est.minute >= 45)) and
    spx_price > levels["current"] + live_gap * 0.80
)

# Pre-compute ES projections at module level so the pre-market banner and the
# SPX projection table share the same overnight-drift-adjusted anchor price.
# Guard: skip if ATR is 0 (data failure) to avoid zero-width projection ranges.
if levels["atr"] > 0 and es_price > 0:
    _es_rows_precomp = generate_es_projections(
        es_price, levels["atr"], score, gap=live_gap, vix=vix_now,
        news_score=_news_comp, orb_status=_orb_status, opex=_opex_week,
        orb_range_atr=_orb_range_atr, orb_distance_atr=_orb_distance_atr,
        gap_confirmed=_gap_confirmed)
else:
    _es_rows_precomp = []
_es_rth_anchor = None
if _pre_market and live["es_price"] and _es_rows_precomp:
    for _ei, _er in enumerate(_es_rows_precomp):
        if _er.get("session") == "RTH":
            if _ei > 0:
                _es_rth_anchor = _es_rows_precomp[_ei - 1]["price"]
            break
# Now fill the pre-market banner placeholder with the overnight-adjusted anchor.
if _pre_market and live["es_price"]:
    _proj_open_display = _es_rth_anchor if _es_rth_anchor is not None else _proj_spx_open
    _banner_placeholder.markdown(
        f'<div style="background:#0d1117;border:1px solid {_gap_color};border-radius:8px;'
        f'padding:10px 16px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center">'
        f'<div>'
        f'<span style="font-size:10px;color:#64748b;letter-spacing:1.2px;text-transform:uppercase">PRE-MARKET MODE</span>'
        f'<div style="font-size:15px;font-weight:800;color:{_gap_color};margin-top:2px">'
        f'Implied Gap: {_implied_gap:+.1f} pts ({_implied_gap_pct:+.2f}%) → {_gap_regime_lbl}</div>'
        f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">'
        f'ES {es_price:,.1f} vs SPX last close {levels["current"]:,.1f} · '
        f'Projected RTH open: <b style="color:{_gap_color}">{_proj_open_display:,.1f}</b>'
        f'{_exp_range_html} · '
        f'{"Gap-down override: Bull Window → chop" if _implied_gap < -GAP_THRESHOLD else "Gap-up override: Pre-Bull Fade → chop" if _implied_gap > GAP_THRESHOLD else "No override threshold crossed"}'
        f'</div>'
        f'</div>'
        f'<div style="text-align:right">'
        f'<div style="font-size:22px;font-weight:800;color:#64748b">⏳</div>'
        f'<div style="font-size:12px;color:#64748b">{_open_label}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True)
# ═══════════════════════════════════════════════════════════════════════════════
# ROW 3 — WHY THIS BIAS (active override chain)
# ═══════════════════════════════════════════════════════════════════════════════
_why_rows = []

# 1. Gap regime
_gap_regime = ("Gap-Up" if live_gap > GAP_THRESHOLD
               else "Gap-Down" if live_gap < -GAP_THRESHOLD else "Flat")
_gap_c = "#4ade80" if live_gap > GAP_THRESHOLD else ("#f87171" if live_gap < -GAP_THRESHOLD else "#64748b")
_why_rows.append(("Gap", f"{live_gap:+.0f} pts → {_gap_regime}", _gap_c,
                  "Override: Pre-Bull Fade & Afternoon Trend → chop on gap-up" if live_gap > GAP_THRESHOLD else ""))

# 2. VIX regime
if vix_now >= VIX_FEAR_THRESHOLD:
    _vix_regime_lbl = f"HIGH ({vix_now}) → Fear"
    _vix_regime_c = "#f87171"
    _vix_override = "Active: chop→bear, bull→chop (high-fear)"
elif vix_now < VIX_CALM_THRESHOLD:
    _vix_regime_lbl = f"LOW ({vix_now}) → Calm"
    _vix_regime_c = "#4ade80"
    _vix_override = "Active: bear→chop (low-vol range-bound)"
else:
    _vix_regime_lbl = f"MID ({vix_now}) → Normal"
    _vix_regime_c = "#94a3b8"
    _vix_override = "No override"
_why_rows.append(("VIX Regime", _vix_regime_lbl, _vix_regime_c, _vix_override))

# 3. ORB status — include width and distance for context
if orb_data.get("valid"):
    _orb_dist_str = (f"  dist {_orb_distance_atr:.2f}×ATR" if _orb_distance_atr > 0 else "")
    _orb_narrow_warn = " ⚠ narrow ORB" if 0 < _orb_range_atr < 0.12 else ""
    _orb_lbl = (f"{orb_data['status'].capitalize()} ({orb_data['high']}/{orb_data['low']})"
                f"  rng {orb_data['range_pts']}pts ({round(_orb_range_atr*100)}%ATR)"
                f"{_orb_dist_str}{_orb_narrow_warn}")
    _orb_c = "#4ade80" if orb_data["status"] == "above" else ("#f87171" if orb_data["status"] == "below" else "#64748b")
    if 0 < _orb_range_atr < 0.12:
        _orb_ov = "ORB too narrow — breakout override suppressed"
    elif orb_data["status"] == "above":
        _orb_ov = f"Active post 10 AM: chop→bull (+{_orb_distance_atr:.2f}×ATR past edge)"
    elif orb_data["status"] == "below":
        _orb_ov = f"Active post 10 AM: chop→bear ({_orb_distance_atr:.2f}×ATR past edge)"
    else:
        _orb_ov = "No override (inside ORB)"
else:
    _orb_lbl, _orb_c, _orb_ov = "ORB unavailable", "#475569", "Pre-market or no data"
_why_rows.append(("ORB", _orb_lbl, _orb_c, _orb_ov))

# 4. News override
_news_abs = abs(_news_comp)
_news_dir_lbl = "Bullish" if _news_comp > 0 else ("Bearish" if _news_comp < 0 else "Neutral")
_news_ov_c = "#4ade80" if _news_comp > 0.25 else ("#f87171" if _news_comp < -0.25 else "#64748b")
_news_ov_txt = (f"Active: chop→{'bull' if _news_comp>0 else 'bear'} (|score|={_news_abs:.2f}≥0.25)"
                if _news_abs >= 0.25 else f"No override (|score|={_news_abs:.2f}<0.25)")
_why_rows.append(("News", f"{_news_dir_lbl} ({_news_comp:+.3f})", _news_ov_c, _news_ov_txt))

# 4b. Overnight range position (ES pre-market)
if live.get("overnight_pos") is not None:
    _on_pos  = live["overnight_pos"]
    _on_high = live.get("overnight_high", "—")
    _on_low  = live.get("overnight_low",  "—")
    _on_pct  = round(_on_pos * 100)
    _on_lbl  = f"{_on_pct}% of range  ({_on_low}–{_on_high})"
    _on_c    = "#4ade80" if _on_pos > 0.67 else ("#94a3b8" if _on_pos > 0.33 else "#f87171")
    _on_ov   = ("Upper third — bull lean" if _on_pos > 0.67
                else "Lower third — bear lean" if _on_pos < 0.33
                else "Mid range — no clear edge")
    _why_rows.append(("Overnight (ES)", _on_lbl, _on_c, _on_ov))

# 4c. Overnight range compression vs daily ATR
if macro_data.get("overnight_range_atr") is not None:
    _ora     = macro_data["overnight_range_atr"]
    _ora_pct = round(_ora * 100)
    _ora_c   = "#f59e0b" if _ora < 0.30 else ("#94a3b8" if _ora < 0.70 else "#f87171")
    _ora_ov  = ("Compressed (<30% ATR) — breakout pending" if _ora < 0.30
                else "Expanded (>70% ATR) — exhaustion lean, expect chop" if _ora >= 0.70
                else "Normal range — no override")
    _why_rows.append(("ON Range vs ATR", f"{_ora_pct}% of daily ATR", _ora_c, _ora_ov))

# 4d. NQ/ES divergence
if live.get("nq_es_div") is not None:
    _div    = live["nq_es_div"]
    _nq_p   = live.get("nq_pct", 0.0)
    _es_p   = live.get("es_pct", 0.0)
    _div_c  = "#4ade80" if _div > 0.15 else ("#f87171" if _div < -0.15 else "#94a3b8")
    _div_ov = ("NQ leading ES — tech risk-on, bull lean" if _div > 0.15
               else "NQ lagging ES — tech distributing, bear lean" if _div < -0.15
               else "No meaningful divergence")
    _why_rows.append(("NQ/ES Divergence",
                      f"NQ {_nq_p:+.2f}%  ES {_es_p:+.2f}%  (Δ{_div:+.3f}%)",
                      _div_c, _div_ov))

# 4e. ES pre-market momentum (30-min slope)
if live.get("es_mom_30m_pct") is not None:
    _ep    = live["es_mom_30m_pct"]
    _ep_pt = live.get("es_mom_30m", 0.0)
    _ep_c  = "#4ade80" if _ep > 0.05 else ("#f87171" if _ep < -0.05 else "#94a3b8")
    _ep_ov = ("Rising momentum — confirms bull bias" if _ep > 0.05
              else "Falling momentum — supply entering pre-open" if _ep < -0.05
              else "Flat — no momentum signal")
    _why_rows.append(("ES Mom (30m)",
                      f"{_ep_pt:+.1f} pts  ({_ep:+.3f}%)",
                      _ep_c, _ep_ov))

# 5. OpEx
if _opex_friday:
    _opex_lbl, _opex_c, _opex_ov = "OpEx Friday", "#f59e0b", "EOD Trend preserved directional (gamma unwind)"
elif _opex_week:
    _opex_lbl, _opex_c, _opex_ov = "OpEx Week (Mon/Tue pin)", "#f59e0b", "Chop windows reinforced Mon-Tue only"
else:
    _opex_lbl, _opex_c, _opex_ov = "Not OpEx", "#475569", "No override"
_why_rows.append(("OpEx", _opex_lbl, _opex_c, _opex_ov))

# 6. Current window actual bias (show what fired)
_bias_fired, _bias_label = window_bias_at(now_hhmm, gap=live_gap, vix=vix_now,
                                          news_score=_news_comp, orb_status=_orb_status, opex=_opex_week,
                                          orb_range_atr=_orb_range_atr, atr=levels["atr"])
_bias_fired_c = BIAS_TEXT.get(_bias_fired, "#94a3b8")

_why_html = "".join(
    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
    f'padding:5px 0;border-bottom:1px solid #1a1f33;font-size:12px">'
    f'<span style="color:#64748b;min-width:80px">{label}</span>'
    f'<span style="color:{vc};font-weight:600;min-width:130px">{val}</span>'
    f'<span style="color:#475569;font-size:11px">{ov}</span>'
    f'</div>'
    for label, val, vc, ov in _why_rows
)

st.markdown(
    f'<div class="card" style="margin-bottom:8px">'
    f'<h3 style="margin-bottom:8px">Why This Bias?</h3>'
    f'<div style="font-size:11px;color:#475569;margin-bottom:8px">'
    f'Active window: <b style="color:{_bias_fired_c}">{_bias_label}</b></div>'
    f'{_why_html}'
    f'</div>',
    unsafe_allow_html=True
)

# ═══════════════════════════════════════════════════════════════════════════════
# TABS — split Live signal from Research/Validation
# ═══════════════════════════════════════════════════════════════════════════════
_tab_live, _tab_research = st.tabs(["📈 Live Signal", "🔬 Research & Validation"])

# ── Everything below this line that is "research" goes in _tab_research ───────
# Signal Breakdown, 2-Year Backtest, Self-Improvement, Last-10-days Backtest

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL BREAKDOWN (Research tab)
# ═══════════════════════════════════════════════════════════════════════════════
with _tab_research:
    st.markdown("""
    <div style="background:#1a1f35;border:1px solid #2d3250;border-left:3px solid #475569;
                border-radius:6px;padding:6px 14px;margin-bottom:10px;font-size:11px;color:#64748b">
      🔬 <b style="color:#94a3b8">Research & Validation</b> — All statistics are
      <b>backtested on historical data</b> and do not guarantee future accuracy.
      In-sample window: first 9 months. Out-of-sample: remaining ~1.8 years.
      Small regime bins (&lt;10 samples) should be interpreted with caution.
    </div>
    """, unsafe_allow_html=True)
    with st.expander(f"📊 Signal Breakdown — {buys} Buy / {sells} Sell · Core SSR: {_core_ssr} · Live-Adj: {score}", expanded=False):
        _INTRADAY_SIGS = {"RSI Above 50", "RSI Trend Zone"} if (_intra_rsi is not None and _is_rth_now) else set()
        # Tier label + color for each signal
        _TIER_LABEL = {"core": "", "session": ("session", "#f59e0b"), "live": ("live", "#64748b")}
        bull_sigs = {k:v for k,v in signals.items() if v==1}
        bear_sigs = {k:v for k,v in signals.items() if v==0}
        scol1, scol2, scol3 = st.columns(3)
        all_sigs = [(k, 1) for k in bull_sigs] + [(k, 0) for k in bear_sigs]
        third = (len(all_sigs) + 2) // 3
        for ci, col in enumerate([scol1, scol2, scol3]):
            chunk = all_sigs[ci*third:(ci+1)*third]
            rows_html = "".join(
                f'<div class="sig-row">'
                f'<span>{"✅" if v else "❌"} {k}'
                + (f' <span style="font-size:9px;color:#60a5fa">(5m)</span>'
                   if k in _INTRADAY_SIGS else
                   (f' <span style="font-size:9px;color:{_TIER_LABEL[SIGNAL_TIERS.get(k,"core")][1]}">'
                    f'({_TIER_LABEL[SIGNAL_TIERS.get(k,"core")][0]})</span>'
                    if SIGNAL_TIERS.get(k, "core") != "core" else ""))
                + f'</span>'
                f'<span style="color:{"#22c55e" if v else "#ef4444"};font-size:10px">{"BUY" if v else "SELL"}</span>'
                f'</div>'
                for k, v in chunk
            )
            col.markdown(f'<div style="background:#1e2130;border-radius:8px;padding:8px 12px">{rows_html}</div>',
                         unsafe_allow_html=True)
        st.caption("core = backtestable closed-bar signal · session = requires today's open · live = real-time feed only · (5m) = intraday RSI override")

        # Group score breakdown — shows each category's score + adaptive weight
        _grp_rows = []
        for _gn, _gs in SIGNAL_GROUPS.items():
            _gpresent = [signals[k] for k in _gs if k in signals]
            if _gpresent:
                _gscore = sum(_gpresent) / len(_gpresent)
                _gw     = _grp_weights.get(_gn, 1.0)
                _grp_rows.append((_gn, _gscore, _gw))
        if _grp_rows:
            _grp_html = "".join(
                f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px">'
                f'<span style="color:#64748b;min-width:80px">{gn}</span>'
                f'<div style="flex:1;background:#1e2130;border-radius:4px;height:8px;overflow:hidden">'
                f'<div style="width:{int(gs*100)}%;height:100%;background:{"#22c55e" if gs >= 0.5 else "#ef4444"};border-radius:4px"></div>'
                f'</div>'
                f'<span style="color:{"#22c55e" if gs >= 0.5 else "#ef4444"};min-width:35px;text-align:right">'
                f'{int(gs*100)}%</span>'
                f'<span style="color:#475569;font-size:9px;min-width:38px;text-align:right">'
                f'w={gw:.2f}</span>'
                f'</div>'
                for gn, gs, gw in _grp_rows
            )
            st.markdown(
                f'<div style="background:#111827;border-radius:8px;padding:10px 14px;margin-top:8px">'
                f'<div style="color:#94a3b8;font-size:11px;margin-bottom:6px">'
                f'Group Score Breakdown &nbsp;·&nbsp; '
                f'<span style="color:#475569">w= adaptive weight from {_grp_weights_ts} calibration</span></div>'
                f'{_grp_html}</div>',
                unsafe_allow_html=True
            )

    with st.expander("📅 Weekly SSR Directional Accuracy — Last 20 Weeks (click to expand)", expanded=False):
        st.caption(
            "Validates SSR directional call (bull/bear/neutral, score vs 50) against actual weekly SPX move — "
            "last 20 weeks. Scope: price+VIX+all 11 sector ETFs (same universe as live model). "
            "VIX and sector slices are date-aligned (not row-position). "
            "Note: this table measures SSR direction accuracy, not the weekly projection path or ranges "
            "(VIX scaling, exhaustion gate, and day-by-day levels are not backtested here)."
        )
        @st.cache_data(ttl=3600)
        def run_weekly_ssr_validation():
            try:
                _spx_w = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
                _vix_w = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
                # Use all 11 sectors (same universe as compute_ssr and backtest_export) so
                # sector breadth scores are computed on the same denominator as the live model.
                # Previously only 5 sectors were used, which skewed breadth scores.
                _sec_w = {}
                for _t in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
                    try:
                        _sec_w[_t] = yf.download(_t, period="2y", interval="1d", progress=False, auto_adjust=True)
                    except Exception:
                        _sec_w[_t] = pd.DataFrame()
                _closes = _spx_w["Close"].squeeze()
                _opens  = _spx_w["Open"].squeeze()
                if isinstance(_closes, pd.DataFrame): _closes = _closes.iloc[:, 0]
                if isinstance(_opens, pd.DataFrame):  _opens  = _opens.iloc[:, 0]
                _dates   = list(_spx_w.index)
                _results = []
                for _wi in range(4, len(_dates) - 5, 5):
                    try:
                        _fri_idx = _wi
                        _cutoff_ts = _dates[_fri_idx]
                        _base    = _spx_w.iloc[:_fri_idx + 1]
                        # Date-aligned VIX and sector slices (not row-position) so warmed-up
                        # history is always anchored to the same calendar date, not array offset.
                        _vbase   = _vix_w[_vix_w.index <= _cutoff_ts]
                        _ebase   = {k: v[v.index <= _cutoff_ts] for k, v in _sec_w.items()}
                        if len(_base) < 252: continue
                        _fri_as_of = EST.localize(datetime(_dates[_fri_idx].year,
                                                           _dates[_fri_idx].month,
                                                           _dates[_fri_idx].day, 15, 0))
                        _, _, _, _wk_sigs = compute_ssr(_base, _vbase, pd.DataFrame(), _ebase,
                                                       as_of_dt=_fri_as_of)
                        # Use equal-weight core-only score (same model as exporter's
                        # equal_weight_static_core) so this table is directly comparable
                        # to exported accuracy numbers. Previously used the full dynamic
                        # compute_ssr() score which includes Gap/ATR Normal (session-only)
                        # and drift-dampened group weights — a different model entirely.
                        _wk_core = {k: v for k, v in _wk_sigs.items()
                                    if SIGNAL_TIERS.get(k) == "core"}
                        _wk_gws, _wk_gww = [], []
                        for _gn, _gs in SIGNAL_GROUPS.items():
                            _pr = [_wk_core[k] for k in _gs if k in _wk_core]
                            if _pr:
                                _wk_gws.append(sum(_pr) / len(_pr))
                                _wk_gww.append(1.0)
                        _sc = round(sum(_wk_gws) / len(_wk_gws) * 100) if _wk_gws else 50
                        _dir = ssr_direction(_sc)
                        _proj_call = "bull" if _dir > 0.2 else ("bear" if _dir < -0.2 else "neutral")
                        _nxt_start = _fri_idx + 1
                        _nxt_end   = min(_fri_idx + 6, len(_closes))
                        if _nxt_end <= _nxt_start: continue
                        _wk_open  = float(_opens.iloc[_nxt_start]) if len(_opens) > _nxt_start else float(_closes.iloc[_nxt_start])
                        _wk_close = float(_closes.iloc[_nxt_end - 1])
                        _wk_move  = round(_wk_close - _wk_open, 1)
                        _actual   = "bull" if _wk_move > 5 else ("bear" if _wk_move < -5 else "neutral")
                        _correct  = (_proj_call == _actual) if _proj_call != "neutral" else None
                        _wk_label = _dates[_nxt_start].strftime("%b %d")
                        _results.append({
                            "week": _wk_label, "ssr": _sc, "call": _proj_call,
                            "actual": _actual, "move": _wk_move, "correct": _correct,
                        })
                    except Exception:
                        continue
                return _results[-20:]
            except Exception:
                return []

        _wkly_results = run_weekly_ssr_validation()
        if not _wkly_results:
            st.warning("Weekly validation unavailable — needs market data connection.")
        else:
            _wk_hits   = sum(1 for r in _wkly_results if r["call"] != "neutral" and r["correct"])
            _wk_total  = sum(1 for r in _wkly_results if r["call"] != "neutral")
            _wk_neutral = sum(1 for r in _wkly_results if r["call"] == "neutral")
            _wk_acc    = int(_wk_hits / _wk_total * 100) if _wk_total else 0
            _wk_acc_c  = "#4ade80" if _wk_acc >= 60 else ("#f59e0b" if _wk_acc >= 45 else "#f87171")
            _neutral_note = f" · {_wk_neutral} neutral (excluded)" if _wk_neutral else ""
            st.markdown(
                f'<div style="font-size:13px;color:#94a3b8;margin-bottom:8px">'
                f'Weekly directional accuracy: <b style="color:{_wk_acc_c};font-size:16px">{_wk_acc}%</b>'
                f' ({_wk_hits}/{_wk_total} directional calls{_neutral_note})'
                f'<span style="font-size:10px;color:#64748b;margin-left:8px">'
                f'[full dynamic model — differs from equal-weight static-core exporter]</span></div>',
                unsafe_allow_html=True)
            _wk_rows = ""
            for _r in _wkly_results:
                _correct_val = _r["correct"]
                _cc  = "#4ade80" if _correct_val else ("#64748b" if _correct_val is None else "#f87171")
                _mc  = "#4ade80" if _r["move"] > 0 else "#f87171"
                _ac  = {"bull":"#4ade80","bear":"#f87171","neutral":"#64748b"}.get(_r["actual"],"#94a3b8")
                _pc  = {"bull":"#4ade80","bear":"#f87171","neutral":"#64748b"}.get(_r["call"],"#94a3b8")
                _tk  = "\u2705" if _correct_val else ("\u2014" if _correct_val is None else "\u274c")
                _wk_rows += (
                    f'<tr style="border-bottom:1px solid #1a1f33">'
                    f'<td style="padding:4px 10px;font-size:12px;color:#94a3b8">{_r["week"]}</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:#f1f5f9">{_r["ssr"]}/100</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:{_pc}">{_r["call"]}</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:{_mc}">{_r["move"]:+.1f} pts</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:{_ac}">{_r["actual"]}</td>'
                    f'<td style="padding:4px 10px;font-size:14px">{_tk}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:12px 14px;'
                f'border:1px solid #2d3250;overflow-x:auto">'
                f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9">'
                f'<thead><tr style="background:#0f1117">'
                f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">WEEK OF</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">SSR</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">CALL</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">ACTUAL MOVE</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">ACTUAL DIR</th>'
                f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">HIT</th>'
                f'</tr></thead><tbody>{_wk_rows}</tbody></table></div>',
                unsafe_allow_html=True)

    with st.expander("📊 2-Year Statistical Window Validation (click to run — takes ~5s)", expanded=False):
        st.caption(
            "2-year hourly accuracy of each window bias — validated with historical gap/VIX/event/OpEx context. "
            "Scope: gap/VIX/calendar events/weekday/OpEx window overrides only. "
            "Not backfilled: ORB width/distance, news sentiment, intraday RSI, PCR, macro, A/D, overnight range. "
            "Live projections layer all of these on top. Slots sampled at 1h bars; "
            "quarter-hour windows (10:45, 11:15, 13:15) are not individually measured here."
        )
        _bt = run_extended_window_backtest()
        if not _bt:
            st.warning("Backtest data unavailable — yfinance 1h data requires a valid market data connection.")
        else:
            _bt_rows = []
            for _wlbl, _ws in _bt.items():
                _tot = _ws["total"]
                _acc = round(_ws["correct"] / _tot * 100, 1) if _tot else 0
                _bt_rows.append({
                    "Window": _wlbl, "Bias": _ws["bias"], "Acc%": f"{_acc}%", "n": _tot,
                    "VIX Lo": (f'{round(_ws["vix_low"]["c"]/_ws["vix_low"]["t"]*100,1)}%' if _ws["vix_low"]["t"] else "—"),
                    "VIX Mid": (f'{round(_ws["vix_mid"]["c"]/_ws["vix_mid"]["t"]*100,1)}%' if _ws["vix_mid"]["t"] else "—"),
                    "VIX Hi": (f'{round(_ws["vix_high"]["c"]/_ws["vix_high"]["t"]*100,1)}%' if _ws["vix_high"]["t"] else "—"),
                    "Gap↑": (f'{round(_ws["gap_up"]["c"]/_ws["gap_up"]["t"]*100,1)}%' if _ws["gap_up"]["t"] else "—"),
                    "Gap→": (f'{round(_ws["gap_flat"]["c"]/_ws["gap_flat"]["t"]*100,1)}%' if _ws["gap_flat"]["t"] else "—"),
                    "Gap↓": (f'{round(_ws["gap_down"]["c"]/_ws["gap_down"]["t"]*100,1)}%' if _ws["gap_down"]["t"] else "—"),
                    "Suggested": _ws.get("suggested_bias", _ws["bias"]),
                    "Flag": "⚠️ FLIP" if _ws.get("flip") else "",
                })
            _bt_df = pd.DataFrame(_bt_rows)
            _flips_df = _bt_df[_bt_df["Flag"] == "⚠️ FLIP"]
            if not _flips_df.empty:
                st.markdown("**⚠️ Bias Flip Suggestions** (accuracy < 40%)")
                for _, _row in _flips_df.iterrows():
                    st.markdown(
                        f'<div style="background:#450a0a;border-radius:6px;padding:6px 10px;margin-bottom:4px;font-size:12px">'
                        f'<b style="color:#f87171">{_row["Window"]}</b> &nbsp;·&nbsp; current: <b>{_row["Bias"]}</b>'
                        f' → suggested: <b style="color:#4ade80">{_row["Suggested"]}</b>'
                        f' &nbsp;·&nbsp; acc: <b style="color:#f87171">{_row["Acc%"]}</b> n={_row["n"]}'
                        f'</div>', unsafe_allow_html=True)
            st.dataframe(_bt_df, use_container_width=True, hide_index=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PRIORITY 2: Regime Accuracy Breakdown
    # ─────────────────────────────────────────────────────────────────────────
    with st.expander("📐 Regime Accuracy Breakdown — 2yr Walk-Forward (click to expand)", expanded=False):
        st.caption(
            "Uses core SSR only (28 backtestable signals, equal group weights). "
            "Predicted direction: score ≥55 = bull, ≤44 = bear; neutral skipped. "
            "Actual = next-day SPX close vs today's close (>5 pts = bull, <−5 = bear). "
            "Run once per day (cached 24h) — first run downloads ~13 tickers."
        )
        if st.button("🔬 Run Regime & Ablation Analysis", key="run_ha"):
            compute_historical_analysis.clear()  # force refresh
        _ha = compute_historical_analysis()
        if not _ha:
            st.warning("Analysis unavailable — needs market data connection.")
        else:
            _bt_total = _ha["baseline_total"]
            _bt_hits  = _ha["baseline_hits"]
            _bt_base  = int(_bt_hits / _bt_total * 100) if _bt_total else 0
            _btc      = "#4ade80" if _bt_base >= 60 else ("#f59e0b" if _bt_base >= 50 else "#f87171")
            st.markdown(
                f'<div style="font-size:13px;color:#94a3b8;margin-bottom:12px">'
                f'Overall core-SSR directional accuracy (2yr): '
                f'<b style="color:{_btc};font-size:16px">{_bt_base}%</b>'
                f' &nbsp;·&nbsp; n={_bt_total} directional calls</div>',
                unsafe_allow_html=True)

            def _regime_table(title, buckets, labels):
                rows = ""
                for k, lbl in labels:
                    d = buckets.get(k, {"h":0,"t":0})
                    if d["t"] < 3: continue
                    pct = int(d["h"] / d["t"] * 100)
                    c   = "#4ade80" if pct >= 60 else ("#f59e0b" if pct >= 50 else "#f87171")
                    bar = f'<div style="flex:1;background:#1e2130;border-radius:3px;height:6px;overflow:hidden"><div style="width:{pct}%;height:100%;background:{c};border-radius:3px"></div></div>'
                    rows += (f'<tr><td style="padding:4px 10px;font-size:12px;color:#94a3b8">{lbl}</td>'
                             f'<td style="padding:4px 8px;font-size:13px;font-weight:700;color:{c}">{pct}%</td>'
                             f'<td style="padding:4px 8px;font-size:11px;color:#475569">n={d["t"]}</td>'
                             f'<td style="padding:4px 12px;min-width:80px">{bar}</td></tr>')
                if not rows: return ""
                return (f'<div style="margin-bottom:12px"><div style="font-size:10px;color:#64748b;letter-spacing:1px;margin-bottom:4px">{title}</div>'
                        f'<table style="width:100%;border-collapse:collapse">{rows}</table></div>')

            _reg = _ha["regime"]
            _dow_names = {0:"Monday",1:"Tuesday",2:"Wednesday",3:"Thursday",4:"Friday"}
            _cols = st.columns(3)
            with _cols[0]:
                st.markdown(_regime_table("VIX REGIME", _reg["vix"],
                    [("low","VIX < 18 (calm)"),("mid","VIX 18–25 (normal)"),("high","VIX > 25 (fear)")]),
                    unsafe_allow_html=True)
                st.markdown(_regime_table("GAP REGIME", _reg["gap"],
                    [("up",f"Gap Up > {int(GAP_THRESHOLD)} pts"),("flat",f"Flat / ≤{int(GAP_THRESHOLD)} pts"),("down",f"Gap Down > {int(GAP_THRESHOLD)} pts")]),
                    unsafe_allow_html=True)
            with _cols[1]:
                st.markdown(_regime_table("DAY OF WEEK", _reg["dow"],
                    [(d, _dow_names[d]) for d in range(5)]), unsafe_allow_html=True)
            with _cols[2]:
                st.markdown(_regime_table("EVENT DAY", _reg["event"],
                    [("event","FOMC / CPI / NFP"),("normal","No major event")]),
                    unsafe_allow_html=True)
                st.markdown(_regime_table("OPEX WEEK", _reg["opex"],
                    [("opex","OpEx week"),("normal","Normal week")]),
                    unsafe_allow_html=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PRIORITY 3: Signal Ablation Study
    # ─────────────────────────────────────────────────────────────────────────
    with st.expander("🧬 Signal Ablation Study — Which Signals Add Edge (click to expand)", expanded=False):
        st.caption(
            "For each core signal: compare accuracy WITH vs WITHOUT that signal over the 2yr walk-forward. "
            "Positive delta = signal helps (removing it hurts accuracy). "
            "Negative delta = signal may be noise (removing it improves accuracy). "
            "Uses equal group weights and core signals only. Same cache as regime analysis."
        )
        _ha2 = compute_historical_analysis()
        if not _ha2 or not _ha2.get("ablation"):
            st.warning("Ablation data unavailable — run the regime analysis first.")
        else:
            _abl = _ha2["ablation"]
            _abl_rows = []
            for _sig, _ad in _abl.items():
                if _ad["t"] < 10: continue
                _acc_all  = int(_ad["h_all"]  / _ad["t"] * 100)
                # Accuracy w/o signal: measure only on rows where excluded model still directional
                _acc_excl = int(_ad["h_excl"] / _ad["t_excl"] * 100) if _ad["t_excl"] >= 5 else None
                # Coverage = fraction of original directional calls preserved after removal
                _coverage = int(_ad["t_excl"] / _ad["t"] * 100) if _ad["t"] else 100
                _delta    = (_acc_all - _acc_excl) if _acc_excl is not None else None
                _grp_name = next((g for g, sigs in SIGNAL_GROUPS.items() if _sig in sigs), "—")
                _abl_rows.append((_sig, _grp_name, _acc_all, _acc_excl, _delta, _coverage, _ad["t"]))
            # Sort by delta descending (biggest contributors first); None deltas go to end
            _abl_rows.sort(key=lambda x: (x[4] is None, -(x[4] or 0)))
            if _abl_rows:
                _tbl_html = ""
                for _sig, _grp, _acc_a, _acc_e, _dlt, _cov, _n in _abl_rows:
                    _dlt_str  = (f'{"+" if _dlt >= 0 else ""}{_dlt}pp') if _dlt is not None else "—"
                    _dc = ("#4ade80" if (_dlt or 0) > 1 else ("#f87171" if (_dlt or 0) < -1 else "#94a3b8"))
                    _icon = "✅" if (_dlt or 0) > 1 else ("⚠️" if (_dlt or 0) < -1 else "➖")
                    _cov_c = "#94a3b8" if _cov >= 90 else ("#f59e0b" if _cov >= 75 else "#f87171")
                    _tbl_html += (
                        f'<tr style="border-bottom:1px solid #1a1f33">'
                        f'<td style="padding:4px 10px;font-size:11px">{_icon} {_sig}</td>'
                        f'<td style="padding:4px 6px;font-size:10px;color:#64748b">{_grp}</td>'
                        f'<td style="padding:4px 6px;font-size:12px;font-weight:700;color:#94a3b8">{_acc_a}%</td>'
                        f'<td style="padding:4px 6px;font-size:12px;color:#64748b">{"—" if _acc_e is None else f"{_acc_e}%"}</td>'
                        f'<td style="padding:4px 10px;font-size:13px;font-weight:800;color:{_dc}">{_dlt_str}</td>'
                        f'<td style="padding:4px 6px;font-size:11px;color:{_cov_c}">{_cov}%</td>'
                        f'<td style="padding:4px 6px;font-size:10px;color:#475569">n={_n}</td>'
                        f'</tr>'
                    )
                st.markdown(
                    f'<div style="background:#1e2130;border-radius:10px;padding:12px 14px;overflow-x:auto">'
                    f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9">'
                    f'<thead><tr style="background:#0f1117">'
                    f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">SIGNAL</th>'
                    f'<th style="padding:5px 6px;text-align:left;color:#64748b;font-size:10px">GROUP</th>'
                    f'<th style="padding:5px 6px;color:#64748b;font-size:10px">ACC W/ SIG</th>'
                    f'<th style="padding:5px 6px;color:#64748b;font-size:10px">ACC W/O SIG</th>'
                    f'<th style="padding:5px 10px;color:#64748b;font-size:10px">DELTA</th>'
                    f'<th style="padding:5px 6px;color:#64748b;font-size:10px">COVERAGE</th>'
                    f'<th style="padding:5px 6px;color:#64748b;font-size:10px">N</th>'
                    f'</tr></thead><tbody>{_tbl_html}</tbody></table></div>',
                    unsafe_allow_html=True)
                st.caption("✅ = adds edge  ⚠️ = noise  ➖ = neutral  ·  Coverage = % of directional calls preserved without the signal  ·  delta >±1pp meaningful at n≥50")
                # Auto-write ablation summary to Codex/ for the other agent to read
                try:
                    _rpt_lines = [
                        f"# Signal Ablation Report",
                        f"Generated: {now_est.strftime('%Y-%m-%d %H:%M')} ET  "
                        f"· Baseline accuracy: {_ha2['baseline_hits']}/{_ha2['baseline_total']} "
                        f"({int(_ha2['baseline_hits']/_ha2['baseline_total']*100) if _ha2['baseline_total'] else 0}%)\n",
                        "| Signal | Group | Acc w/ | Acc w/o | Delta | Coverage | N | Verdict |",
                        "|--------|-------|--------|---------|-------|----------|---|---------|",
                    ]
                    for _sig, _grp, _acc_a, _acc_e, _dlt, _cov, _n in _abl_rows:
                        _verdict = "✅ adds edge" if (_dlt or 0) > 1 else ("⚠️ may be noise" if (_dlt or 0) < -1 else "➖ neutral")
                        _dlt_s = f'{"+" if (_dlt or 0) >= 0 else ""}{_dlt}pp' if _dlt is not None else "—"
                        _acc_e_s = f"{_acc_e}%" if _acc_e is not None else "—"
                        _rpt_lines.append(f"| {_sig} | {_grp} | {_acc_a}% | {_acc_e_s} | {_dlt_s} | {_cov}% | {_n} | {_verdict} |")
                    _rpt_lines.append("\n**Signals with negative delta (noise candidates):**")
                    _noisy = [f"- {_sig} ({_grp}) delta={_dlt}pp coverage={_cov}% n={_n}"
                              for _sig, _grp, _acc_a, _acc_e, _dlt, _cov, _n in _abl_rows if (_dlt or 0) < -1]
                    _rpt_lines += (_noisy if _noisy else ["- None identified"])
                    _rpt_lines.append("\n_Note: Acc w/o and Delta computed only on rows where excluded model still makes a directional call. Coverage = % of original directional calls preserved._")
                    os.makedirs("Codex", exist_ok=True)
                    with open("Codex/ablation-report.md", "w") as _rf:
                        _rf.write("\n".join(_rpt_lines) + "\n")
                except Exception:
                    pass  # silently skip if filesystem not writable


with _tab_live:
    # ═══════════════════════════════════════════════════════════════════════════════
    # ROW 4 — HOURLY PROJECTIONS (ES left, SPX right)
    # live_gap and _orb_status computed at module level above (before tabs)
    # ═══════════════════════════════════════════════════════════════════════════════
    # Reuse the ES rows pre-computed at module level.
    es_rows = _es_rows_precomp
    # SPX anchor = ES projected price at last overnight slot (just before RTH).
    # This aligns the SPX RTH open row with the ES table's 9:30 AM slot so
    # both tables show the same price at the RTH open boundary.
    _spx_proj_base = (_es_rth_anchor if (_es_rth_anchor is not None and _pre_market and live["es_price"])
                      else spx_price)
    spx_rows = generate_spx_projections(_spx_proj_base, levels["atr"], score, gap=live_gap, vix=vix_now, news_score=_news_comp, orb_status=_orb_status, opex=_opex_week, orb_range_atr=_orb_range_atr, orb_distance_atr=_orb_distance_atr, gap_confirmed=_gap_confirmed)

    colA, colB = st.columns(2)

    with colA:
        rows_html = ""
        for r in es_rows:
            bg  = BIAS_BG.get(r["win_bias"], "#1e293b")
            tc  = BIAS_TEXT.get(r["win_bias"], "#94a3b8")
            mc  = "#4ade80" if r["move"] >= 0 else "#f87171"
            sgn = "+" if r["move"] >= 0 else ""
            rows_html += (
                f'<tr style="background:{bg}">'
                f'<td class="pt" style="white-space:nowrap">{r["time"]}</td>'
                f'<td class="pt" style="color:#64748b">{r["session"]}</td>'
                f'<td class="pt" style="font-weight:700">{r["price"]:,}</td>'
                f'<td class="pt" style="color:{mc}">{sgn}{r["move"]}</td>'
                f'<td class="pt" style="color:#64748b">{r["rng_lo"]}–{r["rng_hi"]}</td>'
                f'<td class="pt" style="color:{tc}">{BIAS_COLOR.get(r["win_bias"],"")} {r["win_label"]}</td>'
                f'</tr>'
            )
        st.markdown(f"""
        <div class="card">
          <h3>ES Futures — 30-Min Projection &nbsp;·&nbsp; Next 23-Hour Session from Opening Bell</h3>
          <div style="overflow-y:auto;max-height:400px">
          <table class="proj-table">
            <thead><tr>
              <th>TIME (EST)</th><th>SESS</th><th>PROJECTED</th>
              <th>MOVE</th><th>RANGE</th><th>WINDOW</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table></div>
        </div>
        <style>.pt{{padding:5px 8px;font-size:13px}}</style>
        """, unsafe_allow_html=True)

    with colB:
        spx_rows_html = ""
        # Pre-market: prepend an anchor row showing projected SPX RTH open.
        # Anchor = current implied open (last SPX close + implied gap from ES).
        if _pre_market and live["es_price"]:
            _anc = _spx_proj_base
            _prev_close = levels["current"] if levels["current"] > 0 else spx_price
            _anc_delta = round(_anc - _prev_close, 1) if _prev_close > 0 else 0.0
            _gap_c_row = "#4ade80" if _anc_delta >= 0 else "#f87171"
            _gap_sign  = "+" if _anc_delta >= 0 else ""
            spx_rows_html += f"""
            <tr style="background:#0a1a0a;border-bottom:2px solid #1e3a1e">
              <td style="padding:5px 10px;color:#64748b;font-size:11px">RTH Open (proj)</td>
              <td style="padding:5px 10px;font-weight:800;font-size:14px;color:{_gap_c_row}">{_anc:,.1f}</td>
              <td style="padding:5px 8px;font-size:11px;color:{_gap_c_row}">{_gap_sign}{_anc_delta:.1f}</td>
              <td style="padding:5px 8px;font-size:10px;color:#475569">ES implied open</td>
              <td style="padding:5px 10px;font-size:10px;color:#475569">ES {es_price:,.1f} · close {_prev_close:,.1f}</td>
            </tr>"""
        for r in spx_rows:
            bg   = "#111827" if r["past"] else BIAS_BG.get(r["win_bias"], "#1e293b")
            tc   = "#374151" if r["past"] else BIAS_TEXT.get(r["win_bias"], "#94a3b8")
            pc   = "#374151" if r["past"] else "#f1f5f9"
            sign = "+" if r["move"] >= 0 else ""
            mc   = "#374151" if r["past"] else ("#4ade80" if r["move"] >= 0 else "#f87171")
            past_tag = '<span style="font-size:10px;color:#374151"> (past)</span>' if r["past"] else ""
            spx_rows_html += f"""
            <tr style="background:{bg}">
              <td style="padding:6px 10px;color:#94a3b8;font-size:12px;white-space:nowrap">{r['time']}{past_tag}</td>
              <td style="padding:6px 10px;font-weight:700;font-size:14px;color:{pc}">{r['price']:,}</td>
              <td style="padding:6px 8px;font-size:12px;color:{mc}">{sign}{r['move']}</td>
              <td style="padding:6px 8px;font-size:11px;color:#64748b">{r['rng_lo']} – {r['rng_hi']}</td>
              <td style="padding:6px 10px;font-size:12px;color:{tc}">{BIAS_COLOR.get(r['win_bias'],'')} {r['win_label']}</td>
            </tr>"""

        st.markdown(f"""
        <div class="card">
          <h3>SPX — Intraday Projection &nbsp;·&nbsp; RTH 9:30 AM – 4:00 PM EST</h3>
          <div style="overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;color:#f1f5f9">
            <thead>
              <tr style="background:#0f1117">
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b;letter-spacing:.8px">TIME (EST)</th>
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">PROJECTED</th>
                <th style="padding:7px 8px;text-align:left;font-size:11px;color:#64748b">MOVE</th>
                <th style="padding:7px 8px;text-align:left;font-size:11px;color:#64748b">RANGE</th>
                <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">WINDOW</th>
              </tr>
            </thead>
            <tbody>{spx_rows_html}</tbody>
          </table>
          </div>
          <div style="margin-top:10px;font-size:11px;color:#475569">
            Weekend / after close → shows next trading session. Grayed rows = past slots during live session.
          </div>
        </div>
        """, unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    # ROW 4B — PROJECTED CLOSE CARD (re-anchored to current live price)
    # ═══════════════════════════════════════════════════════════════════════════════
    # Instead of reading the stale open-anchored projection table, we re-anchor
    # to the current SPX price and sum only the remaining (future) slot moves.
    # This gives a live "where will we close?" number that updates every refresh.
    _is_rth_live = _is_rth_now  # already computed above
    _future_rows = [r for r in spx_rows if not r["past"]]
    _past_rows   = [r for r in spx_rows if r["past"]]

    if _is_rth_live and _future_rows:
        # Sum remaining window moves from current price
        _remaining_move = sum(r["move"] for r in _future_rows)
        _proj_close     = round(spx_price + _remaining_move, 0)
        # Derive per-slot ATR from the rng_hi/rng_lo stored in each row:
        # rng = price ± slot_atr*0.4  →  slot_atr = (rng_hi - rng_lo) / 0.8
        _remaining_atr  = sum((r["rng_hi"] - r["rng_lo"]) / 0.8 for r in _future_rows)
        _proj_close_lo  = round(_proj_close - _remaining_atr * 0.55, 0)
        _proj_close_hi  = round(_proj_close + _remaining_atr * 0.55, 0)
        _slots_left     = len(_future_rows)
        _move_from_now  = round(_remaining_move, 1)
        _move_today     = round(spx_price - levels["current"], 1)  # from prev close
        _mv_sign        = "+" if _move_from_now >= 0 else ""
        _td_sign        = "+" if _move_today >= 0 else ""
        _close_color    = "#4ade80" if _proj_close > spx_price else ("#f87171" if _proj_close < spx_price else "#94a3b8")
        _today_color    = "#4ade80" if _move_today >= 0 else "#f87171"
        _atr_pct        = round(abs(_move_today) / levels["atr"] * 100, 0) if levels["atr"] > 0 else 0

        # Determine regime label for close
        _close_regime   = "Holding gains" if _move_from_now >= 0 else "Afternoon fade"
        if abs(_move_from_now) < levels["atr"] * 0.05:
            _close_regime = "Consolidation into close"

        # Next key window (first future slot label)
        _next_window = _future_rows[0]["win_label"] if _future_rows else "—"
        _next_time   = _future_rows[0]["time"]      if _future_rows else "—"

        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#0f1a2e,#1a1a2e);border:1px solid #2d3a5a;
                    border-radius:14px;padding:22px 28px;margin-bottom:16px">
          <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px">

            <!-- Left: big close number -->
            <div>
              <div style="font-size:11px;color:#64748b;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:6px">
                📍 Projected SPX Close — Re-anchored to Live Price
              </div>
              <div style="font-size:42px;font-weight:800;color:{_close_color};line-height:1">
                {int(_proj_close):,}
              </div>
              <div style="font-size:13px;color:#64748b;margin-top:4px">
                Range &nbsp;<span style="color:#94a3b8;font-weight:600">{int(_proj_close_lo):,} – {int(_proj_close_hi):,}</span>
              </div>
            </div>

            <!-- Middle: live context -->
            <div style="text-align:center">
              <div style="font-size:11px;color:#64748b;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">Now</div>
              <div style="font-size:26px;font-weight:700;color:#f1f5f9">{spx_price:,.1f}</div>
              <div style="font-size:12px;color:{_today_color};margin-top:2px">{_td_sign}{_move_today:+.1f} today &nbsp;({_atr_pct:.0f}% ATR)</div>
            </div>

            <!-- Right: remaining move + regime -->
            <div style="text-align:right">
              <div style="font-size:11px;color:#64748b;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">Remaining move</div>
              <div style="font-size:22px;font-weight:700;color:{_close_color}">{_mv_sign}{_move_from_now:+.1f} pts</div>
              <div style="font-size:12px;color:#64748b;margin-top:2px">{_slots_left} slots left · {_close_regime}</div>
              <div style="font-size:11px;color:#475569;margin-top:6px">
                Next: <span style="color:#94a3b8">{_next_time} {_next_window}</span>
              </div>
            </div>

          </div>
        </div>
        """, unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    # ROW 5 — WEEKLY PROJECTION
    # ═══════════════════════════════════════════════════════════════════════════════
    st.markdown("<h4 style='margin:6px 0 10px;color:#94a3b8'>📅 Weekly Projection — Next 5 Trading Days</h4>",
                unsafe_allow_html=True)

    weekly = generate_weekly_projections(spx_price, levels["atr"], score, vix=vix_now)

    week_cols = st.columns(5)
    for i, (col, r) in enumerate(zip(week_cols, weekly)):
        bg_card  = {"bear": "#2d0a0a", "bull": "#0a2d1a", "neutral": "#1e2130"}[r["bias"]]
        tc       = {"bear": "#f87171", "bull": "#4ade80",  "neutral": "#94a3b8"}[r["bias"]]
        sign     = "+" if r["move"] >= 0 else ""
        mc       = "#4ade80" if r["move"] >= 0 else "#f87171"
        border   = {"bear": "#7f1d1d", "bull": "#14532d", "neutral": "#2d3250"}[r["bias"]]
        with col:
            st.markdown(f"""
            <div style="background:{bg_card};border:1px solid {border};border-radius:12px;
                        padding:18px 16px;text-align:center;margin-bottom:8px">
              <div style="font-size:11px;color:#64748b;letter-spacing:1px;text-transform:uppercase">{r['day']}</div>
              <div style="font-size:18px;font-weight:800;color:#f1f5f9;margin:4px 0">{r['date']}</div>
              <div style="font-size:28px;margin:6px 0">{r['icon']}</div>
              <div style="font-size:22px;font-weight:800;color:{tc};margin:4px 0">{r['price']:,}</div>
              <div style="font-size:12px;color:{mc};margin-bottom:8px">{sign}{r['move']} pts</div>
              <div style="font-size:11px;color:#64748b;border-top:1px solid {border};padding-top:8px;margin-top:4px">
                <div>High: <b style="color:#f87171">{r['hi']:,}</b></div>
                <div>Low: &nbsp;<b style="color:#4ade80">{r['lo']:,}</b></div>
              </div>
              <div style="margin-top:8px;background:#0f1117;border-radius:5px;padding:2px 6px;
                          font-size:10px;color:#64748b;display:inline-block">
                Confidence: {r['conf']}
              </div>
            </div>
            """, unsafe_allow_html=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    # BACKTEST — Last 5 Trading Days  (Week of March 23–27, 2026)
    # ═══════════════════════════════════════════════════════════════════════════════

    # Read from Streamlit secrets (never commit raw token)
    UW_TOKEN = st.secrets.get("UW_TOKEN", "")

    @st.cache_data(ttl=3600)
    def load_uw_market_tide(date_str):
        """Fetch Unusual Whales market tide (bull/bear flow) for a given date YYYY-MM-DD."""
        try:
            import urllib.request, json as _json
            url = f"https://api.unusualwhales.com/api/market/market-tide?date={date_str}&limit=100"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UW_TOKEN}"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = _json.loads(r.read())
            records = data.get("data", [])
            if not records:
                return None
            bull_prem = sum(float(x.get("net_call_premium", 0) or 0) for x in records)
            bear_prem = sum(float(x.get("net_put_premium",  0) or 0) for x in records)
            pcr_uw    = round(abs(bear_prem) / bull_prem, 2) if bull_prem > 0 else 1.0
            bias      = "Bullish" if bull_prem > abs(bear_prem) else "Bearish"
            return {"bull": round(bull_prem/1e6,1), "bear": round(abs(bear_prem)/1e6,1),
                    "pcr": pcr_uw, "bias": bias, "records": len(records)}
        except Exception:
            return None


    @st.cache_data(ttl=3600)
    def load_backtest_data():
        """Load 5-day intraday + daily data for full-week backtest."""
        spx_d = yf.download("^GSPC", period="100d", interval="1d", progress=False, auto_adjust=True)
        # Match VIX period to SPX so weight calibration can look back 100 days, not just 30.
        # Previously 30d meant VIX slices ran out early and calibration had too few data points.
        vix_d = yf.download("^VIX",  period="100d", interval="1d", progress=False, auto_adjust=True)
        sectors_d = {}
        for t in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
            try:
                sectors_d[t] = yf.download(t, period="100d", interval="1d", progress=False, auto_adjust=True)
            except Exception:
                sectors_d[t] = pd.DataFrame()

        # period="20d" gives ~20 trading days of 5-min bars → covers 2 full weeks
        spx_5m = yf.download("^GSPC", period="20d", interval="5m", progress=False, auto_adjust=True)
        spx_5m.index = spx_5m.index.tz_convert(EST)
        trading_days  = sorted(set(spx_5m.index.date))
        # Store both Close (for slot outcome scoring) and Open (for gap/day_open accuracy).
        # day_series stores Close; day_open_series stores the first 5m bar's Open.
        day_series      = {d: spx_5m[spx_5m.index.date == d]["Close"].squeeze() for d in trading_days}
        day_open_series = {}
        for d in trading_days:
            _ddf = spx_5m[spx_5m.index.date == d]
            try:
                _o = _ddf["Open"].squeeze()
                if isinstance(_o, pd.DataFrame): _o = _o.iloc[:, 0]
                day_open_series[d] = float(_o.iloc[0])
            except Exception:
                # fallback: use first Close if Open not available
                _c = day_series.get(d)
                day_open_series[d] = float(_c.iloc[0]) if _c is not None and len(_c) else 0.0
        return spx_d, vix_d, sectors_d, day_series, trading_days, day_open_series


    def run_backtest_for_day(target_date, day_series, spx_d, vix_d, sectors_d, daily_dates_list, offset_from_end, day_open_series=None):
        day_5m = day_series.get(target_date)
        if day_5m is None or len(day_5m) == 0:
            return None

        spx_base = spx_d.iloc[:-offset_from_end] if offset_from_end > 0 else spx_d
        vix_base = vix_d.iloc[:-offset_from_end] if offset_from_end > 0 else vix_d
        sec_base  = {k: v.iloc[:-offset_from_end] if offset_from_end > 0 else v for k, v in sectors_d.items()}

        prev_close   = float(spx_base["Close"].squeeze().iloc[-1])
        # Pass historical noon timestamp so VIX Falling uses the right clock
        _bt_as_of = EST.localize(datetime(target_date.year, target_date.month, target_date.day, 12, 0))
        bt_score, bt_buys, bt_sells, _ = compute_ssr(
            spx_base, vix_base, pd.DataFrame(), sec_base, as_of_dt=_bt_as_of)
        bt_rating, bt_action, _, bt_color = ssr_meta(bt_score)
        bt_direction = ssr_direction(bt_score)

        h = spx_base["High"].squeeze(); l = spx_base["Low"].squeeze(); c = spx_base["Close"].squeeze()
        tr     = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        bt_atr = float(tr.rolling(14).mean().iloc[-1])
        # Adaptive slot-ATR profile — front-loads morning volatility, same as live SPX path.
        # 16 backtest slots mapped to 8 profile buckets (pairs of slots share one bucket).
        # Profile: [0.28, 0.18, 0.12, 0.08, 0.09, 0.11, 0.09, 0.05] sums to ~1.0
        _bt_atr_profile = [0.28, 0.28, 0.18, 0.18, 0.12, 0.12, 0.08, 0.08,
                           0.09, 0.09, 0.11, 0.11, 0.09, 0.09, 0.05, 0.05]
        # slot_atr is now per-slot; override it inside the loop below
        slot_atr = bt_atr / 6.5   # fallback if profile lookup fails

        # VIX on this day — used for VIX regime window overrides
        try:
            vix_on_day = float(vix_base["Close"].squeeze().iloc[-1])
        except Exception:
            vix_on_day = 0.0

        # Compute actual gap for this day (open − prior close) to drive gap-conditional windows.
        # Use the first 5-minute bar's Open price if available (more accurate than Close).
        # Falls back to first Close if Open data wasn't stored (older cache entries).
        if day_open_series and target_date in day_open_series:
            day_open = day_open_series[target_date]
        else:
            day_open = float(day_5m.iloc[0])
        day_gap   = round(day_open - prev_close, 1)

        slots = ["09:30","10:00","10:30","10:45","11:00","11:15","11:30","12:00",
                 "13:00","13:15","13:30","14:00","14:30","15:00","15:30","16:00"]
        # Always anchor projection to the actual open price, not prior close.
        # Using prev_close would inflate hit rates on gap days (gap move credited to forecast).
        proj_price = day_open
        projections = []
        for s in slots:
            _bt_dt_str  = target_date.strftime("%Y-%m-%d")
            _bt_evts    = {ev[2] for ev in _ECON_CAL if ev[0] == _bt_dt_str}
            _bt_is_opex = is_opex_week(target_date)
            win_bias, win_label = window_bias_at(
                s, gap=day_gap, vix=vix_on_day,
                event_types=_bt_evts, weekday=target_date.weekday(),
                opex=_bt_is_opex, atr=bt_atr)
            wf   = {"bull":0.5,"bear":-0.5,"chop":0.0,"neutral":0.0}[win_bias]
            # Use adaptive per-slot ATR (front-loaded morning vol)
            _si  = slots.index(s)
            _p   = _bt_atr_profile[_si] if _si < len(_bt_atr_profile) else 0.07
            _s_atr = bt_atr * _p
            # Regime-aware blend — same logic as generate_spx_projections()
            _bt_score_borderline = 35 < bt_score < 65
            # Large gap-up ATR ratio — used to detect gap-dominant sessions where
            # the prior-day SSR is a weaker predictor than the gap itself.
            _bt_gap_atr_ratio = day_gap / max(bt_atr, 1.0)
            _large_gap_up = day_gap > GAP_THRESHOLD and _bt_gap_atr_ratio > 0.5
            if vix_on_day > VIX_FEAR_THRESHOLD:
                # Gap-down + hi-VIX: mirror live projection dampening in backtest.
                if day_gap < -GAP_THRESHOLD:
                    _dw, _ww = (0.40, 0.60) if _bt_score_borderline else (0.50, 0.50)
                elif _large_gap_up:
                    # Large gap-up in hi-VIX: SSR reflects fear from prior close,
                    # not the gap continuation context. Let windows dominate.
                    _dw, _ww = (0.35, 0.65) if _bt_score_borderline else (0.45, 0.55)
                else:
                    _dw, _ww = (0.55, 0.45) if _bt_score_borderline else (0.70, 0.30)
            elif 0 < vix_on_day < VIX_CALM_THRESHOLD:
                _dw, _ww = 0.40, 0.60
            elif day_gap < -GAP_THRESHOLD:
                _dw, _ww = 0.65, 0.35
            elif _large_gap_up:
                # Large gap-up in normal VIX: prior-day SSR is a poor predictor of
                # intraday direction from the gap-up open. Reduce SSR weight so the
                # model doesn't over-predict a reversal on days where the gap holds.
                _dw, _ww = (0.40, 0.60) if _bt_score_borderline else (0.50, 0.50)
            elif day_gap > GAP_THRESHOLD:
                _dw, _ww = 0.60, 0.40
            else:
                _dw, _ww = 0.55, 0.45
            move = _s_atr * (bt_direction * _dw + wf * _ww)
            proj_price = round(proj_price + move, 1)
            projections.append({"slot": s, "proj": proj_price, "move": round(move,1),
                                 "bias": win_bias, "label": win_label, "slot_atr": round(_s_atr,1)})

        def actual_at(hhmm):
            hh, mm = map(int, hhmm.split(":"))
            snap = day_5m[(day_5m.index.hour == hh) & (day_5m.index.minute >= mm)].head(1)
            return round(float(snap.iloc[0]), 1) if len(snap) else None

        results = []
        for p in projections:
            actual = actual_at(p["slot"])
            if actual is None: continue
            idx    = slots.index(p["slot"])
            # Opening slot (09:30): baseline is day_open so overnight gap is not credited
            # to the projection. Prior-slot price for all subsequent slots.
            if idx == 0:
                prev_a = day_open
            else:
                prev_a = actual_at(slots[idx-1]) or day_open
            actual_dir = "bull" if actual > prev_a else ("bear" if actual < prev_a else "chop")
            # Chop = flat/indeterminate — threshold scales with VIX and per-slot ATR.
            # Use adaptive per-slot ATR (front-loaded morning vol) so early-session
            # slots are judged with larger thresholds, not the flat fallback.
            # VIX=20 → 0.30× ATR; VIX=30 → 0.45× ATR; VIX=40 → 0.60× ATR.
            _slot_atr_for_chop = p.get("slot_atr", slot_atr)  # use adaptive if available
            _chop_thresh = _slot_atr_for_chop * min(0.6, 0.30 * max(1.0, vix_on_day / 20.0))
            _flat = abs(actual - prev_a) < _chop_thresh
            correct = (p["bias"] == "bear" and actual_dir == "bear") or \
                      (p["bias"] == "bull" and actual_dir == "bull") or \
                      (p["bias"] == "chop" and _flat)
            results.append({**p, "actual": actual, "actual_dir": actual_dir,
                            "correct": correct, "err": round(actual - p["proj"], 1)})

        dir_acc = sum(1 for r in results if r["correct"]) / len(results) * 100 if results else 0
        avg_err = sum(abs(r["err"]) for r in results) / len(results) if results else 0

        return {
            "score": bt_score, "buys": bt_buys, "sells": bt_sells,
            "rating": bt_rating, "color": bt_color,
            "prev_close": prev_close, "atr": round(bt_atr,1),
            "day_open": float(day_5m.iloc[0]), "day_eod": float(day_5m.iloc[-1]),
            "day_move": round(float(day_5m.iloc[-1]) - float(day_5m.iloc[0]), 1),
            "day_gap": day_gap,
            "date_label": target_date.strftime("%A %B %d, %Y"),
            "vix_on_day": round(vix_on_day, 1),
            "results": results, "dir_acc": round(dir_acc,1), "avg_err": round(avg_err,1),
        }


    def render_backtest_day(bt, uw_data):
        if bt is None:
            st.warning("No intraday 5-min data available for this day.")
            return

        move_c     = "#f87171" if bt['day_move'] < 0 else "#4ade80"
        uw_bias_c  = ("#4ade80" if uw_data and uw_data["bias"]=="Bullish" else "#f87171") if uw_data else "#64748b"
        b1,b2,b3,b4,b5,b6 = st.columns(6)
        for col, lbl, val, vc in [
            (b1, "SSR (Prior eve)",  f"{bt['score']}/100",    bt['color']),
            (b2, "Algo Rating",      bt['rating'].split()[-1], bt['color']),
            (b3, "Actual Move",      f"{bt['day_move']:+.1f} pts", move_c),
            (b4, "Dir Accuracy",     f"{bt['dir_acc']}%",
                 "#4ade80" if bt['dir_acc'] >= 60 else "#f59e0b"),
            (b5, "Avg Price Error",  f"±{bt['avg_err']} pts", "#94a3b8"),
            (b6, "UW Flow Bias",
                 uw_data["bias"] if uw_data else "N/A",
                 uw_bias_c),
        ]:
            col.markdown(
                f'<div class="metric-tile"><div class="metric-label">{lbl}</div>'
                f'<div style="font-size:18px;font-weight:800;color:{vc}">{val}</div></div>',
                unsafe_allow_html=True)

        st.markdown("<div style='margin:6px 0'></div>", unsafe_allow_html=True)
        bc1, bc2 = st.columns([1.7, 1])

        with bc1:
            rows_html = ""
            for r in bt["results"]:
                win_tc  = BIAS_TEXT.get(r["bias"], "#94a3b8")
                hit     = r["correct"]
                hit_bg  = "rgba(34,197,94,0.12)" if hit else "rgba(248,113,113,0.10)"
                tick    = "✅" if hit else "❌"
                err_c   = "#4ade80" if abs(r["err"]) <= bt["atr"]*0.15 else ("#f59e0b" if abs(r["err"]) <= bt["atr"]*0.3 else "#f87171")
                sign    = "+" if r["err"] >= 0 else ""
                ad_icon = "🟢" if r["actual_dir"]=="bull" else ("🔴" if r["actual_dir"]=="bear" else "⚪")
                rows_html += (
                    f'<tr style="background:{hit_bg};border-bottom:1px solid #1a1f33">'
                    f'<td style="padding:5px 10px;color:#94a3b8;font-size:12px">{to_ampm(r["slot"])}</td>'
                    f'<td style="padding:5px 8px;font-size:11px;color:{win_tc}">{BIAS_COLOR.get(r["bias"],"")} {r["label"]}</td>'
                    f'<td style="padding:5px 8px;font-weight:700;color:#94a3b8">{r["proj"]:,}</td>'
                    f'<td style="padding:5px 8px;font-weight:700;color:#f1f5f9">{r["actual"]:,}</td>'
                    f'<td style="padding:5px 8px;color:{err_c}">{sign}{r["err"]}</td>'
                    f'<td style="padding:5px 8px">{ad_icon}</td>'
                    f'<td style="padding:5px 10px;font-size:14px">{tick}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:14px 16px;border:1px solid #2d3250">'
                f'<div style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:8px">'
                f'Projected vs Actual — {bt["date_label"]}'
                f' &nbsp;·&nbsp; Prior Close: {bt["prev_close"]:.1f}'
                f' &nbsp;·&nbsp; Gap: {"+" if bt["day_gap"]>=0 else ""}{bt["day_gap"]:.1f} pts'
                f' &nbsp;·&nbsp; Proj Base: {bt["day_open"]:.1f} ({"open anchored" if abs(bt["day_gap"])>20 else "prior close"})'
                f' &nbsp;·&nbsp; ATR: {bt["atr"]}</div>'
                f'<div style="overflow-y:auto;max-height:370px">'
                f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9;font-size:12px">'
                f'<thead><tr style="background:#0f1117">'
                f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">TIME</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">WINDOW</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">PROJ</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">ACTUAL</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">ERR</th>'
                f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">DIR</th>'
                f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">HIT</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
                f'<div style="margin-top:8px;font-size:10px;color:#475569">'
                f'✅ Hit = direction matched &nbsp;·&nbsp; Error = actual − projected</div>'
                f'</div>',
                unsafe_allow_html=True)

        with bc2:
            result_bg = "#0d2010" if bt['day_move'] < 0 else "#0a1f10"
            uw_html   = ""
            if uw_data:
                pcr_c   = "#f87171" if uw_data["pcr"] > 1.0 else "#4ade80"
                uw_html = (
                    f'<div style="margin-top:10px;padding:8px;background:#0d1520;border-radius:6px">'
                    f'<div style="font-size:10px;color:#64748b;margin-bottom:4px">🦅 Unusual Whales Flow</div>'
                    f'<div style="display:flex;gap:10px;font-size:12px">'
                    f'<span style="color:#4ade80">🟢 Bull ${uw_data["bull"]}M</span>'
                    f'<span style="color:#f87171">🔴 Bear ${uw_data["bear"]}M</span></div>'
                    f'<div style="margin-top:4px;font-size:12px">'
                    f'P/C Ratio: <span style="color:{pcr_c};font-weight:700">{uw_data["pcr"]}</span>'
                    f' &nbsp; Bias: <span style="color:{move_c};font-weight:700">{uw_data["bias"]}</span>'
                    f'</div></div>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:14px 16px;border:1px solid #2d3250">'
                f'<div style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:8px">Day Summary</div>'
                f'<div style="padding:10px;background:{result_bg};border-radius:8px">'
                f'<div style="font-size:10px;color:#64748b">Actual Result</div>'
                f'<div style="font-size:17px;font-weight:800;color:{move_c};margin:3px 0">'
                f'SPX {bt["day_open"]:,.1f} → {bt["day_eod"]:,.1f}</div>'
                f'<div style="color:{move_c};font-size:13px">{bt["day_move"]:+.1f} pts on the day</div>'
                f'</div>'
                f'{uw_html}'
                f'<div style="margin-top:10px;padding:8px;background:#0a0e1a;border-radius:6px">'
                f'<div style="font-size:10px;color:#64748b;margin-bottom:4px">SSR Summary</div>'
                f'<div style="font-size:12px;color:#cbd5e1">'
                f'Score: <b>{bt["score"]}/100</b> &nbsp; '
                f'Buys: <span style="color:#4ade80">{bt["buys"]}</span> &nbsp; '
                f'Sells: <span style="color:#f87171">{bt["sells"]}</span></div>'
                f'</div></div>',
                unsafe_allow_html=True)


    # ═══════════════════════════════════════════════════════════════════════════════
    # NEWS & ECONOMIC CALENDAR PANEL
    # ═══════════════════════════════════════════════════════════════════════════════
    _nc1, _nc2 = st.columns([1.6, 1])

    with _nc1:
        # ── Breaking News Feed ──────────────────────────────────────────────────
        articles = news_data.get("articles", [])
        comp     = news_data.get("composite_score", 0.0)
        comp_lbl = news_data.get("label", "⚪ Neutral")
        comp_c   = "#4ade80" if comp > 0.10 else ("#f87171" if comp < -0.10 else "#94a3b8")

        # Category → display label + color
        _CAT_DISPLAY = {
            "OIL_SUPPLY_SHOCK":           ("🛢️ OIL SHOCK",      "#f59e0b"),
            "OIL_DROP":                   ("🛢️ OIL DROP",       "#4ade80"),
            "OIL_SPIKE":                  ("🛢️ OIL SPIKE",      "#f87171"),
            "OIL_DEMAND_DROP":            ("🛢️ OIL DEMAND↓",    "#f87171"),
            "OIL_DEMAND_SURGE":           ("🛢️ OIL DEMAND↑",    "#4ade80"),
            "US_IRAN_WAR":                ("🚨 US-IRAN WAR",     "#b91c1c"),
            "IRAN_ESCALATION":            ("⚔️ IRAN",            "#f87171"),
            "IRAN_DEESCALATION":          ("🕊️ IRAN DEAL",       "#4ade80"),
            "RUSSIA_GEO":                 ("⚔️ RUSSIA",          "#f87171"),
            "CHINA_TAIWAN":               ("⚔️ TAIWAN",          "#f87171"),
            "PAKISTAN_MEDIATION_PROGRESS":("🕊️ PAK MEDIATION",  "#4ade80"),
            "GEO_DEESCALATION":           ("🕊️ DE-ESCAL",        "#4ade80"),
            "TARIFF_BEARISH":             ("🚧 TARIFF",          "#f87171"),
            "TARIFF_BULLISH":             ("🤝 TRADE DEAL",      "#4ade80"),
            "FED_DOVISH":                 ("🏦 FED DOVE",        "#4ade80"),
            "FED_HAWKISH":                ("🏦 FED HAWK",        "#f87171"),
            "CPI_HOT":                    ("📊 CPI HOT",         "#f87171"),
            "CPI_COOL":                   ("📊 CPI COOL",        "#4ade80"),
            "JOBS_STRONG":                ("👷 JOBS+",           "#f59e0b"),
            "JOBS_WEAK":                  ("👷 JOBS−",           "#f59e0b"),
            "BANK_CRISIS":                ("🏦 BANK CRISIS",     "#b91c1c"),
            "CREDIT_DOWNGRADE":           ("📉 DOWNGRADE",       "#f87171"),
            "FISCAL_CRISIS":              ("🏛️ FISCAL",          "#f87171"),
            "FISCAL_RESOLUTION":          ("🏛️ FISCAL OK",       "#4ade80"),
            "YIELD_SPIKE":                ("📈 YIELD↑",          "#f87171"),
            "YIELD_DROP":                 ("📈 YIELD↓",          "#4ade80"),
            "EARNINGS_BEAT":              ("💹 EARN BEAT",       "#4ade80"),
            "EARNINGS_MISS":              ("💹 EARN MISS",       "#f87171"),
            "RECESSION_FEAR":             ("🔻 RECESSION",       "#f87171"),
            "GROWTH_STRONG":              ("📈 GROWTH",          "#4ade80"),
            "GENERIC":                    ("",                   "#475569"),
        }
        # Manual refresh button — clears news cache for instant update
        _fetched_at = news_data.get("fetched_at", "")
        _ncol1, _ncol2 = st.columns([3, 1])
        with _ncol1:
            st.markdown(
                f'<div style="font-size:10px;color:#475569;margin-bottom:4px">'
                f'Last fetched: {_fetched_at} · auto-refresh every 90s · '
                f'{len(articles)} articles · ForexLive / FinancialJuice / BBC-ME / JPost / AlJazeera / OilPrice / CNBC / AP</div>',
                unsafe_allow_html=True)
        with _ncol2:
            if st.button("🔄 Refresh News", key="news_refresh", use_container_width=True):
                load_news.clear()
                st.rerun()

        rows_html = ""
        for a in articles[:20]:
            sc    = a["score"]
            sc_c  = "#4ade80" if sc > 0.10 else ("#f87171" if sc < -0.10 else "#64748b")
            badge = "🟢" if sc > 0.10 else ("🔴" if sc < -0.10 else "⚪")
            src   = a.get("source","")[:14]
            t_    = a.get("time","")[:16]
            cat   = a.get("category","GENERIC")
            note  = a.get("note","")
            iw    = a.get("impact_weight", 1.0)
            cat_lbl, cat_c = _CAT_DISPLAY.get(cat, ("", "#475569"))
            cat_tag = (f' <span style="background:#1a1a2e;color:{cat_c};font-size:9px;'
                       f'padding:1px 5px;border-radius:3px;font-weight:700">{cat_lbl}</span>'
                       if cat_lbl else "")
            # High-weight items get a causal note tooltip-style row
            note_row = (f'<div style="font-size:9px;color:#475569;margin-top:1px;'
                        f'font-style:italic">{note}</div>' if note and iw >= 2.5 else "")
            rows_html += (
                f'<div style="padding:6px 8px;border-bottom:1px solid #1a1f33;display:flex;gap:8px;align-items:flex-start">'
                f'<span style="font-size:14px;flex-shrink:0;margin-top:1px">{badge}</span>'
                f'<div style="flex:1;min-width:0">'
                f'<div style="font-size:12px;color:#e2e8f0;line-height:1.3">{a["title"][:120]}{cat_tag}</div>'
                f'{note_row}'
                f'<div style="font-size:10px;color:#475569;margin-top:2px">{src} · {t_} · wt:{iw:.1f}</div>'
                f'</div>'
                f'<span style="font-size:11px;color:{sc_c};font-weight:700;flex-shrink:0">{sc:+.2f}</span>'
                f'</div>'
            )
        if not rows_html:
            rows_html = '<div style="padding:16px;color:#475569;font-size:12px;text-align:center">News unavailable — check connection</div>'

        # Top-impact alert banner — escalates visually for weight ≥ 4.0 events
        _top = news_data.get("top_impact")
        _top_html = ""
        if _top and _top.get("note"):
            _tw  = _top.get("weight", 1.0)
            _tc  = "#f87171" if comp < -0.05 else "#4ade80" if comp > 0.05 else "#f59e0b"
            if _tw >= 4.0:
                # High-severity: full-width red alert with flashing border
                _alert_bg  = "#1a0505" if comp < 0 else "#051a05"
                _alert_bdr = "#b91c1c" if comp < 0 else "#15803d"
                _alert_ico = "🚨" if comp < 0 else "🟢"
                _cat_disp  = _CAT_DISPLAY.get(_top["category"], (_top["category"], _tc))[0]
                _top_html  = (
                    f'<div style="margin:6px 10px 2px;padding:10px 12px;background:{_alert_bg};'
                    f'border:2px solid {_alert_bdr};border-radius:6px">'
                    f'<div style="font-size:12px;font-weight:800;color:{_alert_bdr};letter-spacing:0.5px">'
                    f'{_alert_ico} HIGH-IMPACT EVENT · {_cat_disp} · weight {_tw:.1f}</div>'
                    f'<div style="font-size:12px;color:#f1f5f9;margin-top:3px;font-weight:600">'
                    f'{_top.get("title","")}</div>'
                    f'<div style="font-size:10px;color:#94a3b8;margin-top:3px;font-style:italic">'
                    f'{_top["note"]}</div>'
                    f'</div>'
                )
            else:
                _top_html = (
                    f'<div style="margin:6px 10px;padding:8px 10px;background:#0d1520;'
                    f'border-left:3px solid {_tc};border-radius:4px;font-size:11px">'
                    f'<span style="color:{_tc};font-weight:700">⚡ TOP IMPACT [{_top["category"]}]</span>'
                    f'<div style="color:#94a3b8;margin-top:2px">{_top["note"]}</div>'
                    f'</div>'
                )

        st.markdown(
            f'<div style="background:#1e2130;border-radius:10px;border:1px solid #2d3250;margin-bottom:14px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px 6px">'
            f'<span style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase">📰 Market News · Causal-Chain Scored</span>'
            f'<span style="font-size:12px;font-weight:700;color:{comp_c}">Composite: {comp_lbl} ({comp:+.3f})</span>'
            f'</div>'
            f'{_top_html}'
            f'<div style="overflow-y:auto;max-height:360px">{rows_html}</div>'
            f'</div>',
            unsafe_allow_html=True)

    with _nc2:
        # ── Economic Calendar ───────────────────────────────────────────────────
        today_str = date.today().strftime("%Y-%m-%d")
        cal_rows  = ""
        for (ev_date, ev_label, ev_type, ev_impact) in today_events:
            d = datetime.strptime(ev_date, "%Y-%m-%d").date()
            days_away = (d - date.today()).days
            if days_away == 0:
                day_tag = '<span style="background:#7f1d1d;color:#fca5a5;font-size:9px;padding:1px 6px;border-radius:3px;font-weight:700">TODAY</span>'
            elif days_away == 1:
                day_tag = '<span style="background:#1c1917;color:#f59e0b;font-size:9px;padding:1px 6px;border-radius:3px">TOMORROW</span>'
            else:
                day_tag = f'<span style="color:#64748b;font-size:10px">{d.strftime("%a %b %d")}</span>'
            icon    = _EVENT_ICON.get(ev_type, "📌")
            imp_c   = _EVENT_COLOR.get(ev_impact, "#94a3b8")
            cal_rows += (
                f'<div style="padding:8px 12px;border-bottom:1px solid #1a1f33;display:flex;gap:10px;align-items:center">'
                f'<span style="font-size:18px">{icon}</span>'
                f'<div style="flex:1">'
                f'<div style="font-size:12px;color:#e2e8f0;font-weight:600">{ev_label}</div>'
                f'<div style="margin-top:3px">{day_tag}</div>'
                f'</div>'
                f'<span style="font-size:10px;color:{imp_c};font-weight:700">{ev_impact}</span>'
                f'</div>'
            )
        if not cal_rows:
            cal_rows = '<div style="padding:16px;color:#475569;font-size:12px;text-align:center">No major events this week</div>'

        # Today's event impact on trading
        today_types = {e[2] for e in today_events if e[0] == today_str}
        event_note  = ""
        if "FOMC" in today_types:
            event_note = '<div style="margin:8px 12px;padding:8px;background:#2d1b00;border-radius:6px;font-size:11px;color:#f59e0b">⚡ <b>FOMC Day</b> — Windows 9:30–2:00 PM forced to CHOP. Expect explosion after 2 PM announcement.</div>'
        elif "CPI" in today_types:
            event_note = '<div style="margin:8px 12px;padding:8px;background:#1a1233;border-radius:6px;font-size:11px;color:#a78bfa">📊 <b>CPI Day</b> — First 30 min unreliable (knee-jerk reversal common). Wait for 10:00 AM re-test.</div>'
        elif "NFP" in today_types:
            event_note = '<div style="margin:8px 12px;padding:8px;background:#0d2010;border-radius:6px;font-size:11px;color:#4ade80">👷 <b>NFP Day</b> — First 30 min is noise. True direction sets after 10:00 AM.</div>'

        st.markdown(
            f'<div style="background:#1e2130;border-radius:10px;border:1px solid #2d3250;margin-bottom:14px">'
            f'<div style="padding:10px 14px 6px;font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase">📅 Economic Calendar — Next 5 Days</div>'
            f'{event_note}'
            f'<div style="overflow-y:auto;max-height:280px">{cal_rows}</div>'
            f'</div>',
            unsafe_allow_html=True)

def aggregate_window_stats(all_bt_results):
    """
    Aggregate per-window directional accuracy across all backtest days,
    broken down by VIX regime and gap regime.
    Returns dict: label → {bias, correct, total, vix_low, vix_mid, vix_high, gap_up, gap_flat, gap_down}
    """
    stats = {}
    for bt in all_bt_results:
        if bt is None:
            continue
        vix_day = bt.get("vix_on_day", 0.0)
        gap_day = bt.get("day_gap", 0.0)
        vix_key = "vix_high" if vix_day > VIX_FEAR_THRESHOLD else ("vix_low" if vix_day < VIX_CALM_THRESHOLD else "vix_mid")
        gap_key = "gap_up"   if gap_day > GAP_THRESHOLD else ("gap_down" if gap_day < -GAP_THRESHOLD else "gap_flat")

        for r in bt["results"]:
            # Key by FULL label (including override suffix like "(hi-VIX→bear)") so that
            # different effective biases on the same base window are tracked separately.
            # Previously stripping the suffix merged bull/bear/chop variants under one row,
            # making accuracy stats unreliable for overridden regimes (peer review finding #5).
            full_lbl = r["label"]
            base_lbl = r["label"].split(" (")[0]   # kept for lookup fallback in windows_html
            if full_lbl not in stats:
                stats[full_lbl] = {
                    "bias": r["bias"], "base_label": base_lbl,
                    "correct": 0, "total": 0,
                    "vix_low":  {"c": 0, "t": 0},
                    "vix_mid":  {"c": 0, "t": 0},
                    "vix_high": {"c": 0, "t": 0},
                    "gap_up":   {"c": 0, "t": 0},
                    "gap_flat": {"c": 0, "t": 0},
                    "gap_down": {"c": 0, "t": 0},
                }
            s = stats[full_lbl]
            s["total"] += 1
            if r["correct"]:
                s["correct"] += 1
            s[vix_key]["t"] += 1
            if r["correct"]:
                s[vix_key]["c"] += 1
            s[gap_key]["t"] += 1
            if r["correct"]:
                s[gap_key]["c"] += 1
    return stats


def _pct(d):
    """Format accuracy dict {c,t} as percentage string."""
    if d["t"] == 0: return "—"
    return f"{int(d['c']/d['t']*100)}%"

def _acc_color(d):
    if d["t"] == 0: return "#64748b"
    v = d["c"] / d["t"] * 100
    return "#4ade80" if v >= 65 else ("#f59e0b" if v >= 45 else "#f87171")



with _tab_research:
    # ═══════════════════════════════════════════════════════════════════════════════
    # SELF-IMPROVEMENT — Today's Live Prediction Accuracy
    # Compares this session's window predictions vs actual SPX intraday moves.
    # Refreshes every page reload (every 3 min) — accuracy improves as day progresses.
    # ═══════════════════════════════════════════════════════════════════════════════
    with st.expander("🧠 Self-Improvement — Today's Live Prediction Accuracy (click to expand)", expanded=False):
        @st.cache_data(ttl=180)
        def load_today_5m():
            """Fetch today's 5-min SPX bars for live accuracy scoring."""
            try:
                df = yf.download("^GSPC", period="1d", interval="5m",
                                 progress=False, auto_adjust=True)
                df.index = df.index.tz_convert(EST)
                return df
            except Exception:
                return pd.DataFrame()

        _today_5m = load_today_5m()
        # Upper bound added: after 4 PM, market is closed and "today's" 5-min data is stale.
        _rth_open  = EST.localize(datetime(now_est.year, now_est.month, now_est.day, 9, 30))
        _rth_close = EST.localize(datetime(now_est.year, now_est.month, now_est.day, 16, 0))
        _today_is_rth = (now_est.weekday() < 5 and _rth_open <= now_est <= _rth_close)

        if _today_5m.empty or not _today_is_rth:
            st.markdown(
                '<div style="padding:14px;color:#475569;font-size:12px;text-align:center">'
                'Live accuracy available during market hours (Mon–Fri 9:30 AM+ EST).'
                '</div>', unsafe_allow_html=True)
        else:
            _5m_close = _today_5m["Close"].squeeze()
            if isinstance(_5m_close, pd.DataFrame): _5m_close = _5m_close.iloc[:, 0]
            _5m_open  = float(_5m_close.iloc[0]) if len(_5m_close) else spx_price

            def _snap_at(hhmm):
                hh, mm = map(int, hhmm.split(":"))
                _mask = ((_today_5m.index.hour == hh) &
                         (_today_5m.index.minute >= mm))
                _s = _today_5m[_mask]["Close"].squeeze()
                if isinstance(_s, pd.DataFrame): _s = _s.iloc[:, 0]
                return round(float(_s.iloc[0]), 1) if len(_s) else None

            _slots = ["09:30","10:00","10:30","11:00","11:30","12:00",
                      "13:00","13:30","14:00","14:30","15:00","15:30","16:00"]
            # live_gap = today's daily Open − prior Close (computed once at page load,
            # reused here so window_bias_at() sees the same gap regime as projections do)
            # Adaptive per-slot ATR fractions — front-loaded morning volatility,
            # matching the projection function profile (avoids flat 1/6.5 fallback
            # which over-classifies morning moves as "chop" and afternoon as "directional").
            _live_slot_atr_fracs = {
                "09:30": 0.28, "10:00": 0.18, "10:30": 0.12, "11:00": 0.10, "11:30": 0.09,
                "12:00": 0.09, "13:00": 0.09, "13:30": 0.11, "14:00": 0.10, "14:30": 0.09,
                "15:00": 0.08, "15:30": 0.05, "16:00": 0.05,
            }

            _today_results = []
            # Anchor first slot direction comparison against prior session close
            # (same reference the day backtest uses), not the 9:30 bar's own close.
            _prev_actual   = _session_prior_close
            for _sl in _slots:
                _wb, _wl = window_bias_at(_sl, gap=live_gap, vix=vix_now, news_score=_news_comp, orb_status=_orb_status, opex=_opex_week, orb_range_atr=_orb_range_atr, atr=levels["atr"])
                _actual  = _snap_at(_sl)
                if _actual is None:
                    continue
                _actual_dir = "bull" if _actual > _prev_actual else ("bear" if _actual < _prev_actual else "chop")
                # Adaptive per-slot ATR: morning slots get larger threshold (0.28× daily ATR),
                # afternoon slots get smaller (0.05–0.09×). Avoids inflating morning accuracy.
                _s_atr  = levels["atr"] * _live_slot_atr_fracs.get(_sl, 0.077)
                _chop_t = _s_atr * min(0.6, 0.30 * max(1.0, vix_now / 20.0))
                _flat       = abs(_actual - _prev_actual) < _chop_t
                _correct    = ((_wb == "bear" and _actual_dir == "bear") or
                               (_wb == "bull" and _actual_dir == "bull") or
                               (_wb == "chop" and _flat))
                _today_results.append({
                    "slot": _sl, "bias": _wb, "label": _wl,
                    "actual": _actual, "actual_dir": _actual_dir,
                    "correct": _correct, "prev": _prev_actual,
                })
                _prev_actual = _actual

            if _today_results:
                _hits  = sum(1 for r in _today_results if r["correct"])
                _total = len(_today_results)
                _acc   = int(_hits / _total * 100)
                _acc_c = "#4ade80" if _acc >= 65 else ("#f59e0b" if _acc >= 45 else "#f87171")

                _rows_html = ""
                for r in _today_results:
                    _tc   = BIAS_TEXT.get(r["bias"], "#94a3b8")
                    _tick = "✅" if r["correct"] else "❌"
                    _ad   = "🟢" if r["actual_dir"] == "bull" else ("🔴" if r["actual_dir"] == "bear" else "⚪")
                    _bg   = "rgba(34,197,94,0.10)" if r["correct"] else "rgba(248,113,113,0.08)"
                    _rows_html += (
                        f'<tr style="background:{_bg};border-bottom:1px solid #1a1f33">'
                        f'<td style="padding:5px 10px;color:#94a3b8;font-size:12px">{to_ampm(r["slot"])}</td>'
                        f'<td style="padding:5px 8px;font-size:11px;color:{_tc}">{BIAS_COLOR.get(r["bias"],"")} {r["label"][:30]}</td>'
                        f'<td style="padding:5px 8px;font-weight:700;color:#f1f5f9">{r["actual"]:,}</td>'
                        f'<td style="padding:5px 8px;font-size:13px">{_ad}</td>'
                        f'<td style="padding:5px 10px;font-size:14px">{_tick}</td>'
                        f'</tr>'
                    )

                # Partial-day insight — flag underperforming windows without overclaiming
                _worst  = [r["label"][:20] for r in _today_results if not r["correct"]]
                _insight = ""
                if len(_worst) >= 3:
                    _insight = (f'<div style="margin-top:8px;padding:8px;background:#1a1000;border-radius:6px;font-size:11px;color:#f59e0b">'
                                f'⚠️ Misses so far today: {", ".join(set(_worst[:4]))} — small sample, check end-of-day</div>')
                elif _acc >= 70:
                    _insight = (f'<div style="margin-top:8px;padding:8px;background:#0d2010;border-radius:6px;font-size:11px;color:#4ade80">'
                                f'✅ Strong so far today ({_hits}/{_total} windows) — partial session, not a complete sample</div>')

                st.markdown(
                    f'<div style="background:#1e2130;border-radius:10px;border:1px solid #2d3250;padding:14px 16px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
                    f'<span style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase">'
                    f'📍 Today So Far — {now_est.strftime("%A %b %d")} &nbsp;·&nbsp; n={_total} slots</span>'
                    f'<span style="font-size:18px;font-weight:800;color:{_acc_c}">{_acc}% &nbsp;<span style="font-size:12px;color:#64748b">({_hits}/{_total})</span></span>'
                    f'</div>'
                    f'<div style="overflow-y:auto;max-height:300px">'
                    f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9;font-size:12px">'
                    f'<thead><tr style="background:#0f1117">'
                    f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">TIME</th>'
                    f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">PREDICTED</th>'
                    f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">SPX</th>'
                    f'<th style="padding:5px 8px;text-align:left;color:#64748b;font-size:10px">ACTUAL DIR</th>'
                    f'<th style="padding:5px 10px;text-align:left;color:#64748b;font-size:10px">HIT</th>'
                    f'</tr></thead><tbody>{_rows_html}</tbody></table></div>'
                    f'{_insight}'
                    f'<div style="margin-top:8px;font-size:10px;color:#475569">'
                    f'Partial-day sample — resets each session. '
                f'Slots: 09:30 10:00 10:30 11:00 11:30 12:00 13:00 13:30 14:00 14:30 15:00 15:30 16:00. '
                f'Uses full live model (gap, VIX, events, OpEx, news, ORB). For multi-day stats see Research tab. Updates every 3 min.'
                    f'</div></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="padding:14px;color:#475569;font-size:12px;text-align:center">'
                    'Waiting for intraday bars — check back after 9:35 AM EST.'
                    '</div>', unsafe_allow_html=True)

    with st.expander("🔬 Backtest — Last 10 Trading Days  (click to expand)", expanded=False):
        st.caption(
            "SSR: price + VIX + sector data only (no PCR, macro, A/D, or overnight signals). "
            "Window bias: historical gap, VIX, calendar events (FOMC/CPI/NFP), weekday, OpEx. "
            "Not validated: ORB width/distance, news sentiment, intraday RSI override (live-only features). "
            "Slot grid: 09:30 10:00 10:30 10:45 11:00 11:15 11:30 12:00 13:00 13:15 13:30 14:00 14:30 15:00 15:30 16:00."
        )
        spx_d_bt, vix_d_bt, sectors_d_bt, day_series_bt, trading_days_bt, day_open_series_bt = load_backtest_data()

        last5 = trading_days_bt[-10:] if len(trading_days_bt) >= 10 else trading_days_bt
        daily_dates_list = list(spx_d_bt.index.date)
        total_daily      = len(spx_d_bt)

        # Pre-compute offset per day: rows to trim so last row = prior day's close
        offsets = {}
        for td in last5:
            try:
                pos = daily_dates_list.index(td)
                offsets[td] = total_daily - pos   # drop from pos onward
            except ValueError:
                offsets[td] = 1

        # Summary grid — 2 rows of 5 for 10 days
        def render_day_tile(col, td, ds):
            if ds is not None and len(ds) > 0:
                d_open  = float(ds.iloc[0])
                d_close = float(ds.iloc[-1])
                d_move  = round(d_close - d_open, 1)
                mc = "#f87171" if d_move < 0 else "#4ade80"
                col.markdown(
                    f'<div class="metric-tile" style="text-align:center">'
                    f'<div class="metric-label">{td.strftime("%a %b %d")}</div>'
                    f'<div style="font-size:16px;font-weight:800;color:{mc}">{d_move:+.1f}</div>'
                    f'<div style="font-size:10px;color:#64748b">{d_open:,.0f}→{d_close:,.0f}</div>'
                    f'</div>', unsafe_allow_html=True)
            else:
                col.markdown(
                    f'<div class="metric-tile" style="text-align:center">'
                    f'<div class="metric-label">{td.strftime("%a %b %d")}</div>'
                    f'<div style="font-size:11px;color:#64748b">No data</div></div>',
                    unsafe_allow_html=True)

        if last5:
            # Row 1: older 5 days
            row1 = last5[:5]
            cols1 = st.columns(5)
            for col, td in zip(cols1, row1):
                render_day_tile(col, td, day_series_bt.get(td))
            # Row 2: more recent 5 days
            if len(last5) > 5:
                row2 = last5[5:]
                cols2 = st.columns(5)
                for col, td in zip(cols2, row2):
                    render_day_tile(col, td, day_series_bt.get(td))

        st.markdown("<hr style='border:1px solid #1e2130;margin:12px 0'>", unsafe_allow_html=True)

        # Pre-compute all day results (needed for calibration table)
        all_bt_results = []
        for td in last5:
            all_bt_results.append(
                run_backtest_for_day(td, day_series_bt, spx_d_bt, vix_d_bt,
                                     sectors_d_bt, daily_dates_list, offsets.get(td, 1),
                                     day_open_series=day_open_series_bt)
            )

        # Per-day tabs
        tab_labels = [td.strftime("%a %b %d") for td in last5]
        tabs = st.tabs(tab_labels)
        for tab, td, bt_day in zip(tabs, last5, all_bt_results):
            with tab:
                uw_day = load_uw_market_tide(td.strftime("%Y-%m-%d"))
                render_backtest_day(bt_day, uw_day)

        # ── Algorithm Calibration Table ──────────────────────────────────────────
        st.markdown("<hr style='border:1px solid #1e2130;margin:18px 0'>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:11px;color:#64748b;letter-spacing:1.4px;"
            "text-transform:uppercase;margin-bottom:10px'>📐 Window Calibration — Accuracy by VIX Regime & Gap</div>",
            unsafe_allow_html=True)

        w_stats = aggregate_window_stats(all_bt_results)

        if w_stats:
            # Overall accuracy across all windows
            total_c = sum(s["correct"] for s in w_stats.values())
            total_t = sum(s["total"]   for s in w_stats.values())
            overall_acc = int(total_c / total_t * 100) if total_t else 0
            overall_c   = "#4ade80" if overall_acc >= 65 else ("#f59e0b" if overall_acc >= 45 else "#f87171")

            st.markdown(
                f'<div style="margin-bottom:10px;font-size:13px;color:#94a3b8">'
                f'Overall directional accuracy across all windows & days: '
                f'<b style="color:{overall_c};font-size:16px">{overall_acc}%</b> '
                f'({total_c}/{total_t} correct)</div>',
                unsafe_allow_html=True)

            BIAS_ICON = {"bull": "🟢", "bear": "🔴", "chop": "⚪", "neutral": "⚪"}
            header = (
                '<table style="width:100%;border-collapse:collapse;font-size:12px;color:#f1f5f9">'
                '<thead><tr style="background:#0f1117">'
                '<th style="padding:6px 10px;text-align:left;color:#64748b;font-size:10px">WINDOW</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">BIAS</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">OVERALL</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">VIX&lt;18</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">VIX 18-25</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">VIX&gt;25</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">GAP UP</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">FLAT</th>'
                '<th style="padding:6px 8px;text-align:center;color:#64748b;font-size:10px">GAP DOWN</th>'
                '<th style="padding:6px 10px;text-align:left;color:#64748b;font-size:10px">INSIGHT</th>'
                '</tr></thead><tbody>'
            )
            rows_html = ""
            for lbl, s in w_stats.items():
                ov_pct = int(s["correct"]/s["total"]*100) if s["total"] else 0
                ov_c   = "#4ade80" if ov_pct >= 65 else ("#f59e0b" if ov_pct >= 45 else "#f87171")
                bias_icon = BIAS_ICON.get(s["bias"], "⚪")

                # Auto-generate insight
                insight = ""
                if s["vix_high"]["t"] >= 3:
                    vh = int(s["vix_high"]["c"]/s["vix_high"]["t"]*100)
                    if vh < 45 and s["bias"] != "bear":
                        insight = f'⚠️ Only {vh}% on VIX>25 — consider bear override'
                    elif vh >= 70 and s["bias"] == "bear":
                        insight = f'✅ Strong {vh}% bear hit on VIX>25'
                if not insight and s["gap_up"]["t"] >= 3:
                    gu = int(s["gap_up"]["c"]/s["gap_up"]["t"]*100)
                    if gu < 40:
                        insight = f'⚠️ Only {gu}% on gap-up days'
                if not insight and ov_pct >= 70:
                    insight = "✅ Reliable"
                if not insight and ov_pct < 40 and s["total"] >= 5:
                    insight = "🔴 Flip or skip this window"

                rows_html += (
                    f'<tr style="border-bottom:1px solid #1a1f33">'
                    f'<td style="padding:5px 10px;font-size:12px">{lbl}</td>'
                    f'<td style="padding:5px 8px;text-align:center">{bias_icon} {s["bias"]}</td>'
                    f'<td style="padding:5px 8px;text-align:center;font-weight:700;color:{ov_c}">{ov_pct}% <span style="font-size:10px;color:#475569">({s["correct"]}/{s["total"]})</span></td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["vix_low"])}">{_pct(s["vix_low"])}</td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["vix_mid"])}">{_pct(s["vix_mid"])}</td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["vix_high"])}">{_pct(s["vix_high"])}</td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["gap_up"])}">{_pct(s["gap_up"])}</td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["gap_flat"])}">{_pct(s["gap_flat"])}</td>'
                    f'<td style="padding:5px 8px;text-align:center;color:{_acc_color(s["gap_down"])}">{_pct(s["gap_down"])}</td>'
                    f'<td style="padding:5px 10px;font-size:11px;color:#94a3b8">{insight}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:14px 16px;'
                f'border:1px solid #2d3250;overflow-x:auto">'
                f'{header}{rows_html}</tbody></table></div>',
                unsafe_allow_html=True)

            st.markdown(
                '<div style="font-size:10px;color:#475569;margin-top:6px">'
                '🟢 ≥65% accurate &nbsp;·&nbsp; 🟡 45-64% &nbsp;·&nbsp; 🔴 &lt;45% &nbsp;·&nbsp; '
                'VIX&lt;18 = calm/range-bound &nbsp;·&nbsp; VIX&gt;25 = fear/trending &nbsp;·&nbsp; '
                'Minimum 3 samples shown per cell</div>',
                unsafe_allow_html=True)

        # ── Day-of-week accuracy ──────────────────────────────────────────────
        st.markdown("<hr style='border:1px solid #1e2130;margin:18px 0'>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:11px;color:#64748b;letter-spacing:1.4px;"
            "text-transform:uppercase;margin-bottom:10px'>📆 Day-of-Week Directional Accuracy</div>",
            unsafe_allow_html=True)
        _dow_names = {0:"Monday",1:"Tuesday",2:"Wednesday",3:"Thursday",4:"Friday"}
        _dow_acc   = {d:{"hits":0,"total":0,"moves":[]} for d in range(5)}
        for _bt_r in all_bt_results:
            if _bt_r is None: continue
            _wd = _bt_r.get("date_label","")
            # Recover weekday from date_label (e.g. "Monday March 24, 2026")
            try:
                _d_obj = datetime.strptime(_bt_r["date_label"], "%A %B %d, %Y")
                _wd_i  = _d_obj.weekday()
            except Exception:
                continue
            _hits_day = sum(1 for r in _bt_r["results"] if r["correct"])
            _tot_day  = len(_bt_r["results"])
            if _tot_day == 0: continue
            _dow_acc[_wd_i]["hits"]  += _hits_day
            _dow_acc[_wd_i]["total"] += _tot_day
            _dow_acc[_wd_i]["moves"].append(_bt_r.get("day_move", 0))

        _dow_cols = st.columns(5)
        for _di, _col in zip(range(5), _dow_cols):
            _da = _dow_acc[_di]
            if _da["total"] == 0:
                _col.markdown(
                    f'<div class="metric-tile" style="text-align:center">'
                    f'<div class="metric-label">{_dow_names[_di][:3]}</div>'
                    f'<div style="font-size:11px;color:#64748b">No data</div></div>',
                    unsafe_allow_html=True)
            else:
                _da_pct = int(_da["hits"] / _da["total"] * 100)
                _da_c   = "#4ade80" if _da_pct >= 65 else ("#f59e0b" if _da_pct >= 45 else "#f87171")
                _avg_mv = round(sum(_da["moves"]) / len(_da["moves"]), 1) if _da["moves"] else 0
                _mv_c   = "#4ade80" if _avg_mv > 0 else "#f87171"
                _col.markdown(
                    f'<div class="metric-tile" style="text-align:center">'
                    f'<div class="metric-label">{_dow_names[_di][:3]}</div>'
                    f'<div style="font-size:20px;font-weight:800;color:{_da_c}">{_da_pct}%</div>'
                    f'<div style="font-size:10px;color:#64748b">{_da["hits"]}/{_da["total"]} slots</div>'
                    f'<div style="font-size:11px;color:{_mv_c};margin-top:2px">avg move: {_avg_mv:+.1f}</div>'
                    f'</div>',
                    unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# SHADOW PERFORMANCE LEDGER
# Appends one row per trading day at or after 4 PM (post-close snapshot).
# Records frozen daily outputs so live performance can be measured forward.
# Works locally; on Streamlit Cloud writes to /tmp (ephemeral across restarts).
# ─────────────────────────────────────────────────────────────────────────────
_LEDGER_DIR  = "Codex"
_LEDGER_FILE = os.path.join(_LEDGER_DIR, "shadow-ledger.csv")
_LEDGER_COLS = ["date","core_ssr","live_adj_ssr","vix","gap_pts","event_flags",
                "opex","orb_status","actual_dir","actual_pts"]

def _ledger_read():
    """Load the shadow ledger CSV; return list of dicts."""
    try:
        if not os.path.exists(_LEDGER_FILE):
            return []
        with open(_LEDGER_FILE, newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []

def _ledger_append(row_dict):
    """Append one row to the shadow ledger, creating file + header if needed."""
    try:
        os.makedirs(_LEDGER_DIR, exist_ok=True)
        exists = os.path.exists(_LEDGER_FILE)
        with open(_LEDGER_FILE, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_LEDGER_COLS, extrasaction="ignore")
            if not exists or os.path.getsize(_LEDGER_FILE) == 0:
                w.writeheader()
            w.writerow(row_dict)
    except Exception:
        pass  # silently fail (e.g. read-only FS on Cloud)

def _ledger_fill_actuals(rows):
    """
    Retroactively fill 'actual_dir' / 'actual_pts' for rows where it's blank.
    actual_dir = direction of SPX close on that date vs prior close.
    Uses SPX 5d/1d data already available — cheap and accurate.
    """
    try:
        _spx_hist = yf.download("^GSPC", period="6mo", interval="1d",
                                progress=False, auto_adjust=True)
        if _spx_hist.empty: return rows
        _hc = _spx_hist["Close"].squeeze()
        if isinstance(_hc, pd.DataFrame): _hc = _hc.iloc[:, 0]
        _hist_map = {idx.date().isoformat(): float(v) for idx, v in zip(_spx_hist.index, _hc)}
        dates_sorted = sorted(_hist_map.keys())
        _prev_close_map = {dates_sorted[i]: _hist_map[dates_sorted[i-1]]
                           for i in range(1, len(dates_sorted))}
        updated = []
        for r in rows:
            if r.get("actual_dir", "").strip() == "" and r["date"] in _hist_map:
                _close = _hist_map[r["date"]]
                _prev  = _prev_close_map.get(r["date"])
                if _prev:
                    _pts = round(_close - _prev, 1)
                    r = dict(r)
                    r["actual_pts"] = str(_pts)
                    r["actual_dir"]  = "bull" if _pts > 5 else ("bear" if _pts < -5 else "flat")
            updated.append(r)
        return updated
    except Exception:
        return rows

# ── Auto-append today's row at post-close ───────────────────────────────────
_is_post_close = (now_est.weekday() < 5 and now_est.hour >= 16)
_today_str     = now_est.strftime("%Y-%m-%d")
if _is_post_close:
    _existing_rows = _ledger_read()
    _existing_dates = {r["date"] for r in _existing_rows}
    if _today_str not in _existing_dates:
        _today_events_str = ",".join(sorted(get_event_types_today()))
        _ledger_append({
            "date":         _today_str,
            "core_ssr":     str(_core_ssr),
            "live_adj_ssr": str(score),
            "vix":          str(vix_now),
            "gap_pts":      str(round(live_gap, 1)),
            "event_flags":  _today_events_str if _today_events_str else "none",
            "opex":         "yes" if _opex_week else "no",
            "orb_status":   _orb_status,
            "actual_dir":   "",
            "actual_pts":   "",
        })

# ── Shadow ledger display in research tab ───────────────────────────────────
with _tab_research:
    with st.expander("📓 Shadow Performance Ledger — Forward-Tracked Sessions (click to expand)", expanded=False):
        st.caption(
            "Each row = one post-close snapshot (Core SSR + Live-Adj SSR). "
            "'actual_dir' is filled retroactively from daily SPX closes. "
            "Requires local filesystem write — ephemeral on Streamlit Cloud unless volume mounted."
        )
        _ledger_rows = _ledger_read()
        if _ledger_rows:
            _ledger_rows = _ledger_fill_actuals(_ledger_rows)
            # Re-save with filled actuals
            try:
                os.makedirs(_LEDGER_DIR, exist_ok=True)
                with open(_LEDGER_FILE, "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=_LEDGER_COLS, extrasaction="ignore")
                    w.writeheader(); w.writerows(_ledger_rows)
            except Exception:
                pass
            _recent = _ledger_rows[-30:][::-1]   # last 30, newest first
            # Exclude flat days and neutral SSR from accuracy calculation.
            # Only measure directional calls (SSR ≥55 or ≤44) on directional outcomes (bull/bear).
            _hits_c = sum(1 for r in _recent
                          if r.get("actual_dir","").strip()
                          and r["actual_dir"] in ("bull","bear")
                          and ((r["actual_dir"] == "bull" and int(r.get("live_adj_ssr",50)) >= 55)
                               or (r["actual_dir"] == "bear" and int(r.get("live_adj_ssr",50)) <= 44)))
            _tot_known = sum(1 for r in _recent
                             if r.get("actual_dir","").strip()
                             and r["actual_dir"] in ("bull","bear")
                             and (int(r.get("live_adj_ssr",50)) >= 55
                                  or int(r.get("live_adj_ssr",50)) <= 44))
            _ldg_acc   = int(_hits_c / _tot_known * 100) if _tot_known else 0
            _ldg_c     = "#4ade80" if _ldg_acc >= 60 else ("#f59e0b" if _ldg_acc >= 45 else "#f87171")
            # Extra breakdown counts
            _flat_count    = sum(1 for r in _recent if r.get("actual_dir","") == "flat")
            _neutral_count = sum(1 for r in _recent
                                 if r.get("actual_dir","") in ("bull","bear")
                                 and not (int(r.get("live_adj_ssr",50)) >= 55
                                          or int(r.get("live_adj_ssr",50)) <= 44))
            if _tot_known:
                st.markdown(
                    f'<div style="font-size:13px;color:#94a3b8;margin-bottom:6px">'
                    f'Forward accuracy (directional calls only): '
                    f'<b style="color:{_ldg_c};font-size:16px">{_ldg_acc}%</b> '
                    f'<span style="font-size:11px">({_hits_c}/{_tot_known} sessions)</span>'
                    f'</div>'
                    f'<div style="font-size:11px;color:#475569;margin-bottom:10px">'
                    f'Flat days excluded: {_flat_count} · Neutral SSR excluded: {_neutral_count}</div>',
                    unsafe_allow_html=True)
            _ldg_rows_html = ""
            for _lr in _recent:
                _cs  = int(_lr.get("core_ssr","50") or 50)
                _ls  = int(_lr.get("live_adj_ssr","50") or 50)
                _ad  = _lr.get("actual_dir","")
                _ap  = _lr.get("actual_pts","")
                _cs_c = "#4ade80" if _cs >= 55 else "#ef4444" if _cs <= 44 else "#94a3b8"
                _ls_c = "#4ade80" if _ls >= 55 else "#ef4444" if _ls <= 44 else "#94a3b8"
                _ad_c = "#4ade80" if _ad == "bull" else "#ef4444" if _ad == "bear" else "#64748b"
                # Model call from live-adj SSR
                _model_call = "bull" if _ls >= 55 else ("bear" if _ls <= 44 else "neutral")
                _mc_c = "#4ade80" if _model_call == "bull" else "#ef4444" if _model_call == "bear" else "#64748b"
                # Result: ✅ correct / ❌ wrong / ⚪ flat or neutral call / — unknown
                if not _ad:
                    _result = "—"
                elif _ad == "flat" or _model_call == "neutral":
                    _result = "⚪"
                elif _ad == _model_call:
                    _result = "✅"
                else:
                    _result = "❌"
                _ldg_rows_html += (
                    f'<tr style="border-bottom:1px solid #1a1f33">'
                    f'<td style="padding:4px 8px;font-size:11px;color:#64748b">{_lr["date"]}</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:{_cs_c};font-weight:700">{_cs}</td>'
                    f'<td style="padding:4px 8px;font-size:12px;color:{_ls_c};font-weight:700">{_ls}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:{_mc_c}">{_model_call}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:#94a3b8">{_lr.get("vix","")}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:#94a3b8">{_lr.get("gap_pts","")}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:#64748b">{_lr.get("event_flags","")}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:{_ad_c}">{_ad or "—"}</td>'
                    f'<td style="padding:4px 8px;font-size:11px;color:{_ad_c}">{_ap or "—"}</td>'
                    f'<td style="padding:4px 8px;font-size:13px;text-align:center">{_result}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:12px 14px;overflow-x:auto">'
                f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9">'
                f'<thead><tr style="background:#0f1117">'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px;text-align:left">DATE</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">CORE</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">LIVE-ADJ</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">CALL</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">VIX</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">GAP</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">EVENTS</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">ACTUAL</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">SPX Δ</th>'
                f'<th style="padding:5px 8px;color:#64748b;font-size:10px">RESULT</th>'
                f'</tr></thead><tbody>{_ldg_rows_html}</tbody></table></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="padding:16px;color:#475569;font-size:12px;text-align:center">'
                'No ledger entries yet. The first row is appended automatically after market close (4 PM EST). '
                'Requires writable local filesystem.</div>',
                unsafe_allow_html=True)

st.markdown("""
<div style="text-align:center;color:#374151;font-size:11px;margin-top:10px;padding-bottom:6px">
  🔄 Auto-refreshes every 60s &nbsp;·&nbsp; ES &amp; SPX live prices update each refresh &nbsp;·&nbsp;
  SSR recomputes every 5 min &nbsp;·&nbsp; Options flow via 🦅 unusualwhales.com &nbsp;·&nbsp;
  For educational purposes only · Not financial advice
</div>
""", unsafe_allow_html=True)
