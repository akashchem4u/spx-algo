"""
SPX Algo — Player224 Style  |  Streamlit Web UI
Run: streamlit run spx_app.py
"""

import sys, io
from datetime import datetime, date, timedelta
import pytz
import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st
import streamlit.components.v1 as _components

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

BIAS_COLOR = {"bull": "🟢", "bear": "🔴", "chop": "⚪", "neutral": "⚪"}


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
        sectors[t] = yf.download(t, period="60d", interval="1d", progress=False, auto_adjust=True)
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
    try:
        # Use 1-minute bar — most reliable source for actual last trade
        es_df = yf.download("ES=F", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not es_df.empty:
            results["es_price"] = round(float(es_df["Close"].iloc[-1]), 2)
            prev_close = float(es_df["Close"].iloc[-2]) if len(es_df) > 1 else results["es_price"]
            results["es_change"] = round(results["es_price"] - prev_close, 2)
            results["es_pct"]    = round((results["es_change"] / prev_close) * 100, 2)
            ts = es_df.index[-1]
            results["es_ts"] = ts.astimezone(EST).strftime("%I:%M %p EST")
    except Exception:
        pass
    try:
        spx_df = yf.download("^GSPC", period="2d", interval="1m", progress=False, auto_adjust=True)
        if not spx_df.empty:
            results["spx_price"] = round(float(spx_df["Close"].iloc[-1]), 2)
            prev_close = float(spx_df["Close"].iloc[-2]) if len(spx_df) > 1 else results["spx_price"]
            results["spx_change"] = round(results["spx_price"] - prev_close, 2)
            results["spx_pct"]    = round((results["spx_change"] / prev_close) * 100, 2)
            ts = spx_df.index[-1]
            results["spx_ts"] = ts.astimezone(EST).strftime("%I:%M %p EST")
    except Exception:
        pass
    return results


# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def rsi(series, n=14):
    d = series.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - (100 / (1 + g / l))


def macd(series, f=12, s=26, sig=9):
    m = series.ewm(span=f).mean() - series.ewm(span=s).mean()
    return m, m.ewm(span=sig).mean()


def atr(df, n=14):
    h = df["High"].squeeze(); l = df["Low"].squeeze(); c = df["Close"].squeeze()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compute_ssr(spx, vix, pcr, sectors):
    close = spx["Close"].squeeze(); vol = spx["Volume"].squeeze()
    high  = spx["High"].squeeze();  low = spx["Low"].squeeze()
    rsi_v = rsi(close); macd_l, macd_s = macd(close); atr_v = atr(spx)
    vix_c = vix["Close"].squeeze()
    sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean()
    sma200= close.rolling(200).mean(); ema9 = close.ewm(span=9).mean()
    bb_mid= close.rolling(20).mean(); bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2*bb_std; bb_lower = bb_mid - 2*bb_std
    stoch_k = 100*(close-low.rolling(14).min())/(high.rolling(14).max()-low.rolling(14).min())
    stoch_d = stoch_k.rolling(3).mean()
    c = close.iloc[-1]; c1 = close.iloc[-2]; c5 = close.iloc[-6]

    sigs = {}
    sigs["Above 20 SMA"]          = int(c > sma20.iloc[-1])
    sigs["Above 50 SMA"]          = int(c > sma50.iloc[-1])
    sigs["Above 200 SMA"]         = int(c > sma200.iloc[-1])
    sigs["Above 9 EMA"]           = int(c > ema9.iloc[-1])
    sigs["Higher Close (1d)"]     = int(c > c1)
    sigs["Higher Close (5d)"]     = int(c > c5)
    sigs["20 SMA > 50 SMA"]       = int(sma20.iloc[-1] > sma50.iloc[-1])
    sigs["RSI Above 50"]          = int(rsi_v.iloc[-1] > 50)
    sigs["RSI Not Overbought"]    = int(rsi_v.iloc[-1] < 70)
    sigs["RSI Not Oversold"]      = int(rsi_v.iloc[-1] > 30)
    sigs["MACD Bullish"]          = int(macd_l.iloc[-1] > macd_s.iloc[-1])
    sigs["MACD Rising"]           = int(macd_l.iloc[-1] > macd_l.iloc[-2])
    sigs["Stoch Bullish"]         = int(stoch_k.iloc[-1] > stoch_d.iloc[-1])
    sigs["Stoch Not Overbought"]  = int(stoch_k.iloc[-1] < 80)
    sigs["VIX Below 20"]          = int(vix_c.iloc[-1] < 20)
    sigs["VIX Below 25"]          = int(vix_c.iloc[-1] < 25)
    sigs["VIX Falling"]           = int(vix_c.iloc[-1] < vix_c.iloc[-2])
    sigs["VIX Below 10d Avg"]     = int(vix_c.iloc[-1] < vix_c.rolling(10).mean().iloc[-1])
    sigs["Above BB Mid"]          = int(c > bb_mid.iloc[-1])
    sigs["Above BB Lower"]        = int(c > bb_lower.iloc[-1])
    sigs["Not at BB Upper"]       = int(c < bb_upper.iloc[-1] * 0.995)
    sigs["ATR Expanding"]         = int(atr_v.iloc[-1] > atr_v.iloc[-5])
    sigs["Volume Above Average"]  = int(vol.iloc[-1] > vol.rolling(20).mean().iloc[-1])

    above   = sum(1 for t,df in sectors.items()
                  if not df.empty and len(df["Close"].squeeze())>=50
                  and df["Close"].squeeze().iloc[-1] > df["Close"].squeeze().rolling(50).mean().iloc[-1])
    total_s = sum(1 for t,df in sectors.items() if not df.empty and len(df["Close"].squeeze())>=50)
    if total_s:
        br = above / total_s
        sigs["Sector Breadth ≥ 30%"] = int(br >= 0.3)
        sigs["Sector Breadth ≥ 50%"] = int(br >= 0.5)
        sigs["Sector Breadth ≥ 70%"] = int(br >= 0.7)

    if not pcr.empty and len(pcr) >= 2:
        pc = pcr["Close"].squeeze()
        sigs["Put/Call Ratio < 1"] = int(pc.iloc[-1] < 1.0)
        sigs["Put/Call Falling"]   = int(pc.iloc[-1] < pc.iloc[-2])

    buys  = sum(1 for v in sigs.values() if v == 1)
    sells = sum(1 for v in sigs.values() if v == 0)
    total = buys + sells
    score = round((buys / total) * 100) if total > 0 else 50
    return score, buys, sells, sigs


def compute_levels(spx):
    close = spx["Close"].squeeze(); high = spx["High"].squeeze(); low = spx["Low"].squeeze()
    c = close.iloc[-1]; ph = high.iloc[-2]; pl = low.iloc[-2]; pc = close.iloc[-2]
    pivot = (ph+pl+pc)/3; atr14 = atr(spx).iloc[-1]
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


def window_bias_at(hhmm, gap=0.0):
    """
    Return (bias, label) for a given HH:MM.
    gap = today's open − prior close (positive = gap-up, negative = gap-down).
    Gap-conditional overrides based on 20-day backtest data:
      • Pre-Bull Fade 9:40–10:00  → chop on gap-up  (was 17% as bear; 100% on gap-down)
      • Afternoon Trend 2:00–3:00 → chop on gap-up  (was 33% as bear; 65-70% on gap-down)
    """
    for start, end, label, bias in TIME_WINDOWS:
        if start <= hhmm < end:
            # Gap-up override: Pre-Bull Fade → chop
            if label == "Pre-Bull Fade" and gap > GAP_THRESHOLD:
                return "chop", label + " (gap-up→chop)"
            # Gap-up override: Afternoon Trend → chop
            if label == "Afternoon Trend" and gap > GAP_THRESHOLD:
                return "chop", label + " (gap-up→chop)"
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


def generate_es_projections(base_price, daily_atr, score, gap=0.0):
    """30-minute ES projections for 23 hours starting from the next opening bell."""
    direction = ssr_direction(score)

    # ATR per 30 minutes by session type
    def slot_atr(h):
        if 9 <= h < 16:  return daily_atr * 0.13 / 2   # RTH  ~half-hour slice
        if 16 <= h < 17: return daily_atr * 0.05 / 2   # AH
        return daily_atr * 0.04 / 2                     # Overnight

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
            win_bias, win_label = window_bias_at(hhmm, gap=gap)
            wf       = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
            satr     = slot_atr(t.hour)
            move     = satr * (direction * 0.55 + wf * 0.45)
            price    = round(price + move, 1)
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
    """Return the next weekday on or after from_date."""
    d = from_date
    while d.weekday() >= 5:   # 5=Sat, 6=Sun
        d += timedelta(days=1)
    return d


def generate_spx_projections(base_price, daily_atr, score, gap=0.0):
    """Hourly SPX projections for the next/current RTH session (9:30 AM – 4:00 PM)."""
    direction = ssr_direction(score)
    slot_atr  = daily_atr / 6.5
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
    for slot in slots:
        sh, sm = map(int, slot.split(":"))
        t        = EST.localize(datetime(session_date.year, session_date.month, session_date.day, sh, sm))
        is_past  = (not all_future) and (t < now)
        win_bias, win_label = window_bias_at(slot, gap=gap)
        win_factor = {"bull": 0.5, "bear": -0.5, "chop": 0.0, "neutral": 0.0}[win_bias]
        move  = slot_atr * (direction * 0.55 + win_factor * 0.45)
        price = round(price + move, 1)
        day_label = session_date.strftime("%a") if all_future else ""
        rows.append({
            "time":      f"{day_label} {to_ampm(slot)}".strip(),
            "price":     price,
            "move":      round(move, 1),
            "rng_lo":    round(price - slot_atr * 0.4, 1),
            "rng_hi":    round(price + slot_atr * 0.4, 1),
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
    live = fetch_live()

vix_now = round(vix["Close"].squeeze().iloc[-1], 2)
score, buys, sells, signals = compute_ssr(spx, vix, pcr, sectors)
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

mc1,mc2,mc3,mc4,mc5,mc6,mc7,mc8 = st.columns(8)
for col, lbl, val, sub, vc, sc in [
    (mc1, "SSR Score",      str(score),     f"{rating.split()[0]} {rating.split()[1] if len(rating.split())>1 else ''}", color, "#94a3b8"),
    (mc2, "SSR Action",     action.split("—")[0].strip(), f"{buys}✅ {sells}❌", color, "#64748b"),
    (mc3, "ES Futures",     es_display,     chg_str(live["es_change"],live["es_pct"]), "#f1f5f9", es_chg_color),
    (mc4, "ES Last Tick",   ts1,            "ES=F  24×5", "#94a3b8", "#475569"),
    (mc5, "SPX",            spx_display,    chg_str(live["spx_change"],live["spx_pct"]), "#f1f5f9", spx_chg_color),
    (mc6, "VIX",            str(vix_now),   "Fear Index", "#f59e0b" if vix_now>20 else "#4ade80", "#64748b"),
    (mc7, "ATR (14d)",      str(levels['atr']), f"RSI: {levels['rsi']}", "#94a3b8", "#64748b"),
    (mc8, "Now",            win_icon,       cur_win[:18], BIAS_TEXT.get(cur_bias,"#94a3b8"), "#64748b"),
]:
    with col:
        st.markdown(f'<div class="metric-tile"><div class="metric-label">{lbl}</div>'
                    f'<div class="metric-val" style="color:{vc};font-size:18px">{val}</div>'
                    f'<div class="metric-sub" style="color:{sc}">{sub}</div></div>',
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
      <div style="font-size:12px;color:#94a3b8;margin:3px 0 10px">{action}</div>
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

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 3 — SIGNAL BREAKDOWN (2-column grid, full width)
# ═══════════════════════════════════════════════════════════════════════════════
with st.expander(f"📊 Signal Breakdown — {buys} Buy / {sells} Sell  (click to expand)", expanded=False):
    bull_sigs = {k:v for k,v in signals.items() if v==1}
    bear_sigs = {k:v for k,v in signals.items() if v==0}
    # split into 3 columns to use full width
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

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 4 — HOURLY PROJECTIONS (ES left, SPX right)
# ═══════════════════════════════════════════════════════════════════════════════
live_gap = round(spx_price - float(spx["Close"].squeeze().iloc[-2]), 1) if len(spx) >= 2 else 0.0
es_rows  = generate_es_projections(es_price,  levels["atr"], score, gap=live_gap)
spx_rows = generate_spx_projections(spx_price, levels["atr"], score, gap=live_gap)

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
        sectors_d[t] = yf.download(t, period="60d", interval="1d", progress=False, auto_adjust=True)

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

    # Compute actual gap for this day (open − prior close) to drive gap-conditional windows
    day_open  = float(day_5m.iloc[0])
    day_gap   = round(day_open - prev_close, 1)

    slots = ["09:30","10:00","10:30","10:45","11:00","11:15","11:30","12:00",
             "13:00","13:15","13:30","14:00","14:30","15:00","15:30","16:00"]
    # Anchor projection to open price when gap is significant (fixes systematic drift)
    proj_price = day_open if abs(day_gap) > 20 else prev_close
    projections = []
    for s in slots:
        win_bias, win_label = window_bias_at(s, gap=day_gap)
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
        correct = (p["bias"] in ("bear","chop") and actual_dir == "bear") or \
                  (p["bias"] == "bull" and actual_dir == "bull") or \
                  (p["bias"] == "chop")
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

    # Per-day tabs
    tab_labels = [td.strftime("%a %b %d") for td in last5]
    tabs = st.tabs(tab_labels)
    for tab, td in zip(tabs, last5):
        with tab:
            bt_day = run_backtest_for_day(
                td, day_series_bt, spx_d_bt, vix_d_bt, sectors_d_bt,
                daily_dates_list, offsets.get(td, 1))
            uw_day = load_uw_market_tide(td.strftime("%Y-%m-%d"))
            render_backtest_day(bt_day, uw_day)



# ═══════════════════════════════════════════════════════════════════════════════
# TRADE SIMULATOR — $2K Account · 3 DTE SPY/SPX Options
# ═══════════════════════════════════════════════════════════════════════════════

import math as _math

@st.cache_data(ttl=3600)
def load_sim_data_1m():
    """1-min SPX bars for the last 7 calendar days — used for precise TP/SL simulation."""
    spx_1m = yf.download("^GSPC", period="7d", interval="1m", progress=False, auto_adjust=True)
    spx_1m.index = spx_1m.index.tz_convert(EST)
    trading_days  = sorted(set(spx_1m.index.date))
    # Build per-day High/Low/Close series (need High & Low to detect intrabar TP/SL)
    day_hlc = {}
    for d in trading_days:
        subset = spx_1m[spx_1m.index.date == d].copy()
        # Flatten MultiIndex columns if yfinance returns them (e.g. ("High","^GSPC"))
        if isinstance(subset.columns, pd.MultiIndex):
            subset.columns = subset.columns.get_level_values(0)
        day_hlc[d] = subset[["High", "Low", "Close"]]
    return day_hlc, trading_days

def _ncdf(x):
    return 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))

def bs_price(S, K, T, r, sigma, opt_type='call'):
    """Black-Scholes option price."""
    if T < 0.0001:
        return max(0.0, S - K) if opt_type == 'call' else max(0.0, K - S)
    try:
        d1 = (_math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * _math.sqrt(T))
        d2 = d1 - sigma * _math.sqrt(T)
        if opt_type == 'call':
            return max(0.01, S * _ncdf(d1) - K * _math.exp(-r * T) * _ncdf(d2))
        else:
            return max(0.01, K * _math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1))
    except Exception:
        return 0.01


# Tradeable time slots for the simulator — only windows with directional signal.
# Chop windows (10:45-11:15, 11:30-12:00, 1PM pivot) are skipped automatically
# because window_bias_at() now returns "chop" for them, and chop = no trade.
TRADEABLE_SLOTS = [
    ("09:40", "10:00"),   # Pre-Bull Fade  (gap-conditional via window_bias_at)
    ("10:00", "10:30"),   # Bull Window
    ("11:15", "11:30"),   # Bear Continuation (only reliable Bear Dom sub-slot)
    ("13:15", "14:00"),   # Bear Window / Peak
    ("14:00", "15:00"),   # Afternoon Trend   (gap-conditional via window_bias_at)
    ("15:30", "16:00"),   # EOD Trend
]


def simulate_trades_week(day_hlc_1m, spx_d_bt, vix_d_bt, sectors_d_bt,
                          daily_dates_list, last5,
                          starting_capital=2000, ticker="SPY",
                          profit_target=0.20, stop_loss=0.50, alloc_pct=0.25,
                          dte=3):
    """
    Simulate DTE-configurable options trades using 1-minute HLC bars.
    Each bar checks High (for call TP / put SL) and Low (for put TP / call SL)
    so intrabar spikes that hit the target are correctly captured.
    """
    account     = float(starting_capital)
    all_trades  = []
    equity      = [{"label": "Start", "value": account}]
    SPY_RATIO   = 0.1    # SPY ≈ SPX / 10
    MULT        = 100    # standard options multiplier
    r           = 0.053
    total_daily = len(spx_d_bt)

    for td in last5:
        day_hlc = day_hlc_1m.get(td)
        if day_hlc is None or len(day_hlc) == 0:
            continue

        # SSR from prior day's close
        try:
            pos    = daily_dates_list.index(td)
            offset = total_daily - pos
        except ValueError:
            offset = 1
        spx_base = spx_d_bt.iloc[:-offset] if offset > 0 else spx_d_bt
        vix_base = vix_d_bt.iloc[:-offset] if offset > 0 else vix_d_bt
        sec_base = {k: v.iloc[:-offset] if offset > 0 else v for k, v in sectors_d_bt.items()}

        score, _, _, _ = compute_ssr(spx_base, vix_base, pd.DataFrame(), sec_base)
        direction       = ssr_direction(score)

        try:
            vix_iv = float(vix_base["Close"].squeeze().iloc[-1]) / 100
        except Exception:
            vix_iv = 0.20
        vix_iv = max(0.10, min(vix_iv, 0.80))

        # Compute day's gap (open − prior close) for gap-conditional window logic
        day_gap   = round(float(day_hlc["Close"].iloc[0]) - float(spx_base["Close"].squeeze().iloc[-1]), 1)
        day_label = td.strftime("%a %b %d")

        for (t_start, t_end) in TRADEABLE_SLOTS:
            # Resolve bias using the same updated logic as the main app
            w_bias, w_name = window_bias_at(t_start, gap=day_gap)

            # Skip chop windows — no tradeable signal
            if w_bias == "chop":
                continue

            # Skip windows that strongly oppose SSR direction
            if direction > 0.4 and w_bias == "bear":
                continue
            if direction < -0.4 and w_bias == "bull":
                continue

            hs, ms = map(int, t_start.split(":"))
            he, me = map(int, t_end.split(":"))

            # 1-min bars for this window — use minutes-since-midnight to avoid
            # same-hour OR-logic bug (e.g. 11:15-11:30 previously grabbed 11:00-11:59)
            start_min = hs * 60 + ms
            end_min   = he * 60 + me
            bar_mins  = day_hlc.index.hour * 60 + day_hlc.index.minute
            window_bars = day_hlc[(bar_mins >= start_min) & (bar_mins < end_min)]
            if len(window_bars) < 2:
                continue

            # Entry: first 1-min bar close
            entry_spx = float(window_bars["Close"].iloc[0])
            entry_und = entry_spx * SPY_RATIO if ticker == "SPY" else entry_spx
            opt_type  = "call" if w_bias == "bull" else "put"
            strike    = round(entry_und) if ticker == "SPY" else (round(entry_spx / 5) * 5)
            T_entry   = max(dte, 0.1) / 252   # DTE → fraction of trading year

            entry_prem = bs_price(entry_und, strike, T_entry, r, vix_iv, opt_type)
            if entry_prem < 0.10:
                continue

            # Position sizing — 25% of account per trade
            capital_to_risk = account * alloc_pct
            num_contracts   = max(1, int(capital_to_risk / (entry_prem * MULT)))
            cost            = entry_prem * MULT * num_contracts
            if cost > account * 0.95:
                num_contracts = max(1, int(account * 0.90 / (entry_prem * MULT)))
                cost          = entry_prem * MULT * num_contracts
            if cost > account:
                continue

            account -= cost

            # ── Intrabar TP/SL scan using 1-min High & Low ──
            # For calls: High tells us the best price the bar reached (could have hit TP)
            #            Low  tells us the worst price (could have hit SL)
            # For puts:  Low  → put TP (SPX dropped → put gained)
            #            High → put SL (SPX rose   → put lost)
            exit_prem   = entry_prem
            exit_spx    = entry_spx
            exit_reason = "Window End"
            be_locked   = False   # breakeven stop once up 10%
            bars_seen   = 0

            for bar_ts, bar_row in window_bars.iterrows():
                bars_seen += 1
                if bars_seen == 1:
                    continue   # skip entry bar

                T_now = max(0.0001, T_entry - bars_seen / (252 * 390))

                # Use .item() / iloc[0] guard in case yfinance MultiIndex leaks into iterrows
                bar_high  = float(bar_row["High"].iloc[0])  if hasattr(bar_row["High"],  "iloc") else float(bar_row["High"])
                bar_low   = float(bar_row["Low"].iloc[0])   if hasattr(bar_row["Low"],   "iloc") else float(bar_row["Low"])
                bar_close = float(bar_row["Close"].iloc[0]) if hasattr(bar_row["Close"], "iloc") else float(bar_row["Close"])

                # Best-case price this bar (TP candidate)
                if opt_type == "call":
                    best_spx  = bar_high
                    worst_spx = bar_low
                else:
                    best_spx  = bar_low    # put gains when SPX falls
                    worst_spx = bar_high

                best_und  = best_spx  * (SPY_RATIO if ticker == "SPY" else 1.0)
                worst_und = worst_spx * (SPY_RATIO if ticker == "SPY" else 1.0)

                prem_best  = bs_price(best_und,  strike, T_now, r, vix_iv, opt_type)
                prem_worst = bs_price(worst_und, strike, T_now, r, vix_iv, opt_type)
                prem_close = bs_price(
                    bar_close * (SPY_RATIO if ticker == "SPY" else 1.0),
                    strike, T_now, r, vix_iv, opt_type)

                pct_best  = (prem_best  - entry_prem) / entry_prem
                pct_worst = (prem_worst - entry_prem) / entry_prem

                # Breakeven lock: once up 10%, stop moves to entry (avoids giving back gains)
                if pct_best >= 0.10 and not be_locked:
                    be_locked = True

                if pct_best >= profit_target:
                    # TP hit intrabar — use the TP premium, not bar close
                    tp_prem     = entry_prem * (1 + profit_target)
                    exit_prem   = tp_prem
                    exit_spx    = best_spx
                    exit_reason = f"✅ TP +{profit_target*100:.0f}% (1-min)"
                    break
                elif be_locked and pct_worst <= 0.0:
                    # Breakeven stop triggered
                    exit_prem   = entry_prem   # exit at cost
                    exit_spx    = bar_close
                    exit_reason = "🔒 BE Stop"
                    break
                elif pct_worst <= -stop_loss:
                    sl_prem     = entry_prem * (1 - stop_loss)
                    exit_prem   = max(0.01, sl_prem)
                    exit_spx    = worst_spx
                    exit_reason = f"🛑 SL -{stop_loss*100:.0f}% (1-min)"
                    break

                exit_prem = prem_close
                exit_spx  = bar_close

            proceeds = exit_prem * MULT * num_contracts
            pnl      = proceeds - cost
            account += proceeds

            all_trades.append({
                "date":       day_label,
                "window":     w_name,
                "type":       opt_type.upper(),
                "strike":     strike,
                "entry_spx":  round(entry_spx, 1),
                "exit_spx":   round(exit_spx, 1),
                "entry_prem": round(entry_prem, 2),
                "exit_prem":  round(exit_prem, 2),
                "contracts":  num_contracts,
                "cost":       round(cost),
                "proceeds":   round(proceeds),
                "pnl":        round(pnl),
                "pnl_pct":    round(pnl / cost * 100, 1),
                "reason":     exit_reason,
                "account":    round(account),
            })
            equity.append({"label": f"{day_label} {w_name[:8]}", "value": round(account)})

    return all_trades, round(account), equity


with st.expander("💰 Trade Simulator — $2K · Options · Last Week  (click to expand)", expanded=False):
    sim_c1, sim_c2, sim_c3, sim_c4, sim_c5 = st.columns(5)
    with sim_c1:
        sim_ticker  = st.selectbox("Ticker", ["SPY", "SPX"], index=0,
                                   help="SPY recommended for $2K accounts (SPX needs $4K+/contract)")
    with sim_c2:
        sim_capital = st.number_input("Starting Capital ($)", value=2000, min_value=500, step=500)
    with sim_c3:
        sim_dte     = st.selectbox("DTE", [0, 1, 2, 3], index=3,
                                   help="0 DTE = expires same day (cheapest/riskiest), 3 DTE = 3 trading days")
    with sim_c4:
        sim_tp      = st.slider("Take Profit %", 10, 100, 20, 5)
    with sim_c5:
        sim_sl      = st.slider("Stop Loss %", 20, 80, 50, 5)

    if st.button("▶ Run Simulation", type="primary"):
        spx_d_s, vix_d_s, sec_d_s, ds_s, td_s = load_backtest_data()
        day_hlc_1m_s, td_1m_s = load_sim_data_1m()
        dd_list = list(spx_d_s.index.date)
        # Use 1-min trading days but cap to last 5
        all_days = sorted(set(day_hlc_1m_s.keys()))
        last5_s  = all_days[-5:] if len(all_days) >= 5 else all_days

        trades, final_val, equity_curve = simulate_trades_week(
            day_hlc_1m_s, spx_d_s, vix_d_s, sec_d_s, dd_list, last5_s,
            starting_capital=sim_capital, ticker=sim_ticker,
            profit_target=sim_tp/100, stop_loss=sim_sl/100, alloc_pct=0.25,
            dte=sim_dte)

        total_return = final_val - sim_capital
        total_pct    = round(total_return / sim_capital * 100, 1)
        wins  = sum(1 for t in trades if t["pnl"] > 0)
        losses= sum(1 for t in trades if t["pnl"] <= 0)
        win_rate = round(wins / len(trades) * 100, 1) if trades else 0

        # Summary tiles
        s1,s2,s3,s4,s5 = st.columns(5)
        for col, lbl, val, vc in [
            (s1, "Starting Capital", f"${sim_capital:,}", "#94a3b8"),
            (s2, "Ending Value",     f"${final_val:,}",
             "#4ade80" if final_val >= sim_capital else "#f87171"),
            (s3, "Total P&L",
             f"{'+'if total_return>=0 else ''}{total_return:,} ({total_pct:+.1f}%)",
             "#4ade80" if total_return >= 0 else "#f87171"),
            (s4, "Win Rate",  f"{win_rate}%  ({wins}W / {losses}L)",
             "#4ade80" if win_rate >= 50 else "#f59e0b"),
            (s5, "# Trades", str(len(trades)), "#94a3b8"),
        ]:
            col.markdown(
                f'<div class="metric-tile"><div class="metric-label">{lbl}</div>'
                f'<div style="font-size:18px;font-weight:800;color:{vc}">{val}</div></div>',
                unsafe_allow_html=True)

        st.markdown("<div style='margin:10px 0 4px'></div>", unsafe_allow_html=True)

        # Equity curve (simple bar using HTML)
        if len(equity_curve) > 1:
            min_v = min(e["value"] for e in equity_curve)
            max_v = max(e["value"] for e in equity_curve)
            range_v = max(max_v - min_v, 1)
            bars_html = ""
            for e in equity_curve:
                h_pct  = int(60 * (e["value"] - min_v) / range_v) + 10
                b_color= "#4ade80" if e["value"] >= sim_capital else "#f87171"
                bars_html += (
                    f'<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:40px">'
                    f'<div style="font-size:9px;color:#64748b;margin-bottom:2px">${e["value"]:,}</div>'
                    f'<div style="width:100%;height:{h_pct}px;background:{b_color};border-radius:3px 3px 0 0"></div>'
                    f'<div style="font-size:8px;color:#475569;margin-top:3px;text-align:center;word-break:break-word">{e["label"]}</div>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:16px 18px;border:1px solid #2d3250;margin-bottom:12px">'
                f'<div style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:10px">Equity Curve</div>'
                f'<div style="display:flex;gap:4px;align-items:flex-end;height:100px">{bars_html}</div>'
                f'</div>',
                unsafe_allow_html=True)

        # Trade log
        if trades:
            rows_html = ""
            for t in trades:
                pnl_c  = "#4ade80" if t["pnl"] > 0 else "#f87171"
                sign   = "+" if t["pnl"] >= 0 else ""
                tp_c   = "#4ade80" if "TP" in t["reason"] else ("#f87171" if "SL" in t["reason"] else "#94a3b8")
                t_type_c = "#4ade80" if t["type"] == "CALL" else "#f87171"
                rows_html += (
                    f'<tr style="border-bottom:1px solid #1a1f33">'
                    f'<td style="padding:6px 10px;color:#94a3b8;font-size:12px">{t["date"]}</td>'
                    f'<td style="padding:6px 8px;font-size:11px;color:#cbd5e1">{t["window"]}</td>'
                    f'<td style="padding:6px 8px;font-weight:700;color:{t_type_c}">{t["type"]}</td>'
                    f'<td style="padding:6px 8px;color:#94a3b8;font-size:11px">{sim_ticker} {t["strike"]}</td>'
                    f'<td style="padding:6px 8px;color:#94a3b8">{t["entry_spx"]:,} → {t["exit_spx"]:,}</td>'
                    f'<td style="padding:6px 8px;font-size:11px;color:#64748b">${t["entry_prem"]:.2f} → ${t["exit_prem"]:.2f}</td>'
                    f'<td style="padding:6px 8px;color:#94a3b8">{t["contracts"]}x (${t["cost"]:,})</td>'
                    f'<td style="padding:6px 8px;font-weight:700;color:{pnl_c}">{sign}${t["pnl"]:,} ({sign}{t["pnl_pct"]}%)</td>'
                    f'<td style="padding:6px 8px;color:{tp_c};font-size:11px">{t["reason"]}</td>'
                    f'<td style="padding:6px 10px;font-weight:700;color:#f1f5f9">${t["account"]:,}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<div style="background:#1e2130;border-radius:10px;padding:14px 16px;border:1px solid #2d3250">'
                f'<div style="font-size:10px;color:#64748b;letter-spacing:1.4px;text-transform:uppercase;margin-bottom:8px">'
                f'Trade Log — {sim_ticker} {sim_dte} DTE · TP {sim_tp}% · SL {sim_sl}% · 25% position sizing</div>'
                f'<div style="overflow-x:auto;overflow-y:auto;max-height:450px">'
                f'<table style="width:100%;border-collapse:collapse;color:#f1f5f9;font-size:12px;min-width:900px">'
                f'<thead><tr style="background:#0f1117">'
                f'<th style="padding:6px 10px;text-align:left;color:#64748b;font-size:10px">DATE</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">WINDOW</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">TYPE</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">STRIKE</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">SPX RANGE</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">PREMIUM</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">SIZE</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">P&amp;L</th>'
                f'<th style="padding:6px 8px;text-align:left;color:#64748b;font-size:10px">EXIT</th>'
                f'<th style="padding:6px 10px;text-align:left;color:#64748b;font-size:10px">BALANCE</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
                f'<div style="margin-top:8px;font-size:10px;color:#475569">'
                f'Premiums via Black-Scholes · VIX as IV proxy · Educational simulation only — not financial advice</div>'
                f'</div>',
                unsafe_allow_html=True)
        else:
            st.info("No trades were generated. Try adjusting the parameters or check that backtest data loaded.")
    else:
        st.markdown(
            '<div style="color:#64748b;font-size:13px;padding:12px 0">'
            'Configure parameters above and click <b style="color:#f1f5f9">▶ Run Simulation</b> to see your hypothetical P&amp;L.'
            '</div>', unsafe_allow_html=True)


st.markdown("""
<div style="text-align:center;color:#374151;font-size:11px;margin-top:10px;padding-bottom:6px">
  🔄 Auto-refreshes every 60s &nbsp;·&nbsp; ES &amp; SPX live prices update each refresh &nbsp;·&nbsp;
  SSR recomputes every 5 min &nbsp;·&nbsp; Options flow via 🦅 unusualwhales.com &nbsp;·&nbsp;
  For educational purposes only · Not financial advice
</div>
""", unsafe_allow_html=True)
