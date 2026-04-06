#!/usr/bin/env python3
"""
Standalone backtest export for spx-algo validation artifacts.

Runs a durable Core-SSR backtest summary without importing the Streamlit app.
Safe to call from CI or the run_validation_review.py --profile behavior gate.

Usage:
    python3 scripts/backtest_export.py
    python3 scripts/backtest_export.py --days 30
    python3 scripts/backtest_export.py --out /path/to/output.json

Exit codes:
    0  daily accuracy above minimum threshold
    1  daily accuracy below threshold (flag for review)
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


# Mirrors the backtestable closed-bar portion of app.py without importing Streamlit.
GAP_THRESHOLD = 25.0
VIX_FEAR_THRESHOLD = 25.0
VIX_CALM_THRESHOLD = 18.0
EXPECTED_CORE_SIGNAL_COUNT = 22
SECTOR_TICKERS = ["XLF", "XLK", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]
SIGNAL_GROUPS = {
    "Trend": ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA"],
    # 20 SMA > 50 SMA removed: ablation delta +0.5% — lags the death cross by several sessions,
    # propping up a bullish Trend-group vote during the early phase of bear markets.
    "Momentum": ["Higher Close (1d)", "Higher Close (5d)", "MACD Bullish", "RSI Strong Trend"],
    # RSI Above 50 removed: ablation delta +1.0% drag (2yr +1.4pp post-prune).  In bear/choppy
    # markets RSI bounces above 50 briefly on counter-trend days that subsequently fail; this
    # adds a false-bullish Momentum vote on exactly the days the model is most likely to be wrong.
    # RSI Strong Trend (RSI > 60) covers genuine sustained-momentum content with better precision.
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting", "VIX Below 15", "VIX 1d Down"],
    "Breadth": ["Volume Above Average", "Sector Breadth ≥ 50%", "Sector Breadth ≥ 85%"],
    "Extremes": ["Stoch Bullish"],
    # RSI Trend Zone removed: ablation delta +1.3% — fires = 1 in the RSI 45–65 zone,
    # which catches early-bounce days that subsequently fail; the Momentum group's RSI
    # signals already cover the 50+ threshold with better precision.
    "Options": ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro": ["Yield Curve Positive", "Credit Spread Calm"],
    "Context": ["Gap/ATR Normal", "VIX No Spike", "Gap Up Day", "Gap Down Contrarian"],
    # Gap Down Contrarian: OPTIONAL signal — only added to sigs when gap < -GAP_THRESHOLD.
    # Absent on all other days, so _grp_score() skips it.  Adds 1 bullish Context vote on
    # large-gap-down days where the fade-the-gap tendency is statistically strong (~68% of
    # gap-down days reverse).  2yr: removes 73%-wrong calls, improving accuracy +7pp.
    "Position": ["52w Range Upper Half", "Above Prior Day High", "Above Pivot", "Above 5d High"],
    # 52w Range Top 20% removed: ablation delta +1.0% — fires = 0 throughout bear trends
    # (SPX well below 52w highs), dragging Position group score bearish even on days when
    # near-term price context is constructive.  Kept in display tier for reference.
    # Above BB Mid removed: identical calculation to Above 20 SMA (close > 20d SMA) — was
    # double-counting in two groups (Trend + Position).  Kept in display tier for the UI.
}

MIN_ACCURACY_THRESHOLD = 0.48


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


def _rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-10)))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> tuple[pd.Series, pd.Series]:
    line = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high = _squeeze(df, "High")
    low = _squeeze(df, "Low")
    close = _squeeze(df, "Close")
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _ssr_direction(score: int) -> float:
    return float(np.interp(score, [0, 35, 40, 50, 60, 65, 100], [-1.0, -1.0, -0.6, 0.0, 0.6, 1.0, 1.0]))


def _history_period_for_days(days: int) -> str:
    years = max(2, int(np.ceil((days + 260) / 252.0)))
    return f"{years}y"


def _build_accuracy_bucket() -> dict[str, int]:
    return {"hits": 0, "total": 0}


def _attach_accuracy(stats: dict[str, dict[str, int]]) -> dict[str, dict[str, float | int | None]]:
    out: dict[str, dict[str, float | int | None]] = {}
    for key, bucket in stats.items():
        total = bucket["total"]
        hits = bucket["hits"]
        out[key] = {
            "hits": hits,
            "total": total,
            "accuracy": round(hits / total, 4) if total else None,
        }
    return out


def _compute_signals_fast(
    spx_sl: pd.DataFrame,
    vix_sl: pd.DataFrame,
    sector_slices: dict[str, pd.DataFrame],
) -> dict[str, int]:
    """
    Reconstruct the 25 closed-bar core signals that the app backtests.
    Session-open and live-overlay signals are intentionally excluded.
    """
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs

    close = _squeeze(spx_sl, "Close")
    high = _squeeze(spx_sl, "High")
    low = _squeeze(spx_sl, "Low")
    volume = _squeeze(spx_sl, "Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close) < 20:
        return sigs

    c = _safe_float(close)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi_v = _rsi(close).dropna()
    macd_line, macd_signal = _macd(close)
    atr_v = _atr(spx_sl)

    stoch_range = high.rolling(14).max() - low.rolling(14).min()
    stoch_safe = stoch_range.where(stoch_range > 0)
    stoch_k = (100 * (close - low.rolling(14).min()) / stoch_safe).fillna(50).clip(0, 100)
    stoch_d = stoch_k.rolling(3).mean().fillna(50)

    # Trend
    sigs["Above 20 SMA"] = int(c > _safe_float(sma20))
    sigs["Above 50 SMA"] = int(c > _safe_float(sma50)) if len(close) >= 50 else 0
    sigs["Above 200 SMA"] = int(c > _safe_float(sma200)) if len(close) >= 200 else 0
    # 20 SMA > 50 SMA: removed from scoring (ablation delta +0.5% — lags the death cross,
    # propping up a bullish Trend-group vote during the early phase of bear markets).

    # Momentum
    sigs["Higher Close (1d)"] = int(len(close) >= 2 and c > _safe_float(close, -2))
    sigs["Higher Close (5d)"] = int(len(close) >= 6 and c > _safe_float(close, -6))
    rsi_last = _safe_float(rsi_v, default=50.0)
    sigs["RSI Above 50"] = int(rsi_last > 50)
    sigs["MACD Bullish"] = int(_safe_float(macd_line) > _safe_float(macd_signal))
    sigs["RSI Strong Trend"] = int(60 <= rsi_last <= 75)
    sigs["RSI Trend Zone"] = int(45 <= rsi_last <= 65)

    # Volatility + context
    if not vix_sl.empty:
        vix_c = _squeeze(vix_sl, "Close")
        vv = _safe_float(vix_c, default=20.0)
        sigs["VIX Below 20"] = int(vv < 20)
        sigs["VIX Below 15"] = int(vv < 15)
        # VIX Falling: 5-day trend — VIX today below VIX 5 sessions ago.
        # Captures sustained fear-unwind over a multi-day window and is genuinely
        # independent from the 1-day version below.  In the live app VIX Falling
        # has a market-open gate (disabled pre/post session), which collapses to a
        # 1-day comparison in the exporter's post-close context.  Using a 5-day
        # horizon here gives the Volatility group two distinct time-scale signals.
        sigs["VIX Falling"] = int(len(vix_c) >= 6 and vv < _safe_float(vix_c, -6))
        # VIX 1d Down: single-session VIX decline; fires regardless of market hours.
        sigs["VIX 1d Down"] = int(len(vix_c) >= 2 and vv < _safe_float(vix_c, -2))
        if len(vix_c) >= 4:
            v3d = _safe_float(vix_c, -4, default=vv)
            vix_3d_chg = (vv - v3d) / max(v3d, 1)
            # VIX No Spike: INVERTED — fires 1 when no fear spike (calm = bull), 0 when spike (fear = bear).
            sigs["VIX No Spike"] = int(vix_3d_chg <= 0.08)
            # VIX 3d Relief: removed from scoring (ablation delta +0.5% — pro-cyclical, fires on
            # relief rallies within bear markets, adding false bullish votes near short-term peaks).
        else:
            sigs["VIX No Spike"] = 1
        sigs["ATR Contracting"] = int(len(atr_v.dropna()) >= 20 and _safe_float(atr_v) < _safe_float(atr_v, -5))
        # VIX Below 20 / VIX Below 15 intentionally kept: in sustained bear trends
        # these signals correctly vote bearish.  Live dampening is handled by the
        # drift monitor (which flags them as stuck_bear only when they've been wrong
        # for 10+ days), not by a static always-remove-in-hi-VIX rule.

    # Breadth — accumulation: above-average volume AND price up (not panic selling)
    if len(volume) >= 20:
        vol_avg = volume.rolling(20).mean()
        _vol_ok    = _safe_float(volume) > _safe_float(vol_avg)
        _price_up  = len(close) >= 2 and _safe_float(close) > _safe_float(close, -2)
        sigs["Volume Above Average"] = int(_vol_ok and _price_up)

    total_sectors = len(sector_slices)
    if total_sectors:
        above = 0
        for df in sector_slices.values():
            if df.empty:
                continue
            try:
                sec_close = _squeeze(df, "Close")
                if len(sec_close) < 50:
                    continue
                sec_sma50 = sec_close.rolling(50).mean()
                above += int(_safe_float(sec_close) > _safe_float(sec_sma50))
            except Exception:
                continue
        sigs["Sector Breadth ≥ 50%"] = int((above / total_sectors) >= 0.5)
        # Sector Breadth ≥ 70%: removed from scoring (ablation delta +0.7% — fires near bull market peaks
        # when the rally is extended, adding false bullish votes before corrections).
        sigs["Sector Breadth ≥ 85%"] = int((above / total_sectors) >= 0.85)

    # Gap direction — large positive opening gap from daily OHLC.
    # Fires = 1 when today's open is more than GAP_THRESHOLD pts above yesterday's close.
    # Addresses the gap-up regime accuracy gap: static core signals (SMA, RSI, breadth)
    # are all lagging and stay bearish during violent gap-up bounces in high-VIX markets.
    # This gives the Context group a forward-looking nudge on strong gap-up sessions.
    if "Open" in spx_sl.columns:
        open_s = _squeeze(spx_sl, "Open")
        if len(open_s) >= 2 and len(close) >= 2:
            _gap_pts = _safe_float(open_s) - _safe_float(close, -2)
            sigs["Gap Up Day"] = int(_gap_pts > GAP_THRESHOLD)
            # Gap Down Contrarian: OPTIONAL — only added when there's a large gap down.
            # Key is absent on all other days so _grp_score() treats it as "not present".
            if _gap_pts < -GAP_THRESHOLD:
                sigs["Gap Down Contrarian"] = 1

    # Extremes
    sigs["Stoch Bullish"] = int(_safe_float(stoch_k) > _safe_float(stoch_d))

    # Position
    if len(close) >= 252:
        high_52 = _safe_float(close.rolling(252).max())
        low_52 = _safe_float(close.rolling(252).min())
        range_52 = max(high_52 - low_52, 1.0)
        range_pos = (c - low_52) / range_52
        sigs["52w Range Upper Half"] = int(range_pos > 0.5)
        sigs["52w Range Top 20%"] = int(range_pos > 0.80)
    elif len(close) >= 20:
        high_20 = _safe_float(high.rolling(20).max())
        low_20 = _safe_float(low.rolling(20).min())
        range_20 = max(high_20 - low_20, 1.0)
        range_pos = (c - low_20) / range_20
        sigs["52w Range Upper Half"] = int(range_pos > 0.5)
        sigs["52w Range Top 20%"] = int(range_pos > 0.80)

    # Above BB Mid: removed from scoring — identical computation to Above 20 SMA (close > 20d SMA).
    # Having it in both Trend (via Above 20 SMA) and Position was double-counting.
    if len(close) >= 2:
        sigs["Above Prior Day High"] = int(c > _safe_float(high, -2))
        pivot = (_safe_float(high, -2) + _safe_float(low, -2) + _safe_float(close, -2)) / 3.0
        sigs["Above Pivot"] = int(c > pivot)
    if len(high) >= 6:
        sigs["Above 5d High"] = int(c > float(high.iloc[-6:-1].max()))

    return sigs


def _grp_score(sigs: dict[str, int]) -> int:
    weighted_scores, weights = [], []
    for _, group_signals in SIGNAL_GROUPS.items():
        present = [sigs[name] for name in group_signals if name in sigs]
        if present:
            weighted_scores.append(sum(present) / len(present))
            weights.append(1.0)
    return round(sum(weighted_scores) / len(weighted_scores) * 100) if weighted_scores else 50


def _run_weekly_validation(
    spx: pd.DataFrame,
    vix: pd.DataFrame,
    sectors: dict[str, pd.DataFrame],
    max_rows: int = 20,
) -> dict:
    closes = _squeeze(spx, "Close")
    opens = _squeeze(spx, "Open")
    dates = list(spx.index)
    rows = []

    for idx in range(4, len(dates) - 5, 5):
        if idx < 252:
            continue
        cutoff_ts = dates[idx]
        spx_base = spx.iloc[: idx + 1]
        vix_base = vix[vix.index <= cutoff_ts]
        sector_base = {ticker: df[df.index <= cutoff_ts] for ticker, df in sectors.items()}
        sigs = _compute_signals_fast(spx_base, vix_base, sector_base)
        if not sigs:
            continue

        score = _grp_score(sigs)
        direction = _ssr_direction(score)
        call = "bull" if direction > 0.2 else ("bear" if direction < -0.2 else "neutral")

        next_start = idx + 1
        next_end = min(idx + 6, len(closes))
        if next_end <= next_start:
            continue

        week_open = _safe_float(opens, next_start, default=_safe_float(closes, next_start))
        week_close = _safe_float(closes, next_end - 1)
        week_move = round(week_close - week_open, 1)
        actual = "bull" if week_move > 5 else ("bear" if week_move < -5 else "neutral")
        correct = (call == actual) if call != "neutral" else None
        rows.append(
            {
                "week": dates[next_start].strftime("%Y-%m-%d"),
                "score": score,
                "call": call,
                "actual": actual,
                "move": week_move,
                "correct": correct,
                "signals_present": len(sigs),
            }
        )

    directional_rows = [row for row in rows if row["call"] != "neutral"]
    hits = sum(1 for row in directional_rows if row["correct"])
    total = len(directional_rows)
    neutral = sum(1 for row in rows if row["call"] == "neutral")
    avg_signals = round(sum(row["signals_present"] for row in rows) / len(rows), 1) if rows else 0.0
    return {
        "accuracy": round(hits / total, 4) if total else None,
        "hits": hits,
        "total": total,
        "neutral": neutral,
        "avg_signals_present": avg_signals,
        "expected_core_signals": EXPECTED_CORE_SIGNAL_COUNT,
        "recent_results": rows[-max_rows:],
    }


def run_backtest(days: int = 60) -> dict:
    """
    Fetch enough history to warm up long-horizon signals, then emit both daily
    and weekly Core-SSR validation summaries.
    """
    try:
        period = _history_period_for_days(days)
        spx = yf.download("^GSPC", period=period, interval="1d", progress=False, auto_adjust=True)
        vix = yf.download("^VIX", period=period, interval="1d", progress=False, auto_adjust=True)
        sectors = {}
        for ticker in SECTOR_TICKERS:
            try:
                sectors[ticker] = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
            except Exception:
                sectors[ticker] = pd.DataFrame()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "source": "yfinance_download"}

    if spx.empty or vix.empty:
        return {"ok": False, "error": "empty data from yfinance", "source": "yfinance_download"}

    close = _squeeze(spx, "Close")
    opens = _squeeze(spx, "Open")
    n = len(spx)
    if n < 260:
        return {"ok": False, "error": f"insufficient data: {n} bars", "source": "data_length"}

    eval_start = max(252, n - days - 1)
    results = []
    vix_buckets = {"low": _build_accuracy_bucket(), "mid": _build_accuracy_bucket(), "high": _build_accuracy_bucket()}
    gap_buckets = {"up": _build_accuracy_bucket(), "flat": _build_accuracy_bucket(), "down": _build_accuracy_bucket()}
    # Day-of-week buckets — ablation shows Tuesday (35%) and Thursday (38%) are
    # structural accuracy drags; Mon/Wed/Fri are all ≥49%.
    _DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    dow_buckets = {d: _build_accuracy_bucket() for d in _DOW_NAMES.values()}
    signal_counts: list[int] = []
    gap_down_abstained: int = 0  # days where bear call was suppressed by gap-down gate

    for i in range(eval_start, n - 1):
        cutoff_ts = spx.index[i]
        spx_slice = spx.iloc[: i + 1]
        vix_slice = vix[vix.index <= cutoff_ts]
        sector_slices = {ticker: df[df.index <= cutoff_ts] for ticker, df in sectors.items()}
        sigs = _compute_signals_fast(spx_slice, vix_slice, sector_slices)
        if not sigs:
            continue

        score = _grp_score(sigs)
        signal_counts.append(len(sigs))
        dow_idx = spx.index[i].weekday()

        # Opening gap — computed early for the gap-down abstain gate below.
        day_gap = _safe_float(opens, i) - _safe_float(close, i - 1) if i > 0 else 0.0

        bull_call = score >= 55
        bear_call = score <= 44

        if not bull_call and not bear_call:
            continue

        # Gap-down bear abstain: on large-gap-down days the model's bear calls are wrong
        # ~68% of the time (fade-the-gap pattern — markets reverse gap-down opens more
        # often than they confirm them).  Abstaining on these removes systematic
        # false-bear calls without losing meaningful edge; rare bull calls on gap-down
        # days (score ≥55) still go through since those reflect strong multi-group conviction.
        if day_gap < -GAP_THRESHOLD and bear_call:
            gap_down_abstained += 1
            continue

        nxt = float(close.iloc[i + 1])
        cur = float(close.iloc[i])
        up = nxt > cur + 5
        dn = nxt < cur - 5
        if not up and not dn:
            continue

        correct = (bull_call and up) or (bear_call and dn)
        vix_on_day = _safe_float(_squeeze(vix_slice, "Close"), default=20.0) if not vix_slice.empty else 20.0
        vix_key = "high" if vix_on_day > VIX_FEAR_THRESHOLD else ("low" if vix_on_day < VIX_CALM_THRESHOLD else "mid")
        gap_key = "up" if day_gap > GAP_THRESHOLD else ("down" if day_gap < -GAP_THRESHOLD else "flat")
        dow_name = _DOW_NAMES.get(dow_idx, "?")
        vix_buckets[vix_key]["total"] += 1
        vix_buckets[vix_key]["hits"] += int(correct)
        gap_buckets[gap_key]["total"] += 1
        gap_buckets[gap_key]["hits"] += int(correct)
        dow_buckets[dow_name]["total"] += 1
        dow_buckets[dow_name]["hits"] += int(correct)

        results.append(
            {
                "date": spx.index[i].strftime("%Y-%m-%d"),
                "score": score,
                "bull": bull_call,
                "bear": bear_call,
                "up": up,
                "correct": correct,
                "vix_regime": vix_key,
                "gap_regime": gap_key,
                "dow": dow_name,
                "signals_present": len(sigs),
            }
        )

    if not results:
        return {"ok": False, "error": "no directional days in evaluation window"}

    hits = sum(1 for row in results if row["correct"])
    total = len(results)
    accuracy = round(hits / total, 4)
    passes = accuracy >= MIN_ACCURACY_THRESHOLD

    vix_last = 20.0
    try:
        vix_last = float(_squeeze(vix, "Close").iloc[-1])
    except Exception:
        pass
    regime = "high_vix" if vix_last > VIX_FEAR_THRESHOLD else ("low_vix" if vix_last < VIX_CALM_THRESHOLD else "mid_vix")
    avg_signals = round(sum(signal_counts) / len(signal_counts), 1) if signal_counts else 0.0
    weekly = _run_weekly_validation(spx, vix, sectors)

    daily = {
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "threshold": MIN_ACCURACY_THRESHOLD,
        "eval_days": days,
        "avg_signals_present": avg_signals,
        "expected_core_signals": EXPECTED_CORE_SIGNAL_COUNT,
        "gap_down_abstained": gap_down_abstained,
        "regime_breakdown": {
            "vix": _attach_accuracy(vix_buckets),
            "gap": _attach_accuracy(gap_buckets),
            "dow": _attach_accuracy(dow_buckets),
        },
        "recent_results": results[-5:],
    }

    return {
        "ok": passes,
        "accuracy": accuracy,
        "hits": hits,
        "total": total,
        "threshold": MIN_ACCURACY_THRESHOLD,
        "eval_days": days,
        "vix_last": vix_last,
        "regime": regime,
        "history_period": period,
        "model_alignment": "equal_weight_static_core",
        "limitations": [
            "Daily and weekly outputs validate the 25 closed-bar Core SSR signals only.",
            "Session-open and live-overlay signals are intentionally excluded from this exporter.",
            "IMPORTANT: the live app applies (a) drift dampening (signals persistent-wrong for 10d "
            "are set to abstain) and (b) dynamic per-group weights derived from a rolling backtest. "
            "This exporter uses static equal-weight group averaging. The two scoring paths may "
            "diverge in trending regimes. Do not treat exporter accuracy numbers as a full "
            "validation of the live displayed Core SSR score.",
        ],
        "avg_signals_present": avg_signals,
        "expected_core_signals": EXPECTED_CORE_SIGNAL_COUNT,
        "daily": daily,
        "weekly": weekly,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_results": results[-5:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="spx-algo standalone backtest export")
    parser.add_argument("--days", type=int, default=60, help="Evaluation window in trading days")
    parser.add_argument("--out", default=None, help="Write JSON output to this file path")
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
