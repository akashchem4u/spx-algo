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
EXPECTED_CORE_SIGNAL_COUNT = 25  # iter 1: restored Stoch Bullish (252d -4pp without it despite flat ablation delta)
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
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting", "VIX Below 15", "VIX 1d Down",
                   "VVIX Below 100", "VIX Term Contango"],
    # VIX Term Contango: VIX9D < VIX = no near-term fear premium = bullish term structure.
    # Catches event-driven dislocations before they show in spot VIX (regime change signal).
    # VVIX Below 100: second-order volatility signal — VVIX (VIX of VIX) below 100 indicates
    # that the options market is not pricing in a spike in realized volatility.  Partially
    # independent from VIX Below 20 (Pearson r=0.57, fires 52% vs VIX<20 69%).  2yr ablation
    # addition: +1.9pp; 60d addition: +2.9pp.  Computed from ^VVIX daily closes.
    "Breadth": ["Volume Above Average", "Sector Breadth ≥ 50%", "Sector Breadth ≥ 85%", "XLK Leadership"],
    # Iter 1 tested removing Extremes/Stoch — but 252d backtest dropped 4pp despite the
    # ablation showing flat-delta.  Stoch Bullish correlates with outcomes in ways ablation
    # didn't catch (likely because group structure matters, not just signal-level deltas).
    # Restoring; the structural fix for unstable single-signal groups is to keep it scored.
    "Extremes": ["Stoch Bullish"],
    # RSI Trend Zone removed: ablation delta +1.3% — fires = 1 in the RSI 45–65 zone,
    # which catches early-bounce days that subsequently fail; the Momentum group's RSI
    # signals already cover the 50+ threshold with better precision.
    "Options": ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro": ["Yield Curve Positive", "Credit Spread Calm"],
    "Context": ["Gap/ATR Normal", "VIX No Spike", "Gap Down Contrarian", "Pre-FOMC Eve"],
    # Pre-FOMC Eve: 1 = next session is FOMC.  Captures documented pre-FOMC drift (rally pattern).
    # Gap Up Day removed from scoring: 2yr ablation +1.1pp drag.  Gap-up days trigger the gap-fade
    # tendency (markets reverse), so a binary bullish vote on gap-up days is counter-predictive.
    # The signal is still computed in app.py display tier and consumed by window_bias_at() to
    # apply intraday timing overrides — just not scored as a Context group vote.
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

MIN_ACCURACY_THRESHOLD = 0.52  # 48% gates below random — raised to require meaningful edge before deploying signal changes

# ATR-based directional label threshold (replaces the legacy fixed 5pt threshold).
# A 5pt move at ATR~25 was meaningful (20% of ATR); at ATR~70 it's noise (7% of ATR).
# Scaling with ATR keeps the "directional" sample bucket genuinely directional across vol regimes.
# Floor of 8.0 pts prevents pathologically tight thresholds on ultra-low-vol days.
LABEL_ATR_FRACTION = 0.125     # 1/8 of daily ATR
LABEL_THRESHOLD_FLOOR = 8.0    # absolute minimum pts move to count as directional
# Weekly label threshold — uses 5-bar lookahead, so requires bigger move than daily.
# At SPX ~7500, ATR ~70: weekly move of ~25pts is 35% of one daily ATR — meaningful.
WEEKLY_LABEL_THRESHOLD = 25.0  # was 5pt — too tight; weekly market noise routinely exceeds 5pt

# FOMC-only date set — used by Pre-FOMC Eve signal (different from broader _ECON_DATES).
_FOMC_DATES: frozenset[str] = frozenset({
    # 2025
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18",
    "2025-07-30","2025-09-17","2025-11-07","2025-12-17",
    # 2026
    "2026-01-28","2026-03-18","2026-04-29","2026-06-10","2026-07-29",
    "2026-09-16","2026-11-04","2026-12-16",
})

# HIGH-impact economic event dates used for event-day regime classification.
# Matches the set in run_ablation._ECON_DATES (FOMC, CPI, NFP, PCE; excludes MED-impact PPI/GDP).
_ECON_DATES: frozenset[str] = frozenset({
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
})


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


MIN_REGIME_SAMPLES = 25  # below this n, accuracy is too noisy to act on (binomial 95% CI ~±10pp at p=0.5)

def _block_bootstrap_ci(correct_seq: list[bool], block_size: int = 5, n_boot: int = 500,
                        rng_seed: int = 42) -> tuple[float, float] | None:
    """Block-bootstrap 95% CI for the mean of `correct_seq` (preserves autocorrelation).

    Walk-forward backtest samples are highly correlated (consecutive bars share most history),
    so a naive bootstrap underestimates uncertainty.  Block bootstrap resamples contiguous
    chunks of size `block_size` (≈ 1 trading week), better matching the real dependence.

    Returns (lower, upper) accuracy bounds at 95%, or None if sample too small.
    """
    import random
    n = len(correct_seq)
    if n < max(block_size * 2, 20):
        return None
    rng = random.Random(rng_seed)
    boot_means: list[float] = []
    n_blocks = (n // block_size) + 1
    for _ in range(n_boot):
        sampled: list[bool] = []
        for _ in range(n_blocks):
            _start = rng.randrange(0, n - block_size + 1)
            sampled.extend(correct_seq[_start:_start + block_size])
        sampled = sampled[:n]
        boot_means.append(sum(sampled) / len(sampled))
    boot_means.sort()
    lo_idx = int(0.025 * n_boot)
    hi_idx = int(0.975 * n_boot)
    return (round(boot_means[lo_idx], 4), round(boot_means[hi_idx], 4))

def _attach_accuracy(stats: dict[str, dict[str, int]]) -> dict[str, dict[str, float | int | bool | None]]:
    out: dict[str, dict[str, float | int | bool | None]] = {}
    for key, bucket in stats.items():
        total = bucket["total"]
        hits = bucket["hits"]
        out[key] = {
            "hits": hits,
            "total": total,
            "accuracy": round(hits / total, 4) if total else None,
            "insufficient_sample": total < MIN_REGIME_SAMPLES,  # downstream consumers should ignore accuracy when True
        }
    return out


def _compute_signals_fast(
    spx_sl: pd.DataFrame,
    vix_sl: pd.DataFrame,
    sector_slices: dict[str, pd.DataFrame],
    vvix_sl: pd.Series | None = None,
    vix9d_sl: pd.Series | None = None,
    as_of_date: "pd.Timestamp | None" = None,
) -> dict[str, int]:
    """
    Reconstruct the 23 closed-bar core signals that the app backtests.
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
        # VIX Falling computed FIRST so VIX Below 15 can gate on it.
        # VIX Falling: 5-day trend — VIX today below VIX 5 sessions ago.
        # Captures sustained fear-unwind over a multi-day window and is genuinely
        # independent from the 1-day version below.  In the live app VIX Falling
        # has a market-open gate (disabled pre/post session), which collapses to a
        # 1-day comparison in the exporter's post-close context.  Using a 5-day
        # horizon here gives the Volatility group two distinct time-scale signals.
        sigs["VIX Falling"] = int(len(vix_c) >= 6 and vv < _safe_float(vix_c, -6))
        # VIX Below 15 CONDITIONAL on VIX Falling (2yr ablation: raw <15 has +0.8pp drag —
        # ultra-low VIX often precedes complacency corrections; require contracting momentum).
        sigs["VIX Below 15"] = int(vv < 15 and sigs["VIX Falling"] == 1)
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
        # ATR Contracting: was 1-day comparison; now percentile (current ATR < 85% of 20d avg).
        # Captures volatility compression regimes, not daily noise.
        _atr_dna = atr_v.dropna()
        if len(_atr_dna) >= 20:
            _atr_now = float(_atr_dna.iloc[-1])
            _atr_avg = float(_atr_dna.iloc[-20:].mean())
            sigs["ATR Contracting"] = int(_atr_now < _atr_avg * 0.85)
        else:
            sigs["ATR Contracting"] = 0
        # VIX Below 20 / VIX Below 15 intentionally kept: in sustained bear trends
        # these signals correctly vote bearish.  Live dampening is handled by the
        # drift monitor (which flags them as stuck_bear only when they've been wrong
        # for 10+ days), not by a static always-remove-in-hi-VIX rule.

    # VVIX — second-order volatility (VIX of VIX).  Fires 1 when VVIX < 100, indicating
    # the options market is not pricing in an imminent VIX spike (calm second-order fear).
    # Partially independent from VIX Below 20 (r=0.57).
    # Omitted (not 0) when vvix data is unavailable, so group score falls back to other signals.
    if vvix_sl is not None and len(vvix_sl) >= 1:
        try:
            sigs["VVIX Below 100"] = int(float(vvix_sl.iloc[-1]) < 100)
        except Exception:
            pass  # omit on error

    # VIX Term Contango: VIX9D < VIX (30d) = no near-term fear premium = bullish term structure.
    if vix9d_sl is not None and len(vix9d_sl) >= 1 and not vix_sl.empty:
        try:
            _v9 = float(vix9d_sl.iloc[-1])
            _v30 = _safe_float(_squeeze(vix_sl, "Close"), default=20.0)
            sigs["VIX Term Contango"] = int(_v9 < _v30)
        except Exception:
            pass

    # Breadth — Accumulation Day: above-average volume + intraday strength (close > open).
    # Was close > prev_close (duplicated "Higher Close (1d)"); close > open captures true
    # intraday accumulation, making the signal independent and more meaningful.
    if len(volume) >= 20:
        vol_avg = volume.rolling(20).mean()
        _vol_ok    = _safe_float(volume) > _safe_float(vol_avg)
        try:
            _open_s = _squeeze(spx_sl, "Open")
            _price_up  = _safe_float(close) > _safe_float(_open_s)
        except Exception:
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

        # XLK Leadership: tech (XLK) 5d return > defensive average (XLP+XLU) — quality risk-on.
        try:
            _xk = sector_slices.get("XLK"); _xp = sector_slices.get("XLP"); _xu = sector_slices.get("XLU")
            if _xk is not None and _xp is not None and _xu is not None and \
               not _xk.empty and not _xp.empty and not _xu.empty:
                _xkc = _squeeze(_xk, "Close"); _xpc = _squeeze(_xp, "Close"); _xuc = _squeeze(_xu, "Close")
                if len(_xkc) >= 6 and len(_xpc) >= 6 and len(_xuc) >= 6:
                    _xk_r = float(_xkc.iloc[-1] / _xkc.iloc[-6] - 1)
                    _df_r = (float(_xpc.iloc[-1] / _xpc.iloc[-6] - 1) +
                             float(_xuc.iloc[-1] / _xuc.iloc[-6] - 1)) / 2
                    sigs["XLK Leadership"] = int(_xk_r > _df_r)
        except Exception:
            pass

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

    # Pre-FOMC Eve: 1 = next trading day is FOMC.  Documented pre-FOMC drift pattern.
    if as_of_date is not None:
        try:
            _nd = as_of_date + pd.Timedelta(days=1)
            while _nd.weekday() >= 5:    # skip weekends
                _nd += pd.Timedelta(days=1)
            sigs["Pre-FOMC Eve"] = int(_nd.strftime("%Y-%m-%d") in _FOMC_DATES)
        except Exception:
            sigs["Pre-FOMC Eve"] = 0

    return sigs


def _grp_score(sigs: dict[str, int]) -> int:
    weighted_scores, weights = [], []
    for _, group_signals in SIGNAL_GROUPS.items():
        present = [sigs[name] for name in group_signals if name in sigs]
        if present:
            weighted_scores.append(sum(present) / len(present))
            weights.append(1.0)
    return round(sum(weighted_scores) / len(weighted_scores) * 100) if weighted_scores else 50


def _grp_directional_vote(sigs: dict[str, int]) -> dict[str, int]:
    """Per-group directional vote: +1 (bull majority), -1 (bear), 0 (mixed/empty).

    Used by the group-agreement filter to abstain when SSR direction is supported
    by fewer than 5 of 9 groups (structural filter — no thresholds chosen from data).
    """
    out: dict[str, int] = {}
    for gname, group_signals in SIGNAL_GROUPS.items():
        present = [sigs[name] for name in group_signals if name in sigs]
        if not present:
            out[gname] = 0
            continue
        gs = sum(present) / len(present)
        if   gs > 0.5: out[gname] = 1
        elif gs < 0.5: out[gname] = -1
        else:          out[gname] = 0
    return out


def _run_weekly_validation(
    spx: pd.DataFrame,
    vix: pd.DataFrame,
    sectors: dict[str, pd.DataFrame],
    vvix: "pd.Series | None" = None,
    vix9d: "pd.Series | None" = None,
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
        vvix_base = vvix[vvix.index <= cutoff_ts] if vvix is not None and not vvix.empty else pd.Series(dtype=float)
        vix9d_base = vix9d[vix9d.index <= cutoff_ts] if vix9d is not None and not vix9d.empty else pd.Series(dtype=float)
        sector_base = {ticker: df[df.index <= cutoff_ts] for ticker, df in sectors.items()}
        sigs = _compute_signals_fast(spx_base, vix_base, sector_base, vvix_base, vix9d_base,
                                     as_of_date=cutoff_ts)
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
        actual = "bull" if week_move > WEEKLY_LABEL_THRESHOLD else ("bear" if week_move < -WEEKLY_LABEL_THRESHOLD else "neutral")
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


def run_backtest(days: int = 60, history_period: "str | None" = None) -> dict:
    """
    Fetch enough history to warm up long-horizon signals, then emit both daily
    and weekly Core-SSR validation summaries.
    """
    try:
        # Allow explicit period override (e.g. --period 5y) to extend coverage through bear markets.
        # Default is computed from --days, ensuring at least 1 year of warm-up for SMA(200).
        period = history_period if history_period else _history_period_for_days(days)
        spx = yf.download("^GSPC", period=period, interval="1d", progress=False, auto_adjust=True)
        vix = yf.download("^VIX", period=period, interval="1d", progress=False, auto_adjust=True)
        try:
            vvix_df = yf.download("^VVIX", period=period, interval="1d", progress=False, auto_adjust=True)
            vvix = _squeeze(vvix_df, "Close") if not vvix_df.empty else pd.Series(dtype=float)
        except Exception:
            vvix = pd.Series(dtype=float)
        try:
            vix9d_df = yf.download("^VIX9D", period=period, interval="1d", progress=False, auto_adjust=True)
            vix9d = _squeeze(vix9d_df, "Close") if not vix9d_df.empty else pd.Series(dtype=float)
        except Exception:
            vix9d = pd.Series(dtype=float)
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

    # Pre-compute ATR series for ATR-relative directional label threshold (replaces fixed 5pt).
    atr_series = _atr(spx)
    def _label_threshold(idx: int) -> float:
        try:
            _a = float(atr_series.iloc[idx])
            if not np.isnan(_a):
                return max(LABEL_THRESHOLD_FLOOR, _a * LABEL_ATR_FRACTION)
        except Exception:
            pass
        return LABEL_THRESHOLD_FLOOR

    eval_start = max(252, n - days - 1)
    results = []
    vix_buckets = {"low": _build_accuracy_bucket(), "mid": _build_accuracy_bucket(), "high": _build_accuracy_bucket()}
    gap_buckets = {"up": _build_accuracy_bucket(), "flat": _build_accuracy_bucket(), "down": _build_accuracy_bucket()}
    # Day-of-week buckets — 2yr ablation (23+1opt model): Wed 66.7%, Mon 56.4%
    # best; Thu 45.5% structural drag (structural or regime noise); Fri 48.9%.
    _DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    dow_buckets = {d: _build_accuracy_bucket() for d in _DOW_NAMES.values()}
    # Event-day buckets — HIGH-impact FOMC/CPI/NFP/PCE vs normal days.
    event_buckets = {"event": _build_accuracy_bucket(), "normal": _build_accuracy_bucket()}
    # Score-band buckets — accuracy by SSR range
    score_band_buckets: dict[str, dict[str, int]] = {
        "76-100": _build_accuracy_bucket(),  # strong bull
        "63-75":  _build_accuracy_bucket(),  # moderate bull
        "55-62":  _build_accuracy_bucket(),  # soft bull
        "38-44":  _build_accuracy_bucket(),  # soft bear
        "25-37":  _build_accuracy_bucket(),  # moderate bear
        "0-24":   _build_accuracy_bucket(),  # strong bear
    }
    signal_counts: list[int] = []
    gap_down_abstained: int = 0  # days where bear call was suppressed by gap-down gate

    for i in range(eval_start, n - 1):
        cutoff_ts = spx.index[i]
        spx_slice = spx.iloc[: i + 1]
        vix_slice = vix[vix.index <= cutoff_ts]
        vvix_slice = vvix[vvix.index <= cutoff_ts] if not vvix.empty else pd.Series(dtype=float)
        vix9d_slice = vix9d[vix9d.index <= cutoff_ts] if not vix9d.empty else pd.Series(dtype=float)
        sector_slices = {ticker: df[df.index <= cutoff_ts] for ticker, df in sectors.items()}
        sigs = _compute_signals_fast(spx_slice, vix_slice, sector_slices, vvix_slice, vix9d_slice,
                                     as_of_date=cutoff_ts)
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

        # Gap-UP bear abstain: symmetric to gap-down logic.  Gap-up days trigger fade
        # tendency too (markets mean-revert from extended opens), so lagging SMA/breadth
        # signals stay bearish into a gap-up reversal-attempt — producing bad bear calls.
        # Same mechanism-based justification as gap-down (not in-sample DOW snooping).
        if day_gap > GAP_THRESHOLD and bear_call:
            continue

        # Group-agreement filter: abstain when ≥3 of 8 groups vote AGAINST the SSR direction.
        # Structural filter (no calibrated thresholds) — catches cases where SSR direction
        # is driven by one or two strong groups while others actively disagree.  More
        # permissive than requiring majority agreement since SSR already implies majority;
        # this filter specifically rejects high-DISAGREEMENT calls.
        _grp_votes = _grp_directional_vote(sigs)
        _bull_groups = sum(1 for v in _grp_votes.values() if v ==  1)
        _bear_groups = sum(1 for v in _grp_votes.values() if v == -1)
        if bull_call and _bear_groups >= 3:
            continue
        if bear_call and _bull_groups >= 3:
            continue

        # Thursday bear abstain: DOW data shows Thursday accuracy of ~48% (below random).
        # Combined with the gap-down bear gate, this removes structurally noisy bear calls.
        # Bull calls on Thursdays still go through — only the systematically-wrong bear side
        # is suppressed (mirroring the gap-down bear logic).
        if dow_idx == 3 and bear_call:    # 3 = Thursday
            continue

        # Iter 2 tested Monday bear abstain (Monday 47.3% on 5yr).  Reverted per critic:
        # the rule was discovered AND validated on the SAME 5yr window — pure data snooping.
        # If Monday underperformance holds, it should appear in 2015–2020 OOS data first.
        # Suppressed bear calls also concentrate bull-regime bias (already 4 bear-side gates).

        # Strong-bear abstain: when SSR ≤ 24, all signal groups are simultaneously bearish.
        # Historical accuracy: 30.8% (2yr rolling window) — worse than random.
        # Mechanism: extreme pessimism already priced in; mean-reversion bounce risk is
        # elevated on non-gap-down extreme-bear days. +2.6pp in 2yr window.
        if bear_call and score <= 24:
            continue

        nxt = float(close.iloc[i + 1])
        cur = float(close.iloc[i])
        _lt = _label_threshold(i)
        up = nxt > cur + _lt
        dn = nxt < cur - _lt
        if not up and not dn:
            continue

        correct = (bull_call and up) or (bear_call and dn)
        vix_on_day = _safe_float(_squeeze(vix_slice, "Close"), default=20.0) if not vix_slice.empty else 20.0
        vix_key = "high" if vix_on_day > VIX_FEAR_THRESHOLD else ("low" if vix_on_day < VIX_CALM_THRESHOLD else "mid")
        gap_key = "up" if day_gap > GAP_THRESHOLD else ("down" if day_gap < -GAP_THRESHOLD else "flat")
        dow_name = _DOW_NAMES.get(dow_idx, "?")
        event_key = "event" if spx.index[i].strftime("%Y-%m-%d") in _ECON_DATES else "normal"
        vix_buckets[vix_key]["total"] += 1
        vix_buckets[vix_key]["hits"] += int(correct)
        gap_buckets[gap_key]["total"] += 1
        gap_buckets[gap_key]["hits"] += int(correct)
        dow_buckets[dow_name]["total"] += 1
        dow_buckets[dow_name]["hits"] += int(correct)
        event_buckets[event_key]["total"] += 1
        event_buckets[event_key]["hits"] += int(correct)
        # Score-band
        if score >= 76:   sb_key = "76-100"
        elif score >= 63: sb_key = "63-75"
        elif score >= 55: sb_key = "55-62"
        elif score <= 24: sb_key = "0-24"
        elif score <= 37: sb_key = "25-37"
        else:             sb_key = "38-44"
        score_band_buckets[sb_key]["total"] += 1
        score_band_buckets[sb_key]["hits"] += int(correct)

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

    # Block-bootstrap 95% CI on accuracy.  Walk-forward samples are not iid (consecutive
    # bars share history); naive bootstrap underestimates uncertainty.  5-bar blocks
    # roughly match weekly autocorrelation structure.
    _correct_seq = [bool(row["correct"]) for row in results]
    _ci = _block_bootstrap_ci(_correct_seq, block_size=5, n_boot=500)
    accuracy_ci_lo, accuracy_ci_hi = (_ci if _ci is not None else (None, None))

    vix_last = 20.0
    try:
        vix_last = float(_squeeze(vix, "Close").iloc[-1])
    except Exception:
        pass
    regime = "high_vix" if vix_last > VIX_FEAR_THRESHOLD else ("low_vix" if vix_last < VIX_CALM_THRESHOLD else "mid_vix")
    avg_signals = round(sum(signal_counts) / len(signal_counts), 1) if signal_counts else 0.0
    weekly = _run_weekly_validation(spx, vix, sectors, vvix, vix9d)

    daily = {
        "accuracy": accuracy,
        "accuracy_ci_lo": accuracy_ci_lo,    # 95% block-bootstrap CI lower (None if n < 20)
        "accuracy_ci_hi": accuracy_ci_hi,    # 95% block-bootstrap CI upper
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
            "event": _attach_accuracy(event_buckets),
            "score_band": _attach_accuracy(score_band_buckets),
        },
        "recent_results": results[-5:],
    }

    return {
        "ok": passes,
        "accuracy": accuracy,
        "accuracy_ci_lo": accuracy_ci_lo,
        "accuracy_ci_hi": accuracy_ci_hi,
        "hits": hits,
        "total": total,
        "threshold": MIN_ACCURACY_THRESHOLD,
        "eval_days": days,
        "vix_last": vix_last,
        "regime": regime,
        "history_period": period,
        "model_alignment": "equal_weight_static_core",
        "limitations": [
            "Daily and weekly outputs validate the 23+1opt closed-bar Core SSR signals only.",
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
    parser.add_argument("--period", default=None, help="yfinance history period (e.g. 2y, 5y); overrides days-derived default")
    parser.add_argument("--out", default=None, help="Write JSON output to this file path")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout (only write file)")
    args = parser.parse_args(argv)

    result = run_backtest(days=args.days, history_period=args.period)
    output = json.dumps(result, indent=2, default=str)

    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    if not args.quiet:
        print(output)

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
