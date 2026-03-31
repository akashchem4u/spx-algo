#!/usr/bin/env python3
"""
Standalone backtest export for spx-algo validation artifacts.

Runs a fast directional-accuracy spot-check on the last 60 trading days
without importing the Streamlit app.  Safe to call from CI or the
run_validation_review.py --profile behavior gate.

Usage:
    python3 scripts/backtest_export.py
    python3 scripts/backtest_export.py --days 30
    python3 scripts/backtest_export.py --out /path/to/output.json

Exit codes:
    0  accuracy above minimum threshold
    1  accuracy below threshold (flag for review)
    2  data fetch failed
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ImportError as exc:
    print(json.dumps({"ok": False, "error": f"import error: {exc}"}))
    sys.exit(2)


# ── constants duplicated from app.py (no Streamlit import) ───────────────────
GAP_THRESHOLD     = 25.0
VIX_FEAR_THRESHOLD = 25.0
VIX_CALM_THRESHOLD = 18.0
SIGNAL_GROUPS = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA", "20 SMA > 50 SMA"],
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "RSI Above 50", "MACD Bullish",
                   "RSI Strong Trend"],
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting",
                   "VIX Below 15", "VIX 3d Relief", "VIX 1d Down"],
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%",
                   "Sector Breadth ≥ 70%", "Sector Breadth ≥ 85%"],
    "Extremes":   ["Stoch Bullish", "RSI Trend Zone"],
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
    "Context":    ["Gap/ATR Normal", "VIX No Spike"],
    "Position":   ["52w Range Upper Half", "52w Range Top 20%", "Above BB Mid",
                   "Above Prior Day High", "Above Pivot", "Above 5d High"],
}

MIN_ACCURACY_THRESHOLD = 0.48   # flag if daily directional accuracy drops below 48%


def _squeeze(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col].squeeze()
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna()


def _safe_float(s: pd.Series, idx: int = -1, default: float = 0.0) -> float:
    try:
        return float(s.iloc[idx])
    except Exception:
        return default


def _compute_signals_fast(spx_sl: pd.DataFrame, vix_sl: pd.DataFrame) -> dict[str, int]:
    """
    Lightweight signal computation for a historical slice.
    Mirrors the core logic in compute_ssr() without the Streamlit dependency.
    Only computes signals derivable from SPX + VIX daily bars.
    """
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs

    close = _squeeze(spx_sl, "Close")
    volume = _squeeze(spx_sl, "Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    open_s = _squeeze(spx_sl, "Open")  if "Open"  in spx_sl.columns else pd.Series(dtype=float)

    if len(close) < 20:
        return sigs

    c = _safe_float(close)

    # Trend signals
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sigs["Above 20 SMA"]    = int(c > _safe_float(sma20))
    sigs["Above 50 SMA"]    = int(c > _safe_float(sma50))  if len(close) >= 50  else 0
    sigs["Above 200 SMA"]   = int(c > _safe_float(sma200)) if len(close) >= 200 else 0
    sigs["20 SMA > 50 SMA"] = int(_safe_float(sma20) > _safe_float(sma50)) if len(close) >= 50 else 0

    # Momentum
    sigs["Higher Close (1d)"] = int(len(close) >= 2 and c > _safe_float(close, -2))
    sigs["Higher Close (5d)"] = int(len(close) >= 6 and c > _safe_float(close, -6))

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    rsi_v = rsi.dropna()
    rsi_last = _safe_float(rsi_v, default=50)
    sigs["RSI Above 50"]    = int(rsi_last > 50)
    sigs["RSI Strong Trend"]= int(60 <= rsi_last <= 75)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal_line = macd.ewm(span=9, adjust=False).mean()
    sigs["MACD Bullish"] = int(_safe_float(macd) > _safe_float(signal_line))

    # VIX signals
    if not vix_sl.empty:
        vix_c = _squeeze(vix_sl, "Close")
        if len(vix_c) >= 1:
            vv = _safe_float(vix_c, default=20)
            sigs["VIX Below 20"]  = int(vv < 20)
            sigs["VIX Below 15"]  = int(vv < 15)
            sigs["VIX Falling"]   = int(len(vix_c) >= 2 and vv < _safe_float(vix_c, -2))
            sigs["VIX 1d Down"]   = int(len(vix_c) >= 2 and vv < _safe_float(vix_c, -2))
            if len(vix_c) >= 4:
                v3d = _safe_float(vix_c, -4, default=vv)
                sigs["VIX 3d Relief"] = int((vv - v3d) / max(v3d, 1) < -0.05)
            if len(vix_c) >= 4:
                vix_max3 = float(vix_c.iloc[-4:-1].max())
                sigs["VIX No Spike"] = int(vix_max3 < vv * 1.15)

    # Volume
    if len(volume) >= 20:
        vol_avg = volume.rolling(20).mean()
        sigs["Volume Above Average"] = int(_safe_float(volume) > _safe_float(vol_avg))

    # Position
    if len(close) >= 252:
        h52 = close.rolling(252).max()
        l52 = close.rolling(252).min()
        mid = (_safe_float(h52) + _safe_float(l52)) / 2
        sigs["52w Range Upper Half"] = int(c > mid)
        sigs["52w Range Top 20%"]    = int(c > _safe_float(h52) * 0.80)

    bb_mid = close.rolling(20).mean()
    sigs["Above BB Mid"] = int(c > _safe_float(bb_mid))

    if len(close) >= 2:
        sigs["Above Prior Day High"] = int(c > _safe_float(close, -2))

    if len(close) >= 6:
        sigs["Above 5d High"] = int(c > float(close.iloc[-6:-1].max()))

    return sigs


def _grp_score(sigs: dict[str, int]) -> int:
    ws, ww = [], []
    for gn, gs in SIGNAL_GROUPS.items():
        pr = [sigs[k] for k in gs if k in sigs]
        if pr:
            ws.append(sum(pr) / len(pr))
            ww.append(1.0)
    return round(sum(ws) / len(ws) * 100) if ws else 50


def run_backtest(days: int = 60) -> dict:
    """
    Fetch the last `days`+buffer trading days, run a walk-forward spot-check,
    and return a summary dict suitable for the validation artifact.
    """
    try:
        period = f"{max(days * 2, 120)}d"  # extra buffer for indicator warmup
        spx = yf.download("^GSPC", period=period, interval="1d", progress=False, auto_adjust=True)
        vix = yf.download("^VIX",  period=period, interval="1d", progress=False, auto_adjust=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "source": "yfinance_download"}

    if spx.empty or vix.empty:
        return {"ok": False, "error": "empty data from yfinance", "source": "yfinance_download"}

    close = _squeeze(spx, "Close")
    n = len(spx)
    if n < 40:
        return {"ok": False, "error": f"insufficient data: {n} bars", "source": "data_length"}

    # Walk-forward: evaluate last `days` bars that have a next-day outcome
    eval_start = max(30, n - days - 1)
    results = []

    for i in range(eval_start, n - 1):
        spx_sl = spx.iloc[:i + 1]
        vix_sl = vix.iloc[:i + 1]
        sigs   = _compute_signals_fast(spx_sl, vix_sl)
        if not sigs:
            continue
        score = _grp_score(sigs)
        bull_call = score >= 55
        bear_call = score <= 44
        if not bull_call and not bear_call:
            continue

        nxt = float(close.iloc[i + 1])
        cur = float(close.iloc[i])
        up  = nxt > cur + 5
        dn  = nxt < cur - 5
        if not up and not dn:
            continue

        correct = (bull_call and up) or (bear_call and dn)
        results.append({
            "date":    spx.index[i].strftime("%Y-%m-%d"),
            "score":   score,
            "bull":    bull_call,
            "bear":    bear_call,
            "up":      up,
            "correct": correct,
        })

    if not results:
        return {"ok": False, "error": "no directional days in evaluation window"}

    hits   = sum(1 for r in results if r["correct"])
    total  = len(results)
    acc    = round(hits / total, 4)
    passes = acc >= MIN_ACCURACY_THRESHOLD

    # VIX regime breakdown
    vix_last = 20.0
    try:
        vix_last = float(_squeeze(vix, "Close").iloc[-1])
    except Exception:
        pass
    regime = "high_vix" if vix_last > VIX_FEAR_THRESHOLD else ("low_vix" if vix_last < VIX_CALM_THRESHOLD else "mid_vix")

    return {
        "ok":               passes,
        "accuracy":         acc,
        "hits":             hits,
        "total":            total,
        "threshold":        MIN_ACCURACY_THRESHOLD,
        "eval_days":        days,
        "vix_last":         vix_last,
        "regime":           regime,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "recent_results":   results[-5:],   # last 5 days for spot-check
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="spx-algo standalone backtest export")
    parser.add_argument("--days",  type=int, default=60, help="Evaluation window in trading days")
    parser.add_argument("--out",   default=None, help="Write JSON output to this file path")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout (only write file)")
    args = parser.parse_args(argv)

    result = run_backtest(days=args.days)
    output = json.dumps(result, indent=2, default=str)

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    if not args.quiet:
        print(output)

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
