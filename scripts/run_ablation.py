#!/usr/bin/env python3
"""
Standalone 2-year walk-forward regime & ablation runner for spx-algo.

Mirrors compute_historical_analysis() in app.py without importing Streamlit.
Writes a markdown artifact to Codex/ablation-report.md (or --out path).

Usage:
    python3 scripts/run_ablation.py
    python3 scripts/run_ablation.py --out Codex/ablation-report.md

Exit codes:
    0  completed successfully
    1  data fetch failed
    2  import error
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pandas as pd
    import yfinance as yf
except ImportError as exc:
    print(f"[run_ablation] import error: {exc}")
    sys.exit(2)

# ── Constants (mirror app.py) ─────────────────────────────────────────────────
GAP_THRESHOLD      = 25.0
VIX_FEAR_THRESHOLD = 25.0
VIX_CALM_THRESHOLD = 18.0

# ATR-based directional label threshold — replaces legacy fixed 5pt threshold.
# At SPX 7500 / ATR 70, a 5pt move is ~7% of daily ATR (noise); 1/8 of ATR is meaningful.
LABEL_ATR_FRACTION    = 0.125
LABEL_THRESHOLD_FLOOR = 8.0

SECTOR_TICKERS = ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]

SIGNAL_GROUPS: dict[str, list[str]] = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA"],
    # 20 SMA > 50 SMA removed: ablation delta +0.5% — lags the death cross
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "MACD Bullish", "RSI Strong Trend"],
    # RSI Above 50 removed: ablation delta +1.0% drag (2yr +1.4pp post-prune).  False-bullish
    # votes on counter-trend bounces that fail; RSI Strong Trend (>60) covers the useful content.
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting",
                   "VIX Below 15", "VIX 1d Down", "VVIX Below 100", "VIX Term Contango"],
    # VVIX Below 100: second-order vol signal (VIX of VIX < 100 = calm).  2yr +1.9pp, 60d +2.9pp.
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%", "Sector Breadth ≥ 85%", "XLK Leadership"],
    "Extremes":   ["Stoch Bullish"],   # iter 1 restored: 252d dropped 4pp without it
    # RSI Trend Zone removed: ablation delta +1.3% — fires on early-bounce days that fail;
    # Momentum group RSI signals cover the useful directional RSI information.
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
    "Context":    ["Gap/ATR Normal", "VIX No Spike", "Gap Down Contrarian", "Pre-FOMC Eve"],
    # Gap Up Day removed from scoring: 2yr ablation +1.1pp drag — gap-up days trigger fade,
    # so binary bullish vote is counter-predictive.  Still computed in _compute_signals for
    # consistency with backtest_export but excluded from group scoring via this list.
    # Gap Down Contrarian: optional — only in sigs on large gap-down days
    # Seasonal Bull Week removed: not computed in _compute_signals(), caused 0% coverage in ablation
    "Position":   ["52w Range Upper Half",
                   # 52w Range Top 20% removed: ablation delta +1.0% — fires = 0 throughout
                   # bear trends, dragging Position group even on constructive near-term days.
                   # Above BB Mid removed: identical to Above 20 SMA (close > 20d SMA)
                   "Above Prior Day High", "Above Pivot", "Above 5d High"],
}

CORE_SIGNALS: list[str] = [s for grp in SIGNAL_GROUPS.values() for s in grp]

# Economic calendar (HIGH-impact days — FOMC, CPI, NFP, PCE for 2025 and 2026)
# 2yr walk-forward covers 2025-01-21 → 2026-04-01, so 2025 dates are required for
# accurate event-day regime classification; previously only 2026 dates were included.
# PCE added 2026-04-06: BEA Personal Income & Outlays (Fed's preferred inflation gauge).
# FOMC-only date set — used by Pre-FOMC Eve signal.
_FOMC_DATES: set[str] = {
    # 2025
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18",
    "2025-07-30","2025-09-17","2025-11-07","2025-12-17",
    # 2026
    "2026-01-28","2026-03-18","2026-04-29","2026-06-10","2026-07-29",
    "2026-09-16","2026-11-04","2026-12-16",
}

_ECON_DATES: set[str] = {
    # 2025 FOMC
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18",
    "2025-07-30","2025-09-17","2025-11-07","2025-12-17",
    # 2025 CPI
    "2025-01-15","2025-02-12","2025-03-12","2025-04-10","2025-05-13",
    "2025-06-11","2025-07-15","2025-08-12","2025-09-10","2025-10-15",
    "2025-11-13","2025-12-10",
    # 2025 NFP
    "2025-01-10","2025-02-07","2025-03-07","2025-04-04","2025-05-02",
    "2025-06-06","2025-07-03","2025-08-01","2025-09-05","2025-10-03",
    "2025-11-07","2025-12-05",
    # 2025 PCE
    "2025-01-31","2025-02-28","2025-03-28","2025-04-25","2025-05-30",
    "2025-06-27","2025-07-25","2025-08-29","2025-09-26","2025-10-31",
    "2025-11-26","2025-12-19",
    # 2026 FOMC
    "2026-01-28","2026-03-18","2026-04-29","2026-06-10","2026-07-29",
    "2026-09-16","2026-11-04","2026-12-16",
    # 2026 CPI
    "2026-01-14","2026-02-11","2026-03-11","2026-04-10","2026-05-13",
    "2026-06-10","2026-07-15","2026-08-12","2026-09-10","2026-10-14",
    "2026-11-12","2026-12-10",
    # 2026 NFP
    "2026-01-09","2026-02-06","2026-03-06","2026-04-03","2026-05-01",
    "2026-06-05","2026-07-10","2026-08-07","2026-09-04","2026-10-02",
    "2026-11-06","2026-12-04",
    # 2026 PCE
    "2026-01-30","2026-02-27","2026-03-27","2026-04-24","2026-05-29","2026-06-26",
    "2026-07-31","2026-08-28","2026-09-25","2026-10-30","2026-11-20","2026-12-18",
}


def _get_opex_friday(ref: date) -> date:
    first = ref.replace(day=1)
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def _is_opex_week(ref: date) -> bool:
    third_fri  = _get_opex_friday(ref)
    week_start = ref - timedelta(days=ref.weekday())
    week_end   = week_start + timedelta(days=4)
    return week_start <= third_fri <= week_end


# ── Signal helpers (verbatim from backtest_export.py) ─────────────────────────

def _squeeze(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col].squeeze()
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna()


def _sf(s: pd.Series, idx: int = -1, default: float = 0.0) -> float:
    try:
        return float(s.iloc[idx])
    except Exception:
        return default


def _rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(n).mean()
    loss  = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-10)))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26,
          sig: int = 9) -> tuple[pd.Series, pd.Series]:
    line   = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    signal = line.ewm(span=sig,  adjust=False).mean()
    return line, signal


def _atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hi = _squeeze(df, "High")
    lo = _squeeze(df, "Low")
    cl = _squeeze(df, "Close")
    tr = pd.concat(
        [hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


def _compute_signals(
    spx_sl: pd.DataFrame,
    vix_sl: pd.DataFrame,
    sec_sl: dict[str, pd.DataFrame],
    vvix_sl: "pd.Series | None" = None,
    vix9d_sl: "pd.Series | None" = None,
    as_of_date: "pd.Timestamp | None" = None,
) -> dict[str, int]:
    """Compute the 29 closed-bar signals (23 active + 7 display-only)."""
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs

    close  = _squeeze(spx_sl, "Close")
    high   = _squeeze(spx_sl, "High")
    low    = _squeeze(spx_sl, "Low")
    volume = (_squeeze(spx_sl, "Volume")
              if "Volume" in spx_sl.columns else pd.Series(dtype=float))
    if len(close) < 20:
        return sigs

    c       = _sf(close)
    sma20   = close.rolling(20).mean()
    sma50   = close.rolling(50).mean()
    sma200  = close.rolling(200).mean()
    rsi_v   = _rsi(close).dropna()
    ml, ms  = _macd(close)
    atr_v   = _atr_series(spx_sl)

    stoch_range = high.rolling(14).max() - low.rolling(14).min()
    stoch_safe  = stoch_range.where(stoch_range > 0)
    stoch_k     = (100 * (close - low.rolling(14).min()) / stoch_safe).fillna(50).clip(0, 100)
    stoch_d     = stoch_k.rolling(3).mean().fillna(50)

    # Trend
    sigs["Above 20 SMA"]   = int(c > _sf(sma20))
    sigs["Above 50 SMA"]   = int(c > _sf(sma50))   if len(close) >= 50  else 0
    sigs["Above 200 SMA"]  = int(c > _sf(sma200))  if len(close) >= 200 else 0
    sigs["20 SMA > 50 SMA"]= int(_sf(sma20) > _sf(sma50)) if len(close) >= 50 else 0

    # Momentum
    sigs["Higher Close (1d)"] = int(len(close) >= 2 and c > _sf(close, -2))
    sigs["Higher Close (5d)"] = int(len(close) >= 6 and c > _sf(close, -6))
    rsi_last = _sf(rsi_v, default=50.0)
    sigs["RSI Above 50"]    = int(rsi_last > 50)
    sigs["MACD Bullish"]    = int(_sf(ml) > _sf(ms))
    sigs["RSI Strong Trend"]= int(60 <= rsi_last <= 75)
    sigs["RSI Trend Zone"]  = int(45 <= rsi_last <= 65)

    # Volatility + context
    if not vix_sl.empty:
        vix_c = _squeeze(vix_sl, "Close")
        vv    = _sf(vix_c, default=20.0)
        sigs["VIX Below 20"]  = int(vv < 20)
        # VIX Falling computed FIRST so VIX Below 15 can gate on it.
        # VIX Falling: 5-day trend — aligned with backtest_export.py and app.py
        sigs["VIX Falling"]   = int(len(vix_c) >= 6 and vv < _sf(vix_c, -6))
        # VIX Below 15 CONDITIONAL on VIX Falling (2yr ablation: raw <15 has +0.8pp drag —
        # ultra-low VIX often precedes complacency corrections; require contracting momentum).
        sigs["VIX Below 15"]  = int(vv < 15 and sigs["VIX Falling"] == 1)
        # VIX 1d Down: single-session decline — independent from 5-day VIX Falling
        sigs["VIX 1d Down"]   = int(len(vix_c) >= 2 and vv < _sf(vix_c, -2))
        if len(vix_c) >= 4:
            v3d         = _sf(vix_c, -4, default=vv)
            vix_3d_chg  = (vv - v3d) / max(v3d, 1)
            sigs["VIX 3d Relief"] = int(vix_3d_chg < -0.08)
            sigs["VIX No Spike"]  = int(vix_3d_chg <= 0.08)
        else:
            sigs["VIX 3d Relief"] = 0
            sigs["VIX No Spike"]  = 1
        # ATR Contracting: percentile-based (current ATR < 85% of 20d avg) — was 1d compare.
        _atr_dna_r = atr_v.dropna()
        if len(_atr_dna_r) >= 20:
            _atr_now_r = float(_atr_dna_r.iloc[-1])
            _atr_avg_r = float(_atr_dna_r.iloc[-20:].mean())
            sigs["ATR Contracting"] = int(_atr_now_r < _atr_avg_r * 0.85)
        else:
            sigs["ATR Contracting"] = 0

    # VVIX — second-order volatility (VIX of VIX < 100 = calm options market)
    # Omitted (not 0) when vvix data is unavailable so the group falls back gracefully.
    if vvix_sl is not None and len(vvix_sl) >= 1:
        try:
            sigs["VVIX Below 100"] = int(float(vvix_sl.iloc[-1]) < 100)
        except Exception:
            pass  # omit on error

    # VIX Term Contango: VIX9D < VIX (30d) = no near-term fear premium.
    if vix9d_sl is not None and len(vix9d_sl) >= 1 and not vix_sl.empty:
        try:
            _v9_ab = float(vix9d_sl.iloc[-1])
            _v30_ab = _sf(_squeeze(vix_sl, "Close"), default=20.0)
            sigs["VIX Term Contango"] = int(_v9_ab < _v30_ab)
        except Exception:
            pass

    # Breadth — Accumulation Day: above-avg volume + intraday strength (close > open).
    if len(volume) >= 20:
        vol_avg  = volume.rolling(20).mean()
        vol_ok   = _sf(volume) > _sf(vol_avg)
        try:
            open_s   = _squeeze(spx_sl, "Open")
            price_up = _sf(close) > _sf(open_s)
        except Exception:
            price_up = len(close) >= 2 and _sf(close) > _sf(close, -2)
        sigs["Volume Above Average"] = int(vol_ok and price_up)

    total_sec = len(sec_sl)
    if total_sec:
        above = 0
        for df in sec_sl.values():
            if df.empty:
                continue
            try:
                sc = _squeeze(df, "Close")
                if len(sc) < 50:
                    continue
                above += int(_sf(sc) > _sf(sc.rolling(50).mean()))
            except Exception:
                continue
        sigs["Sector Breadth ≥ 50%"] = int((above / total_sec) >= 0.50)
        sigs["Sector Breadth ≥ 70%"] = int((above / total_sec) >= 0.70)
        sigs["Sector Breadth ≥ 85%"] = int((above / total_sec) >= 0.85)

        # XLK Leadership: tech (XLK) 5d return > defensive avg (XLP+XLU).
        try:
            xk = sec_sl.get("XLK"); xp = sec_sl.get("XLP"); xu = sec_sl.get("XLU")
            if xk is not None and xp is not None and xu is not None and \
               not xk.empty and not xp.empty and not xu.empty:
                xkc = _squeeze(xk, "Close"); xpc = _squeeze(xp, "Close"); xuc = _squeeze(xu, "Close")
                if len(xkc) >= 6 and len(xpc) >= 6 and len(xuc) >= 6:
                    xk_r = float(xkc.iloc[-1] / xkc.iloc[-6] - 1)
                    df_r = (float(xpc.iloc[-1] / xpc.iloc[-6] - 1) +
                            float(xuc.iloc[-1] / xuc.iloc[-6] - 1)) / 2
                    sigs["XLK Leadership"] = int(xk_r > df_r)
        except Exception:
            pass

    # Gap Up Day + Gap Down Contrarian — require Open column
    if "Open" in spx_sl.columns:
        open_s = _squeeze(spx_sl, "Open")
        if len(open_s) >= 2 and len(close) >= 2:
            _gap_pts = _sf(open_s) - _sf(close, -2)
            sigs["Gap Up Day"] = int(_gap_pts > GAP_THRESHOLD)
            # Gap Down Contrarian: OPTIONAL — only added when there is a large gap down.
            # Key absent on all other days so _grp_score() skips it; avoids bearish drag.
            if _gap_pts < -GAP_THRESHOLD:
                sigs["Gap Down Contrarian"] = 1

    # Extremes
    sigs["Stoch Bullish"] = int(_sf(stoch_k) > _sf(stoch_d))

    # Position
    if len(close) >= 252:
        hi52   = _sf(close.rolling(252).max())
        lo52   = _sf(close.rolling(252).min())
        rng52  = max(hi52 - lo52, 1.0)
        rpos   = (c - lo52) / rng52
        sigs["52w Range Upper Half"] = int(rpos > 0.5)
        sigs["52w Range Top 20%"]    = int(rpos > 0.80)
    elif len(close) >= 20:
        hi20   = _sf(high.rolling(20).max())
        lo20   = _sf(low.rolling(20).min())
        rng20  = max(hi20 - lo20, 1.0)
        rpos   = (c - lo20) / rng20
        sigs["52w Range Upper Half"] = int(rpos > 0.5)
        sigs["52w Range Top 20%"]    = int(rpos > 0.80)

    sigs["Above BB Mid"]       = int(c > _sf(close.rolling(20).mean()))
    if len(close) >= 2:
        sigs["Above Prior Day High"] = int(c > _sf(high, -2))
        pivot = (_sf(high, -2) + _sf(low, -2) + _sf(close, -2)) / 3.0
        sigs["Above Pivot"]    = int(c > pivot)
    if len(high) >= 6:
        sigs["Above 5d High"]  = int(c > float(high.iloc[-6:-1].max()))

    # Pre-FOMC Eve: 1 = next trading day is FOMC.
    if as_of_date is not None:
        try:
            nd = as_of_date + pd.Timedelta(days=1)
            while nd.weekday() >= 5:
                nd += pd.Timedelta(days=1)
            sigs["Pre-FOMC Eve"] = int(nd.strftime("%Y-%m-%d") in _FOMC_DATES)
        except Exception:
            sigs["Pre-FOMC Eve"] = 0

    return sigs


def _grp_score(sigs: dict[str, int]) -> int:
    ws, ww = [], []
    for grp_sigs in SIGNAL_GROUPS.values():
        present = [sigs[k] for k in grp_sigs if k in sigs]
        if present:
            ws.append(sum(present) / len(present))
            ww.append(1.0)
    return round(sum(ws) / len(ws) * 100) if ws else 50


def _pct(h: int, t: int) -> str:
    if t == 0:
        return "n/a"
    return f"{h/t:.1%} ({h}/{t})"


# ── Main ablation loop ────────────────────────────────────────────────────────

GATE_NAMES = ("gap_down", "gap_up", "group_agreement", "thursday", "strong_bear")


def run_ablation(verbose: bool = False, period: str = "2y",
                  disabled_gates: frozenset[str] = frozenset(),
                  start: str | None = None, end: str | None = None) -> dict:
    # Fixed calendar window (--start/--end) takes precedence over rolling --period.
    # Used for the held-out confirmation window, which must be a stable block of
    # history that does not shift as "today" advances (unlike period="2y"/"10y").
    _fetch_kw = {"start": start, "end": end} if start else {"period": period}
    _label    = f"{start}→{end or 'now'}" if start else period
    print(f"[run_ablation] Fetching {_label} SPX …")
    spx = yf.download("^GSPC", interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
    print(f"[run_ablation] Fetching {_label} VIX …")
    vix = yf.download("^VIX",  interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
    print(f"[run_ablation] Fetching {_label} VVIX …")
    try:
        _vvix_df = yf.download("^VVIX", interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
        vvix_close = _squeeze(_vvix_df, "Close") if not _vvix_df.empty else pd.Series(dtype=float)
    except Exception:
        vvix_close = pd.Series(dtype=float)
    print(f"[run_ablation] Fetching {_label} VIX9D …")
    try:
        _v9_df = yf.download("^VIX9D", interval="1d", progress=False, auto_adjust=True, **_fetch_kw)
        vix9d_close = _squeeze(_v9_df, "Close") if not _v9_df.empty else pd.Series(dtype=float)
    except Exception:
        vix9d_close = pd.Series(dtype=float)
    sec: dict[str, pd.DataFrame] = {}
    for t in SECTOR_TICKERS:
        try:
            sec[t] = yf.download(t, interval="1d",
                                  progress=False, auto_adjust=True, **_fetch_kw)
        except Exception:
            sec[t] = pd.DataFrame()
    print(f"[run_ablation] SPX bars: {len(spx)}, VIX bars: {len(vix)}, VVIX bars: {len(vvix_close)}")

    if spx.empty or len(spx) < 220:
        print("[run_ablation] ERROR: insufficient SPX data")
        sys.exit(1)

    close = _squeeze(spx, "Close")
    openp = _squeeze(spx, "Open")
    n     = len(spx)

    # Pre-compute ATR series for ATR-relative directional label (replaces fixed 5pt).
    _atr_v = _atr_series(spx)
    def _label_threshold(idx: int) -> float:
        try:
            _a = float(_atr_v.iloc[idx])
            if _a == _a:    # NaN check
                return max(LABEL_THRESHOLD_FLOOR, _a * LABEL_ATR_FRACTION)
        except Exception:
            pass
        return LABEL_THRESHOLD_FLOOR

    # ── Accumulators ──────────────────────────────────────────────────────────
    _reg: dict[str, dict] = {
        "vix":   {"low": [0,0], "mid": [0,0], "high": [0,0]},
        "gap":   {"up":  [0,0], "flat":[0,0], "down": [0,0]},
        "dow":   {d: [0,0] for d in range(5)},
        "event": {"event":[0,0], "normal":[0,0]},
        "opex":  {"opex": [0,0], "normal":[0,0]},
        "opex_dow": {f"opex_{d}": [0,0] for d in ["Mon","Tue","Wed","Thu","Fri"]},
    }
    base_h = 0
    base_t = 0

    # Ablation: per signal — [h_all, h_excl, t, t_excl]
    _abl: dict[str, list[int]] = {s: [0,0,0,0] for s in CORE_SIGNALS}

    START_IDX = 200   # need at least 200 bars of lookback

    for i in range(START_IDX, n - 1):
        try:
            spx_sl   = spx.iloc[:i + 1]
            dt       = spx.index[i].date()
            cutoff   = pd.Timestamp(dt)
            vix_sl   = vix[vix.index <= cutoff]
            sec_sl   = {k: v[v.index <= cutoff]
                        for k, v in sec.items() if not v.empty}
            vvix_sl  = vvix_close[vvix_close.index <= cutoff] if not vvix_close.empty else pd.Series(dtype=float)
            vix9d_sl = vix9d_close[vix9d_close.index <= cutoff] if not vix9d_close.empty else pd.Series(dtype=float)

            sigs     = _compute_signals(spx_sl, vix_sl, sec_sl, vvix_sl, vix9d_sl,
                                        as_of_date=cutoff)
            score    = _grp_score(sigs)

            # Opening gap — computed early for gap-down abstain gate and regime bucket.
            gp = float(openp.iloc[i]) - float(close.iloc[i - 1]) if i > 0 else 0.0

            nxt      = float(close.iloc[i + 1])
            cur      = float(close.iloc[i])
            _lt      = _label_threshold(i)
            up       = nxt > cur + _lt
            dn       = nxt < cur - _lt
            bull_c   = score >= 55
            bear_c   = score <= 44
            if not bull_c and not bear_c:
                continue   # neutral — skip

            # Gap-down bear abstain: mirrors backtest_export.py gate.
            # Bear calls on large-gap-down days are wrong ~68% of the time.
            if gp < -GAP_THRESHOLD and bear_c and "gap_down" not in disabled_gates:
                continue

            # Gap-up bear abstain: symmetric to gap-down.  Gap-up reversal-attempts produce
            # bad bear calls from lagging signals.
            if gp > GAP_THRESHOLD and bear_c and "gap_up" not in disabled_gates:
                continue

            # Group-agreement filter: abstain when ≥3 of 8 groups vote AGAINST SSR direction.
            # Structural filter (no calibrated thresholds) — rejects high-DISAGREEMENT calls.
            _gpd_v = {}
            for _gn, _gs in SIGNAL_GROUPS.items():
                _pr = [sigs[k] for k in _gs if k in sigs]
                if not _pr:
                    _gpd_v[_gn] = 0
                    continue
                _gs_avg = sum(_pr) / len(_pr)
                if _gs_avg > 0.5:   _gpd_v[_gn] =  1
                elif _gs_avg < 0.5: _gpd_v[_gn] = -1
                else:               _gpd_v[_gn] =  0
            _bg = sum(1 for v in _gpd_v.values() if v ==  1)
            _eg = sum(1 for v in _gpd_v.values() if v == -1)
            if "group_agreement" not in disabled_gates:
                if bull_c and _eg >= 3:
                    continue
                if bear_c and _bg >= 3:
                    continue

            # Thursday bear abstain: structural DOW drag (~48% accuracy, below random).
            if spx.index[i].weekday() == 3 and bear_c and "thursday" not in disabled_gates:    # 3 = Thursday
                continue

            # Iter 2 Monday bear abstain reverted: pure in-sample data snooping (rule found AND
            # validated on same 5yr window).  Awaiting 2015-2020 OOS confirmation.

            # Strong-bear abstain: SSR ≤ 24 = extreme pessimism already priced in.
            # Historical accuracy: 30.8% in 2yr rolling window (+2.6pp if abstained).
            if bear_c and score <= 24 and "strong_bear" not in disabled_gates:
                continue

            # Flat-move exclusion: mirrors backtest_export.py (`if not up and not dn: continue`).
            # A bull/bear-scored call on a day the price never clears the ATR-relative threshold
            # in either direction is not a testable claim -- it must be excluded, not auto-scored
            # as a miss.  This check was missing here (present only in backtest_export.py) since
            # the file's original fixed-5pt-threshold version, where flat days were rare enough
            # not to matter; the ATR-relative threshold makes the gap material, especially on
            # lower-priced/older SPX history where the threshold captures a larger share of days.
            if not up and not dn:
                continue

            correct  = (bull_c and up) or (bear_c and dn)

            base_t += 1
            if correct:
                base_h += 1

            # VIX regime
            vv  = float(_squeeze(vix_sl, "Close").iloc[-1]) if not vix_sl.empty else 20.0
            vk  = "high" if vv > VIX_FEAR_THRESHOLD else ("low" if vv < VIX_CALM_THRESHOLD else "mid")
            _reg["vix"][vk][1] += 1
            if correct: _reg["vix"][vk][0] += 1

            # Gap regime
            gk = "up" if gp > GAP_THRESHOLD else ("down" if gp < -GAP_THRESHOLD else "flat")
            _reg["gap"][gk][1] += 1
            if correct: _reg["gap"][gk][0] += 1

            # Weekday
            wd = spx.index[i].weekday()
            _reg["dow"][wd][1] += 1
            if correct: _reg["dow"][wd][0] += 1

            # Event day
            dt_s = dt.strftime("%Y-%m-%d")
            ek   = "event" if dt_s in _ECON_DATES else "normal"
            _reg["event"][ek][1] += 1
            if correct: _reg["event"][ek][0] += 1

            # OpEx
            ok = "opex" if _is_opex_week(dt) else "normal"
            _reg["opex"][ok][1] += 1
            if correct: _reg["opex"][ok][0] += 1

            # OpEx day-of-week sub-breakdown (opex weeks only)
            if ok == "opex":
                _dow_names = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
                _opex_dow_key = f"opex_{_dow_names.get(wd, 'Mon')}"
                if _opex_dow_key in _reg["opex_dow"]:
                    _reg["opex_dow"][_opex_dow_key][1] += 1
                    if correct: _reg["opex_dow"][_opex_dow_key][0] += 1

            # Signal ablation
            for sig in CORE_SIGNALS:
                if sig not in sigs:
                    continue
                excl     = {k: v for k, v in sigs.items() if k != sig}
                sc_excl  = _grp_score(excl)
                bull_ex  = sc_excl >= 55
                bear_ex  = sc_excl <= 44
                neutral  = not bull_ex and not bear_ex
                _abl[sig][0] += 1 if correct else 0   # h_all
                _abl[sig][2] += 1                      # t
                if not neutral:
                    c_excl = (bull_ex and up) or (bear_ex and dn)
                    _abl[sig][1] += 1 if c_excl else 0  # h_excl
                    _abl[sig][3] += 1                    # t_excl

            if verbose and i % 50 == 0:
                print(f"  bar {i}/{n}  score={score}  correct={correct}  base={base_h}/{base_t}")

        except Exception as exc:
            if verbose:
                print(f"  [skip] bar {i}: {exc}")
            continue

    return {
        "regime":         _reg,
        "ablation":       _abl,
        "baseline_hits":  base_h,
        "baseline_total": base_t,
        "n_bars":         n,
        "start_date":     str(spx.index[START_IDX].date()),
        "end_date":       str(spx.index[n - 2].date()),
    }


# ── Markdown report ──────────────────────────────────────────────────────────

_DOW = ["Mon","Tue","Wed","Thu","Fri"]

def _fmt(h: int, t: int) -> str:
    if t == 0:
        return "n/a (0)"
    return f"{h/t:.1%}  ({h}/{t})"


def build_report(res: dict) -> str:
    bh  = res["baseline_hits"]
    bt  = res["baseline_total"]
    reg = res["regime"]
    abl = res["ablation"]
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    lines: list[str] = []
    lines.append("# Ablation Report — SPX Algo Core SSR")
    lines.append("")
    lines.append(f"Generated: `{now}`  ")
    lines.append(f"Walk-forward period: `{res['start_date']}` → `{res['end_date']}`  ")
    lines.append(f"Total directional calls evaluated: `{bt}`  ")
    lines.append(f"**Baseline accuracy: {_fmt(bh, bt)}**")
    lines.append("")

    # ── Regime breakdown ─────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Regime Breakdown")
    lines.append("")

    # VIX
    lines.append("### VIX Regime")
    lines.append("")
    lines.append("| Regime | Accuracy | Calls |")
    lines.append("|--------|----------|-------|")
    for k in ["low","mid","high"]:
        h, t = reg["vix"][k]
        lines.append(f"| VIX:{k} | {_pct(h,t)} | {t} |")
    lines.append("")

    # Gap
    lines.append("### Gap Regime")
    lines.append("")
    lines.append("| Regime | Accuracy | Calls |")
    lines.append("|--------|----------|-------|")
    for k in ["up","flat","down"]:
        h, t = reg["gap"][k]
        lines.append(f"| gap:{k} | {_pct(h,t)} | {t} |")
    lines.append("")

    # Weekday
    lines.append("### Day of Week")
    lines.append("")
    lines.append("| Day | Accuracy | Calls |")
    lines.append("|-----|----------|-------|")
    for d in range(5):
        h, t = reg["dow"][d]
        lines.append(f"| {_DOW[d]} | {_pct(h,t)} | {t} |")
    lines.append("")

    # Event
    lines.append("### Event Days (FOMC/CPI/NFP/PCE)")
    lines.append("")
    lines.append("| Type | Accuracy | Calls |")
    lines.append("|------|----------|-------|")
    for k in ["event","normal"]:
        h, t = reg["event"][k]
        lines.append(f"| {k} | {_pct(h,t)} | {t} |")
    lines.append("")

    # OpEx
    lines.append("### OpEx Week")
    lines.append("")
    lines.append("| Type | Accuracy | Calls |")
    lines.append("|------|----------|-------|")
    for k in ["opex","normal"]:
        h, t = reg["opex"][k]
        lines.append(f"| {k} | {_pct(h,t)} | {t} |")
    lines.append("")

    # OpEx DOW sub-breakdown
    lines.append("### OpEx Day-of-Week (OpEx weeks only)")
    lines.append("")
    lines.append("| Day | Accuracy | Calls |")
    lines.append("|-----|----------|-------|")
    for dname in ["Mon","Tue","Wed","Thu","Fri"]:
        h, t = reg["opex_dow"].get(f"opex_{dname}", [0,0])
        lines.append(f"| {dname} | {_pct(h,t)} | {t} |")
    lines.append("")

    # ── Signal ablation ───────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Signal Ablation")
    lines.append("")
    lines.append(
        "Each row shows what happens when one signal is removed from the model.  \n"
        "**Delta** = accuracy-excl minus baseline accuracy (positive = signal hurts; "
        "negative = signal helps).  \n"
        "**Coverage** = fraction of directional calls preserved after removal."
    )
    lines.append("")
    lines.append("| Signal | Group | Baseline Acc | Excl Acc | Delta | Coverage |")
    lines.append("|--------|-------|-------------|----------|-------|----------|")

    # Build group lookup
    sig_to_group = {s: g for g, ss in SIGNAL_GROUPS.items() for s in ss}

    # Compute delta for sorting
    rows = []
    base_acc = bh / bt if bt else 0.0
    for sig in CORE_SIGNALS:
        h_all, h_excl, t, t_excl = abl[sig]
        sig_base_acc = h_all / t if t else 0.0
        excl_acc     = h_excl / t_excl if t_excl else 0.0
        delta        = excl_acc - sig_base_acc
        coverage     = t_excl / t if t else 0.0
        grp          = sig_to_group.get(sig, "?")
        rows.append((sig, grp, sig_base_acc, excl_acc, delta, coverage, h_all, t, h_excl, t_excl))

    # Sort by delta ascending (most helpful signals first — removing them hurts most)
    rows.sort(key=lambda r: r[4])

    for sig, grp, sig_acc, excl_acc, delta, cov, h_all, t, h_excl, t_excl in rows:
        sig_acc_s  = f"{sig_acc:.1%}" if t      else "n/a"
        excl_acc_s = f"{excl_acc:.1%} ({h_excl}/{t_excl})" if t_excl else "n/a (coverage loss)"
        delta_s    = f"{delta:+.1%}"  if t_excl else "n/a"
        cov_s      = f"{cov:.0%}"
        lines.append(f"| {sig} | {grp} | {sig_acc_s} | {excl_acc_s} | {delta_s} | {cov_s} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Equal group weights used throughout (ablation-consistent, no drift dampening).")
    lines.append("- Flat days (< 5pt SPX move) are excluded — model makes no directional claim.")
    lines.append("- Session-open and live-overlay signals excluded (closed-bar core only).")
    lines.append("- Regime breakdown and ablation share the same walk-forward universe.")
    lines.append("")

    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward regime & ablation runner (default 2y)")
    ap.add_argument("--out",     default=str(ROOT / "Codex" / "ablation-report.md"),
                    help="Output markdown path")
    ap.add_argument("--period",  default="2y", help="yfinance history period (2y, 5y, max)")
    ap.add_argument("--start",   default=None, help="Fixed window start date YYYY-MM-DD "
                    "(overrides --period; use with --end for a stable held-out window)")
    ap.add_argument("--end",     default=None, help="Fixed window end date YYYY-MM-DD "
                    "(only used with --start)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--disable-gate", action="append", default=[], choices=list(GATE_NAMES),
                    help="Disable a named abstain gate for this run (repeatable). "
                         f"Choices: {', '.join(GATE_NAMES)}")
    args = ap.parse_args()

    res    = run_ablation(verbose=args.verbose, period=args.period,
                          disabled_gates=frozenset(args.disable_gate),
                          start=args.start, end=args.end)
    report = build_report(res)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"[run_ablation] Written → {out}")

    bh = res["baseline_hits"]
    bt = res["baseline_total"]
    acc = bh / bt if bt else 0
    print(f"[run_ablation] Baseline accuracy: {acc:.1%} ({bh}/{bt})")
    sys.exit(0)


if __name__ == "__main__":
    main()
