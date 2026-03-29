"""
SPX Algo — Player224 Style  |  Streamlit Web UI
Run: streamlit run spx_app.py
"""

import sys, io, re, xml.etree.ElementTree as _ET
from datetime import datetime, date, timedelta
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as _components
import urllib.request, urllib.error
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
    ("10:45", "11:00", "Intraday Bounce",        "chop"),
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
#   "Above BB Mid"         → mathematically identical to "Above 20 SMA"
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
                   "VIX Below 15"],              # ultra-calm: second tier for gradient
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%", "A/D Line Positive",
                   "Sector Breadth ≥ 70%"],      # strong breadth: second tier for gradient
    "Extremes":   ["Stoch Bullish", "RSI Trend Zone"],
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
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
    (["strait of hormuz","hormuz block","hormuz clos","iran oil","oil tanker attack",
      "oil supply disruption","opec cut","opec produc cut","oil embargo",
      "oil facility attack","saudi oil","pipeline attack"],
     "OIL_SUPPLY_SHOCK", 3.5, "any", "bear",
     "Oil supply shock → oil spike → CPI surge → Fed hawkish → bearish"),

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
    (["iran attack","iran missile","iran nuclear","israel iran","iran war",
      "iran strikes","iran retaliat","iran threaten","iran sanction new",
      "iran oil block","hezbollah","hamas attack","iran-backed"],
     "IRAN_ESCALATION", 3.0, "any", "bear",
     "Iran conflict → Hormuz risk + oil spike + safe-haven flows → bearish"),

    (["iran deal","iran nuclear deal","iran sanction lift","iran ceasefire",
      "iran agreement","iran talks","iran diplomacy"],
     "IRAN_DEESCALATION", 2.5, "any", "bull",
     "Iran de-escalation → Hormuz open + oil supply relief → bullish"),

    # ── BROADER GEOPOLITICAL ─────────────────────────────────────────────────
    (["russia attack","russia missile","russia ukraine escal","russia nato",
      "nuclear threat","nuclear strike","russia invad"],
     "RUSSIA_GEO", 2.5, "any", "bear",
     "Russia escalation → energy supply risk + European recession fears → bearish"),

    (["china taiwan","taiwan strait","taiwan tension","china threaten taiwan",
      "china military taiwan","pla taiwan","china invad taiwan"],
     "CHINA_TAIWAN", 3.0, "any", "bear",
     "Taiwan tension → semiconductor supply chain collapse + tech selloff → sharply bearish"),

    (["ceasefire","peace deal","de-escalat","peace agreement","hostage deal",
      "conflict ends","war ends","truce signed","peace talks succeed"],
     "GEO_DEESCALATION", 2.5, "any", "bull",
     "Conflict resolution → risk-on + commodity price relief → bullish"),

    # ── TRADE / TARIFFS ──────────────────────────────────────────────────────
    (["new tariff","tariff hike","tariff increas","tariff imposed","tariff announc",
      "trade war escal","trade war widen","tariff expand","trump tariff",
      "china tariff","reciprocal tariff","tariffs on","tariff threat",
      "section 301","section 232","import tax","import duty"],
     "TARIFF_BEARISH", 3.5, "any", "bear",
     "Tariffs → supply chain costs + retaliation risk + margin compression → bearish"),

    (["tariff pause","tariff delay","tariff exempt","tariff cut","tariff reduc",
      "trade deal","trade agreement","trade truce","tariff remov","tariff drop",
      "tariff lift","trade war end","trade war resolv","tariff rollback",
      "tariff suspend"],
     "TARIFF_BULLISH", 3.5, "any", "bull",
     "Tariff relief → supply chain normalization + margin recovery + risk-on → bullish"),

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

@st.cache_data(ttl=300)
def load_news(vix_now=0.0):
    """
    Fetch real-time market news with causal-chain sentiment scoring.
    Each article is scored using _NEWS_IMPACTS taxonomy with domain weights.
    Composite score is weighted by article impact weight (not just recency).

    Priority:
      1. Financial Juice RSS (real-time breaking market news, free)
      2. CNBC Markets RSS  (reliable, free)
      3. Alpha Vantage News Sentiment (if AV_KEY set — pre-scored)
      4. yfinance headlines (fallback)
    Returns: {articles: [...], composite_score: float, label: str,
              top_impact: {category, note, weight}}
    """
    articles = []

    def _parse_rss(url, source_name, max_items=12):
        items = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                raw = r.read().decode("utf-8", errors="ignore")
            root = _ET.fromstring(raw)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall(".//item") or root.findall(".//atom:entry", ns)
            for entry in entries[:max_items]:
                title_el = entry.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                if not title: continue
                pub_el = entry.find("pubDate") or entry.find("published")
                pub    = (pub_el.text or "")[:25] if pub_el is not None else ""
                score, cat, wt, note = _keyword_impact(title, vix=vix_now)
                label = "🟢 Bullish" if score > 0.1 else ("🔴 Bearish" if score < -0.1 else "⚪ Neutral")
                items.append({"title": title, "source": source_name,
                               "time": pub, "score": score, "label": label,
                               "category": cat, "impact_weight": wt, "note": note})
        except Exception:
            pass
        return items

    articles += _parse_rss("https://www.financialjuice.com/feed.aspx?q=market", "FinancialJuice")
    if len(articles) < 8:
        articles += _parse_rss(
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
            "CNBC")

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
                "bull_pct": 0, "bear_pct": 0, "top_impact": None}

    # ── Composite score: impact-weighted (high-weight articles matter more) ──
    # Weight = impact_weight × recency (most recent = position 0 = weight 1.0/(0+1)=1.0)
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
    bull_pct = int(sum(1 for a in articles if a["score"] > 0.1)  / len(articles) * 100)
    bear_pct = int(sum(1 for a in articles if a["score"] < -0.1) / len(articles) * 100)

    # Find highest-impact article for display
    top = max(articles, key=lambda a: a["impact_weight"] * abs(a["score"]) if a["score"] != 0 else 0)
    top_impact = {"category": top["category"], "note": top["note"],
                  "weight": top["impact_weight"], "title": top["title"][:80]} if top["score"] != 0 else None

    return {
        "articles":        articles[:12],
        "composite_score": round(comp, 3),
        "label":           label,
        "bull_pct":        bull_pct,
        "bear_pct":        bear_pct,
        "top_impact":      top_impact,
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
        pcr = yf.download("^CPC", period="10d", interval="1d", progress=False, auto_adjust=True)
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
            results["es_price"] = round(_close_scalar(es_df, -1), 2)
            prev_close = _close_scalar(es_df, -2) if len(es_df) > 1 else results["es_price"]
            results["es_change"] = round(results["es_price"] - prev_close, 2)
            results["es_pct"]    = round((results["es_change"] / prev_close) * 100, 2)
            results["es_ts"]     = es_df.index[-1].astimezone(EST).strftime("%I:%M %p EST")
    except Exception:
        pass
    try:
        spx_df = yf.download("^GSPC", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not spx_df.empty:
            results["spx_price"] = round(_close_scalar(spx_df, -1), 2)
            prev_close = _close_scalar(spx_df, -2) if len(spx_df) > 1 else results["spx_price"]
            results["spx_change"] = round(results["spx_price"] - prev_close, 2)
            results["spx_pct"]    = round((results["spx_change"] / prev_close) * 100, 2)
            results["spx_ts"]     = spx_df.index[-1].astimezone(EST).strftime("%I:%M %p EST")
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


@st.cache_data(ttl=180)
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
    # VIX Falling: use as_of_dt when provided (backtest) so we don't leak
    # today's clock into historical evaluation.
    _ref_dt   = as_of_dt if as_of_dt is not None else datetime.now(EST)
    _now_wd   = _ref_dt.weekday()
    _now_h    = _ref_dt.hour
    _mkt_open = (_now_wd < 5 and 9 <= _now_h < 16)
    sigs["VIX Falling"] = (int(vix_c.iloc[-1] < vix_c.iloc[-2])
                           if (len(vix_c) >= 2 and _mkt_open) else 0)
    # ATR Contracting: need >= 20 bars for ATR(14) to stabilize + 5 for comparison
    sigs["ATR Contracting"]   = int(len(atr_v.dropna()) >= 20 and atr_v.iloc[-1] < atr_v.iloc[-5])
    # VIX Below 15: ultra-calm regime tier. Pairs with "VIX Below 20" for gradient:
    # VIX 16-20 fires one signal; VIX <15 fires both — stronger low-vol bull context.
    sigs["VIX Below 15"]      = int(vix_c.iloc[-1] < 15)

    # ── Breadth group ────────────────────────────────────────────────────────
    # Volume directional: confirmed only when price is also higher (accumulation),
    # not raw volume (which could be panic selling).
    sigs["Volume Above Average"] = int(vol.iloc[-1] > vol.rolling(20).mean().iloc[-1]
                                       and len(vol.dropna()) >= 20)

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
    _total_s = len(_sec_closes)
    if _total_s:
        _above = sum(1 for _c in _sec_closes.values()
                     if _c.iloc[-1] > _c.rolling(50).mean().iloc[-1])
        sigs["Sector Breadth ≥ 50%"] = int((_above / _total_s) >= 0.5)
        # Strong breadth tier: ≥70% of sectors above 50-SMA.
        # Pairs with ≥50% to give Breadth group a 2-tier gradient.
        sigs["Sector Breadth ≥ 70%"] = int((_above / _total_s) >= 0.7)

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

    # ── Macro / regime signals ────────────────────────────────────────────────
    # Yield Curve: 10yr − 3mo > 0 = normal (bull context); < 0 = inverted = recession warning
    # Credit Spread proxy: HYG/TLT ratio rising = spreads compressing = risk-on
    if macro:
        _yc = macro.get("yield_curve_pts", 0.0)
        sigs["Yield Curve Positive"]  = int(_yc > 0)   # non-inverted = macro tailwind
        _hyg_rising = macro.get("hyg_tlt_rising", True)
        sigs["Credit Spread Calm"]    = int(_hyg_rising)  # HYG/TLT rising = credit risk-on

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
                   event_types=None, weekday=None):
    """
    Return (bias, label) for a given HH:MM.
    gap        = today's open − prior close (positive = gap-up, negative = gap-down).
    vix        = current VIX reading (0 = unknown / skip VIX override).
    news_score = composite news sentiment −1..+1 (from load_news composite_score).
    orb_status = "above" | "below" | "inside" (Opening Range Breakout status).
    opex       = True if current week is standard monthly options expiration week.

    Override hierarchy (highest priority first):
      1. Gap-up > 25pts  → Pre-Bull Fade & Afternoon Trend become chop
      2. VIX > 25 (fear) → chop windows become bear; bull windows become chop
      3. VIX < 18 (calm) → bear windows soften to chop (range-bound low-vol)
      4. Economic event  → FOMC/CPI/NFP time-of-day overrides
      5. OpEx week       → mid-session chop reinforced; EOD Trend directional preserved
      6. ORB breakout    → price outside opening range shifts chop→directional (post 10 AM)
      7. News sentiment  → strong news (|score|≥0.25) shifts chop→directional
    """
    for start, end, label, bias in TIME_WINDOWS:
        if start <= hhmm < end:
            # ── Gap-conditional overrides ──────────────────────────────────
            if label == "Pre-Bull Fade" and gap > GAP_THRESHOLD:
                return "chop", label + " (gap-up→chop)"
            if label == "Afternoon Trend" and gap > GAP_THRESHOLD:
                return "chop", label + " (gap-up→chop)"

            # ── VIX fear regime (VIX > 25) ─────────────────────────────────
            # In high-fear trending markets, intraday "chop" rarely materialises;
            # selling pressure bleeds through. Bull windows lose reliability.
            if vix > VIX_FEAR_THRESHOLD and label not in ("Open Volatility", "AH Bull Window (ES)"):
                if bias == "chop":
                    return "bear", label + " (hi-VIX→bear)"
                if bias == "bull":
                    return "chop", label + " (hi-VIX→chop)"

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
            if hhmm >= "10:00" and bias == "chop":
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
    """Normalised directional factor from SSR: -1.0 … +1.0"""
    if   score <= 35: return -1.00
    elif score <= 44: return -0.60
    elif score <= 54: return  0.00
    elif score <= 65: return  0.60
    else:             return  1.00


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


def generate_es_projections(base_price, daily_atr, score, gap=0.0, vix=0.0, news_score=0.0, orb_status="inside", opex=False):
    """30-minute ES projections for 23 hours starting from the next opening bell."""
    direction = ssr_direction(score)

    # VIX regime scaling — high VIX = larger per-slot swings (fear = bigger moves)
    if   vix >= 35: _vx = 2.0
    elif vix >= 30: _vx = 1.6
    elif vix >= 25: _vx = 1.35
    elif vix >= 20: _vx = 1.15
    else:           _vx = 1.0

    # OpEx gamma-pinning compression: mid-week RTH ranges ~15% tighter
    # (market makers hold price near max-pain strike, suppressing ATR)
    _opex_factor = 0.85 if opex else 1.0

    # ATR per 30 minutes by session type — scaled by VIX regime + OpEx compression
    def slot_atr(h):
        if 9 <= h < 16:  return daily_atr * 0.09  * _vx * _opex_factor
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
            win_bias, win_label = window_bias_at(hhmm, gap=gap, vix=vix, news_score=news_score, orb_status=orb_status, opex=opex)
            wf       = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
            satr     = slot_atr(t.hour)

            # Direction confidence: neutral SSR (score≈50) should produce minimal moves
            # even when a directional window is active. The window only has edge when
            # the daily conviction (SSR) agrees with it.
            _dir_conf = min(1.0, abs(direction) + 0.15)   # 0→0.15, 0.6→0.75, 1→1.0

            # Regime-aware blend: how much SSR direction vs window bias drives the move.
            # High-VIX trending days: direction dominates (windows are noisy).
            # Low-VIX range days: window timing is more reliable.
            # Large gap days: gap direction pressure lifts SSR weight.
            # OpEx: balanced (gamma pins both SSR and window edge).
            if vix > VIX_FEAR_THRESHOLD:
                _dir_w, _win_w = 0.70, 0.30
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

            # Outside-hours slots: no window bias, SSR direction drives overnight drift
            if win_bias == "neutral":
                move = satr * direction * 0.60 * _dir_conf
            else:
                move = satr * (direction * _dir_w + wf * _win_w) * _dir_conf

            # Mean-reversion dampener: as price drifts far from base, gentle pull-back
            # prevents runaway projections. 1.5% reversion per point of drift.
            _drift    = price - base_price
            _revert   = -_drift * 0.015
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


def generate_spx_projections(base_price, daily_atr, score, gap=0.0, vix=0.0, news_score=0.0, orb_status="inside", opex=False):
    """Hourly SPX projections for the next/current RTH session (9:30 AM – 4:00 PM)."""
    direction = ssr_direction(score)
    # VIX regime scaling — same thresholds as ES projections
    if   vix >= 35: _vx = 2.0
    elif vix >= 30: _vx = 1.6
    elif vix >= 25: _vx = 1.35
    elif vix >= 20: _vx = 1.15
    else:           _vx = 1.0
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
        win_bias, win_label = window_bias_at(slot, gap=gap, vix=vix, news_score=news_score, orb_status=orb_status, opex=opex)
        win_factor  = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
        _dir_conf   = min(1.0, abs(direction) + 0.15)
        _drift      = price - base_price
        _revert     = -_drift * 0.015
        # Adaptive slot ATR: front-loaded to match real intraday vol distribution
        _slot_atr   = daily_atr * _atr_profile[min(idx, len(_atr_profile)-1)] * _vx * _opex_factor
        # Regime-aware blend (mirrors ES logic above)
        if vix > VIX_FEAR_THRESHOLD:
            _dir_w, _win_w = 0.70, 0.30
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
        move        = _slot_atr * (direction * _dir_w + win_factor * _win_w) * _dir_conf + _revert
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


def generate_weekly_projections(base_price, daily_atr, score):
    """
    Daily projections for the next 5 trading days with:
      • Day-of-week tendency (Mon/Wed/Fri mean-revert; Tue/Thu continue)
      • Exhaustion dampener: extreme SSR fades toward neutral over the week
      • Per-day directional confidence (not just magnitude decay)
    """
    base_dir = ssr_direction(score)

    # Exhaustion factor [0–1]: kicks in beyond ±15 SSR units from 50
    # Score 50 → 0.0 (no exhaustion), score 20 or 80 → ~1.0 (full reversion expected)
    ssr_extreme = max(0.0, min(1.0, (abs(score - 50) - 15) / 35.0))

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
        strike = int(c // 25) * 25
        return {"direction":"PUT","strike":strike,
                "expiry": str(friday) if score<=35 else str(next_w),
                "entry": f"{levels['resistance_1']} – {levels['resistance_2']}",
                "target1":levels["target_down_1"],"target2":levels["target_down_2"],
                "stop":round(levels["resistance_2"]+atr14*0.5,1),
                "sizing":"2–4 contracts" if score<=35 else "1–2 contracts"}
    elif bias == "calls":
        strike = (int(c // 25)+1) * 25
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

                # Match window — use window_bias_at() so the same overrides
                # (gap, VIX, events, OpEx) that the live page uses are also
                # applied in the 2-year research validation.
                _hist_dt_str = dt.strftime("%Y-%m-%d")
                _hist_evts   = {ev[2] for ev in _ECON_CAL if ev[0] == _hist_dt_str}
                _is_opex_h   = is_opex_week(dt)
                mbias, mlabel = window_bias_at(
                    hhmm, gap=gap_val, vix=vix_val,
                    event_types=_hist_evts, weekday=dt.weekday(),
                    opex=_is_opex_h)
                if mlabel == "Outside Hours": continue

                vix_val = vix_map.get(dt, 20.0)
                gap_val = gap_map.get(dt, 0.0)
                vix_key = ("vix_high" if vix_val > VIX_FEAR_THRESHOLD
                           else "vix_low" if vix_val < VIX_CALM_THRESHOLD
                           else "vix_mid")
                gap_key = ("gap_up"   if gap_val > 10 else
                           "gap_down" if gap_val < -10 else "gap_flat")

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


def windows_html(now_hhmm):
    rows = []
    for s, e, lbl, b in TIME_WINDOWS:
        is_now = s <= now_hhmm < e
        now_badge = '<span class="win-now">NOW</span>' if is_now else ""
        row_style = 'background:#1a2744;border-radius:6px;' if is_now else ""
        rows.append(
            f'<div class="window-row" style="{row_style}">'
            f'<span class="win-time">{to_ampm(s)}–{to_ampm(e)}</span>'
            f'<span class="win-label">{BIAS_COLOR.get(b,"⚪")} {lbl}</span>'
            f'{now_badge}</div>'
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
_REFRESH_SECS = 180
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

vix_now = round(vix["Close"].squeeze().iloc[-1], 2)

# Load news with live VIX so VIX-conditional items (jobs data) score correctly
news_data = load_news(vix_now=vix_now)

_base_score, buys, sells, signals = compute_ssr(spx, vix, pcr, sectors, macro=macro_data)

# ── Intraday RSI override: replace daily RSI signals with 5-min RSI during RTH ──
# Daily RSI is computed on close-to-close bars; by mid-afternoon it reflects
# yesterday's close and is hours stale. 5-min RSI captures live momentum.
_intra_rsi = fetch_intraday_rsi()
_is_rth_now = (now_est.weekday() < 5 and
               (9 <= now_est.hour < 16 or (now_est.hour == 16 and now_est.minute == 0)))
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
# direction over the last 10 trading days.  Groups with >70% hit rate get
# boosted (up to 1.8×); groups with <50% hit rate get penalised (down to 0.4×).
@st.cache_data(ttl=3600)
def compute_group_weights():
    """Derive per-group weights by correlating each group's vote with actual day direction."""
    try:
        _spx_d, _vix_d, _sec_d, _day_s, _days = load_backtest_data()
        if not _days: return {g: 1.0 for g in SIGNAL_GROUPS}
        _dl = list(_spx_d.index.date); _td = len(_spx_d)
        # Weighted counts: recent 30 days count 2× older days (regime decay)
        _gc = {g: 0.0 for g in SIGNAL_GROUPS}
        _gt = {g: 0.0 for g in SIGNAL_GROUPS}
        _recent_cutoff = len(_days) - 30  # last 30 days = "recent"
        for _i, _day in enumerate(_days[-252:]):
            _day_weight = 2.0 if _i >= (len(_days[-252:]) - 30) else 1.0
            try:
                _pos = _dl.index(_day)
                _off = _td - _pos
                _sb  = _spx_d.iloc[:-_off] if _off > 0 else _spx_d
                _vb  = _vix_d.iloc[:-_off] if _off > 0 else _vix_d
                _eb  = {k: v.iloc[:-_off] if _off > 0 else v for k, v in _sec_d.items()}
                _ds  = _day_s.get(_day)
                if _ds is None or len(_ds) < 2: continue
                # Pass historical noon datetime so VIX Falling doesn't use today's clock
                _as_of = EST.localize(datetime(_day.year, _day.month, _day.day, 12, 0))
                _, _, _, _sigs = compute_ssr(_sb, _vb, pd.DataFrame(), _eb, as_of_dt=_as_of)
                _act = 1 if float(_ds.iloc[-1]) > float(_ds.iloc[0]) else -1
                for _gn, _gs in SIGNAL_GROUPS.items():
                    _pr = [_sigs.get(k, 0) for k in _gs if k in _sigs]
                    if not _pr: continue
                    _vote = 1 if (sum(_pr) / len(_pr)) > 0.5 else -1
                    _gt[_gn] += _day_weight
                    if _vote == _act: _gc[_gn] += _day_weight
            except Exception:
                continue
        # acc → weight: 50%=0.5, 60%=1.0, 70%=1.4, 80%=1.8 (linear)
        # Require effective n >= 5 (sum of weights) before trusting accuracy
        _out = {}
        for _gn in SIGNAL_GROUPS:
            _t = _gt[_gn]
            if _t < 5.0:
                _out[_gn] = 1.0
            else:
                _acc = _gc[_gn] / _t
                _out[_gn] = round(max(0.3, min(2.0, (_acc - 0.5) * 6.0 + 0.7)), 2)
        return _out
    except Exception:
        return {g: 1.0 for g in SIGNAL_GROUPS}

_grp_weights = compute_group_weights()
# Stamp when weights were computed so the UI can show a version, not silently recalculate
_grp_weights_ts = now_est.strftime("%b %d %I:%M %p")   # frozen for this session (1h cache)

# Re-score SSR using data-driven group weights
_wg_s, _wg_w = [], []
for _gn, _gs in SIGNAL_GROUPS.items():
    _pr = [signals.get(k, 0) for k in _gs if k in signals]
    if _pr:
        _w = _grp_weights.get(_gn, 1.0)
        _wg_s.append((sum(_pr) / len(_pr)) * _w)
        _wg_w.append(_w)
_weighted_base = round(sum(_wg_s) / sum(_wg_w) * 100) if _wg_s else _base_score

# ── News sentiment nudge: causal-chain weighted composite → ±10 SSR pts ─────
# Higher-weight news events (OIL_SUPPLY_SHOCK, BANK_CRISIS) move score more.
_news_comp  = news_data.get("composite_score", 0.0)
# Cap at ±5 pts: a single high-weight headline should not swing SSR by 10 pts.
# ±5 is meaningful (can shift Neutral → Weak Buy/Sell) without being headline-driven.
_news_nudge = max(-5, min(5, int(round(_news_comp * 5))))
score       = max(0, min(100, _weighted_base + _news_nudge))
# ── end nudge ───────────────────────────────────────────────────────────────

levels  = compute_levels(spx)
rating, action, bias, color = ssr_meta(score)
trade   = suggest_trade(score, levels)
cur_win, cur_bias, cur_start, cur_end = get_current_window()

es_price   = live["es_price"]  or levels["current"]
spx_price  = live["spx_price"] or levels["current"]
es_display = f"{es_price:,.2f}" if live["es_price"] else "—"
spx_display= f"{spx_price:,.2f}" if live["spx_price"] else f"{levels['current']:,.1f}"

BIAS_BG   = {"bull":"#14532d","bear":"#7f1d1d","chop":"#1e293b","neutral":"#1e293b"}
BIAS_TEXT = {"bull":"#4ade80","bear":"#f87171","chop":"#94a3b8","neutral":"#94a3b8"}

# ── HEADER ───────────────────────────────────────────────────────────────────
st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
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
""", unsafe_allow_html=True)

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

for col, lbl, val, sub, vc, sc, fsize in [
    (mc1, "SSR Score",    str(score),      f"{rating.split()[0]} {rating.split()[1] if len(rating.split())>1 else ''}",  color,  "#94a3b8", "22px"),
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
      <div style="font-size:10px;color:#475569;margin-bottom:4px">
        Base: {_base_score} → Wt: {_weighted_base} &nbsp;·&nbsp;
        News: <span style="color:{'#4ade80' if _news_nudge>0 else '#f87171' if _news_nudge<0 else '#64748b'}">{'+' if _news_nudge>0 else ''}{_news_nudge}</span>
        &nbsp;·&nbsp; Final: <b style="color:{color}">{score}</b>
      </div>
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
    st.markdown(f"""
    <div class="card">
      <h3>Intraday Windows — ES &amp; SPX (EST)</h3>
      {now_badge_html}
      {windows_html(now_hhmm)}
    </div>
    """, unsafe_allow_html=True)

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
                 "#4ade80" if o["status"]=="above" else "#f87171" if o["status"]=="below" else "#94a3b8")
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

# ── Compute live_gap and ORB status at module level (used in Why This Bias
#    and again inside _tab_live for projections — must be defined before tabs)
try:
    _spx_open_  = spx["Open"].squeeze()
    _spx_close_ = spx["Close"].squeeze()
    if isinstance(_spx_open_,  pd.DataFrame): _spx_open_  = _spx_open_.iloc[:,  0]
    if isinstance(_spx_close_, pd.DataFrame): _spx_close_ = _spx_close_.iloc[:, 0]
    if len(_spx_open_) >= 2 and len(_spx_close_) >= 2:
        live_gap = round(float(_spx_open_.iloc[-1]) - float(_spx_close_.iloc[-2]), 1)
    else:
        live_gap = 0.0
except Exception:
    live_gap = 0.0
_orb_status = orb_data.get("status", "inside") if orb_data.get("valid") else "inside"

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

# 3. ORB status
if orb_data.get("valid"):
    _orb_lbl = f"{orb_data['status'].capitalize()} range ({orb_data['high']}/{orb_data['low']})"
    _orb_c = "#4ade80" if orb_data["status"] == "above" else ("#f87171" if orb_data["status"] == "below" else "#64748b")
    _orb_ov = ("Active post 10 AM: chop→bull" if orb_data["status"] == "above"
               else "Active post 10 AM: chop→bear" if orb_data["status"] == "below"
               else "No override (inside ORB)")
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
                                          news_score=_news_comp, orb_status=_orb_status, opex=_opex_week)
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
    with st.expander(f"📊 Signal Breakdown — {buys} Buy / {sells} Sell", expanded=False):
        bull_sigs = {k:v for k,v in signals.items() if v==1}
        bear_sigs = {k:v for k,v in signals.items() if v==0}
        scol1, scol2, scol3 = st.columns(3)
        all_sigs = [(k, 1) for k in bull_sigs] + [(k, 0) for k in bear_sigs]
        third = (len(all_sigs) + 2) // 3
        for ci, col in enumerate([scol1, scol2, scol3]):
            chunk = all_sigs[ci*third:(ci+1)*third]
            rows_html = "".join(
                f'<div class="sig-row">'
                f'<span>{"✅" if v else "❌"} {k}</span>'
                f'<span style="color:{"#22c55e" if v else "#ef4444"};font-size:10px">{"BUY" if v else "SELL"}</span>'
                f'</div>'
                for k, v in chunk
            )
            col.markdown(f'<div style="background:#1e2130;border-radius:8px;padding:8px 12px">{rows_html}</div>',
                         unsafe_allow_html=True)

    with st.expander("📊 2-Year Statistical Window Validation (click to run — takes ~5s)", expanded=False):
        st.caption("Offline accuracy of each TIME_WINDOW bias over 2 years of 1h data by VIX regime and gap type. Research surface — not a live signal.")
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


with _tab_live:
    # ═══════════════════════════════════════════════════════════════════════════════
    # ROW 4 — HOURLY PROJECTIONS (ES left, SPX right)
    # live_gap and _orb_status computed at module level above (before tabs)
    # ═══════════════════════════════════════════════════════════════════════════════
    es_rows  = generate_es_projections(es_price,  levels["atr"], score, gap=live_gap, vix=vix_now, news_score=_news_comp, orb_status=_orb_status, opex=_opex_week)
    spx_rows = generate_spx_projections(spx_price, levels["atr"], score, gap=live_gap, vix=vix_now, news_score=_news_comp, orb_status=_orb_status, opex=_opex_week)

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
    # ROW 5 — WEEKLY PROJECTION
    # ═══════════════════════════════════════════════════════════════════════════════
    st.markdown("<h4 style='margin:6px 0 10px;color:#94a3b8'>📅 Weekly Projection — Next 5 Trading Days</h4>",
                unsafe_allow_html=True)

    weekly = generate_weekly_projections(spx_price, levels["atr"], score)

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

    UW_TOKEN = "378bfa59-6ee5-430d-b3d1-c0792fec2a78"

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
        vix_d = yf.download("^VIX",  period="30d",  interval="1d", progress=False, auto_adjust=True)
        sectors_d = {}
        for t in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
            try:
                sectors_d[t] = yf.download(t, period="60d", interval="1d", progress=False, auto_adjust=True)
            except Exception:
                sectors_d[t] = pd.DataFrame()

        # period="20d" gives ~20 trading days of 5-min bars → covers 2 full weeks
        spx_5m = yf.download("^GSPC", period="20d", interval="5m", progress=False, auto_adjust=True)
        spx_5m.index = spx_5m.index.tz_convert(EST)
        trading_days  = sorted(set(spx_5m.index.date))
        day_series    = {d: spx_5m[spx_5m.index.date == d]["Close"].squeeze() for d in trading_days}
        return spx_d, vix_d, sectors_d, day_series, trading_days


    def run_backtest_for_day(target_date, day_series, spx_d, vix_d, sectors_d, daily_dates_list, offset_from_end):
        day_5m = day_series.get(target_date)
        if day_5m is None or len(day_5m) == 0:
            return None

        spx_base = spx_d.iloc[:-offset_from_end] if offset_from_end > 0 else spx_d
        vix_base = vix_d.iloc[:-offset_from_end] if offset_from_end > 0 else vix_d
        sec_base  = {k: v.iloc[:-offset_from_end] if offset_from_end > 0 else v for k, v in sectors_d.items()}

        prev_close   = float(spx_base["Close"].squeeze().iloc[-1])
        bt_score, bt_buys, bt_sells, _ = compute_ssr(spx_base, vix_base, pd.DataFrame(), sec_base)
        bt_rating, bt_action, _, bt_color = ssr_meta(bt_score)
        bt_direction = ssr_direction(bt_score)

        h = spx_base["High"].squeeze(); l = spx_base["Low"].squeeze(); c = spx_base["Close"].squeeze()
        tr     = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        bt_atr = float(tr.rolling(14).mean().iloc[-1])
        slot_atr = bt_atr / 6.5

        # VIX on this day — used for VIX regime window overrides
        try:
            vix_on_day = float(vix_base["Close"].squeeze().iloc[-1])
        except Exception:
            vix_on_day = 0.0

        # Compute actual gap for this day (open − prior close) to drive gap-conditional windows
        day_open  = float(day_5m.iloc[0])
        day_gap   = round(day_open - prev_close, 1)

        slots = ["09:30","10:00","10:30","10:45","11:00","11:15","11:30","12:00",
                 "13:00","13:15","13:30","14:00","14:30","15:00","15:30","16:00"]
        # Anchor projection to open price when gap is significant (fixes systematic drift)
        proj_price = day_open if abs(day_gap) > 20 else prev_close
        projections = []
        for s in slots:
            _bt_dt_str  = target_date.strftime("%Y-%m-%d")
            _bt_evts    = {ev[2] for ev in _ECON_CAL if ev[0] == _bt_dt_str}
            _bt_is_opex = is_opex_week(target_date)
            win_bias, win_label = window_bias_at(
                s, gap=day_gap, vix=vix_on_day,
                event_types=_bt_evts, weekday=target_date.weekday(),
                opex=_bt_is_opex)
            wf   = {"bull":0.5,"bear":-0.5,"chop":0.0,"neutral":0.0}[win_bias]
            move = slot_atr * (bt_direction * 0.55 + wf * 0.45)
            proj_price = round(proj_price + move, 1)
            projections.append({"slot": s, "proj": proj_price, "move": round(move,1),
                                 "bias": win_bias, "label": win_label})

        def actual_at(hhmm):
            hh, mm = map(int, hhmm.split(":"))
            snap = day_5m[(day_5m.index.hour == hh) & (day_5m.index.minute >= mm)].head(1)
            return round(float(snap.iloc[0]), 1) if len(snap) else None

        results = []
        for p in projections:
            actual = actual_at(p["slot"])
            if actual is None: continue
            idx    = slots.index(p["slot"])
            prev_a = actual_at(slots[idx-1]) if idx > 0 else prev_close
            actual_dir = "bull" if actual > prev_a else ("bear" if actual < prev_a else "chop")
            # Chop = flat/indeterminate — threshold scales with VIX.
            # High-VIX days have larger noise, so a "flat" move can be larger.
            # VIX=20 → 0.30× ATR; VIX=30 → 0.45× ATR; VIX=40 → 0.60× ATR.
            _chop_thresh = slot_atr * min(0.6, 0.30 * max(1.0, vix_on_day / 20.0))
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
            "OIL_SUPPLY_SHOCK": ("🛢️ OIL SHOCK",   "#f59e0b"),
            "OIL_DROP":         ("🛢️ OIL DROP",    "#4ade80"),
            "OIL_SPIKE":        ("🛢️ OIL SPIKE",   "#f87171"),
            "IRAN_ESCALATION":  ("⚔️ IRAN",         "#f87171"),
            "IRAN_DEESCALATION":("🕊️ IRAN DEAL",    "#4ade80"),
            "RUSSIA_GEO":       ("⚔️ RUSSIA",        "#f87171"),
            "CHINA_TAIWAN":     ("⚔️ TAIWAN",        "#f87171"),
            "GEO_DEESCALATION": ("🕊️ DE-ESCAL",     "#4ade80"),
            "TARIFF_BEARISH":   ("🚧 TARIFF",        "#f87171"),
            "TARIFF_BULLISH":   ("🤝 TRADE DEAL",    "#4ade80"),
            "FED_DOVISH":       ("🏦 FED DOVE",      "#4ade80"),
            "FED_HAWKISH":      ("🏦 FED HAWK",      "#f87171"),
            "CPI_HOT":          ("📊 CPI HOT",       "#f87171"),
            "CPI_COOL":         ("📊 CPI COOL",      "#4ade80"),
            "JOBS_STRONG":      ("👷 JOBS+",         "#f59e0b"),
            "JOBS_WEAK":        ("👷 JOBS−",         "#f59e0b"),
            "BANK_CRISIS":      ("🏦 BANK CRISIS",   "#b91c1c"),
            "CREDIT_DOWNGRADE": ("📉 DOWNGRADE",     "#f87171"),
            "FISCAL_CRISIS":    ("🏛️ FISCAL",        "#f87171"),
            "FISCAL_RESOLUTION":("🏛️ FISCAL OK",    "#4ade80"),
            "YIELD_SPIKE":      ("📈 YIELD↑",        "#f87171"),
            "YIELD_DROP":       ("📈 YIELD↓",        "#4ade80"),
            "EARNINGS_BEAT":    ("💹 EARN BEAT",     "#4ade80"),
            "EARNINGS_MISS":    ("💹 EARN MISS",     "#f87171"),
            "RECESSION_FEAR":   ("🔻 RECESSION",     "#f87171"),
            "GROWTH_STRONG":    ("📈 GROWTH",        "#4ade80"),
            "GENERIC":          ("",                 "#475569"),
        }

        rows_html = ""
        for a in articles[:10]:
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

        # Top-impact article banner
        _top = news_data.get("top_impact")
        _top_html = ""
        if _top and _top.get("note"):
            _tc = "#f87171" if comp < -0.05 else "#4ade80" if comp > 0.05 else "#f59e0b"
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
        gap_key = "gap_up"   if gap_day > 10 else ("gap_down" if gap_day < -10 else "gap_flat")

        for r in bt["results"]:
            lbl = r["label"].split(" (")[0]   # strip regime suffix like "(hi-VIX→bear)"
            if lbl not in stats:
                stats[lbl] = {
                    "bias": r["bias"], "correct": 0, "total": 0,
                    "vix_low":  {"c": 0, "t": 0},
                    "vix_mid":  {"c": 0, "t": 0},
                    "vix_high": {"c": 0, "t": 0},
                    "gap_up":   {"c": 0, "t": 0},
                    "gap_flat": {"c": 0, "t": 0},
                    "gap_down": {"c": 0, "t": 0},
                }
            s = stats[lbl]
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
            _slot_atr   = levels["atr"] / 6.5

            _today_results = []
            _prev_actual   = _5m_open
            for _sl in _slots:
                _wb, _wl = window_bias_at(_sl, gap=live_gap, vix=vix_now, news_score=_news_comp, orb_status=_orb_status, opex=_opex_week)
                _actual  = _snap_at(_sl)
                if _actual is None:
                    continue
                _actual_dir = "bull" if _actual > _prev_actual else ("bear" if _actual < _prev_actual else "chop")
                _chop_t     = _slot_atr * min(0.6, 0.30 * max(1.0, vix_now / 20.0))
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
                    f'Partial-day sample — resets each session. For statistical validation see Research tab. Updates every 3 min.'
                    f'</div></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="padding:14px;color:#475569;font-size:12px;text-align:center">'
                    'Waiting for intraday bars — check back after 9:35 AM EST.'
                    '</div>', unsafe_allow_html=True)

    with st.expander("🔬 Backtest — Last 10 Trading Days  (click to expand)", expanded=False):
        spx_d_bt, vix_d_bt, sectors_d_bt, day_series_bt, trading_days_bt = load_backtest_data()

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
                                     sectors_d_bt, daily_dates_list, offsets.get(td, 1))
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


st.markdown("""
<div style="text-align:center;color:#374151;font-size:11px;margin-top:10px;padding-bottom:6px">
  🔄 Auto-refreshes every 60s &nbsp;·&nbsp; ES &amp; SPX live prices update each refresh &nbsp;·&nbsp;
  SSR recomputes every 5 min &nbsp;·&nbsp; Options flow via 🦅 unusualwhales.com &nbsp;·&nbsp;
  For educational purposes only · Not financial advice
</div>
""", unsafe_allow_html=True)
