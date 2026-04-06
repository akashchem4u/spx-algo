#!/usr/bin/env python3
"""
Probe: test ^CPC (CBOE equity put/call ratio) as a daily backtestable signal.

Two signal candidates:
  PCR-A  "Put/Call High Fear"  — PCR close > 1.0 (fear premium = contrarian bull)
  PCR-B  "Put/Call Abating"    — PCR today > 0.85 AND today < yesterday's close
                                  (fear winding down = bull context)

Methodology:
  - Drop-in replace for the Options group (currently 0% coverage) in the same
    walk-forward window used by run_ablation.py  (2y data, bar-by-bar slice).
  - Measure baseline (no PCR) vs +PCR-A vs +PCR-B vs +PCR-A+PCR-B.
  - Report directional-call accuracy and group coverage change.

Safe to run standalone. Does not modify any production file.
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

# ── mirror the production constants ──────────────────────────────────────────
GAP_THRESHOLD = 25.0
VIX_CALM_THRESHOLD = 18.0
VIX_FEAR_THRESHOLD = 25.0

SIGNAL_GROUPS = {
    "Trend":      ["Above 20 SMA", "Above 50 SMA", "Above 200 SMA"],
    "Momentum":   ["Higher Close (1d)", "Higher Close (5d)", "MACD Bullish", "RSI Strong Trend"],
    "Volatility": ["VIX Below 20", "VIX Falling", "ATR Contracting", "VIX Below 15", "VIX 1d Down",
                   "VVIX Below 100"],
    "Breadth":    ["Volume Above Average", "Sector Breadth ≥ 50%", "Sector Breadth ≥ 85%"],
    "Extremes":   ["Stoch Bullish"],
    "Options":    ["Put/Call Fear Premium", "Put/Call Fear Abating"],
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],
    "Context":    ["Gap/ATR Normal", "VIX No Spike", "Gap Up Day", "Gap Down Contrarian"],
    "Position":   ["52w Range Upper Half", "Above Prior Day High", "Above Pivot", "Above 5d High"],
}

SECTOR_TICKERS = ["XLF", "XLK", "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLRE", "XLU"]

# ── helpers (copied from backtest_export.py) ─────────────────────────────────
def _squeeze(df: pd.DataFrame, col: str) -> pd.Series:
    s = df[col] if col in df.columns else df.iloc[:, 0]
    return s.squeeze() if isinstance(s, pd.DataFrame) else s


def _safe_float(s, idx=-1, default=0.0):
    try:
        return float(s.iloc[idx])
    except Exception:
        return default


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h = _squeeze(df, "High")
    l = _squeeze(df, "Low")
    c = _squeeze(df, "Close")
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _grp_score(sigs: dict[str, int], groups: dict | None = None) -> int:
    g = groups or SIGNAL_GROUPS
    weighted_scores, weights = [], []
    for _, group_signals in g.items():
        present = [sigs[name] for name in group_signals if name in sigs]
        if present:
            weighted_scores.append(sum(present) / len(present))
            weights.append(1.0)
    return round(sum(weighted_scores) / len(weighted_scores) * 100) if weighted_scores else 50


def _compute_signals(spx_sl, vix_sl, sector_slices, vvix_sl=None, pcr_sl=None) -> dict[str, int]:
    """Stripped-down signal computation mirroring backtest_export._compute_signals_fast()."""
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs

    close = _squeeze(spx_sl, "Close")
    high  = _squeeze(spx_sl, "High")
    low   = _squeeze(spx_sl, "Low")
    volume = _squeeze(spx_sl, "Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close) < 20:
        return sigs

    c = _safe_float(close)
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi_v  = _rsi(close).dropna()
    macd_line, macd_signal = _macd(close)
    atr_v  = _atr(spx_sl)

    stoch_range = high.rolling(14).max() - low.rolling(14).min()
    stoch_safe  = stoch_range.where(stoch_range > 0)
    stoch_k     = (100 * (close - low.rolling(14).min()) / stoch_safe).fillna(50).clip(0, 100)
    stoch_d     = stoch_k.rolling(3).mean().fillna(50)

    # Trend
    sigs["Above 20 SMA"]  = int(c > _safe_float(sma20))
    sigs["Above 50 SMA"]  = int(c > _safe_float(sma50))  if len(close) >= 50  else 0
    sigs["Above 200 SMA"] = int(c > _safe_float(sma200)) if len(close) >= 200 else 0

    # Momentum
    rsi_last = _safe_float(rsi_v, default=50.0)
    sigs["Higher Close (1d)"] = int(len(close) >= 2 and c > _safe_float(close, -2))
    sigs["Higher Close (5d)"] = int(len(close) >= 6 and c > _safe_float(close, -6))
    sigs["MACD Bullish"]      = int(_safe_float(macd_line) > _safe_float(macd_signal))
    sigs["RSI Strong Trend"]  = int(60 <= rsi_last <= 75)

    # Volatility
    if not vix_sl.empty:
        vix_c = _squeeze(vix_sl, "Close")
        vv = _safe_float(vix_c, default=20.0)
        sigs["VIX Below 20"]    = int(vv < 20)
        sigs["VIX Below 15"]    = int(vv < 15)
        sigs["VIX Falling"]     = int(len(vix_c) >= 6 and vv < _safe_float(vix_c, -6))
        sigs["VIX 1d Down"]     = int(len(vix_c) >= 2 and vv < _safe_float(vix_c, -2))
        if len(vix_c) >= 4:
            v3d = _safe_float(vix_c, -4, default=vv)
            sigs["VIX No Spike"] = int((vv - v3d) / max(v3d, 1) <= 0.08)
        else:
            sigs["VIX No Spike"] = 1
        sigs["ATR Contracting"] = int(len(atr_v.dropna()) >= 20 and _safe_float(atr_v) < _safe_float(atr_v, -5))

    if vvix_sl is not None and len(vvix_sl) >= 1:
        try:
            sigs["VVIX Below 100"] = int(float(vvix_sl.iloc[-1]) < 100)
        except Exception:
            pass

    # Breadth
    if len(volume) >= 20:
        vol_avg  = volume.rolling(20).mean()
        _vol_ok  = _safe_float(volume) > _safe_float(vol_avg)
        _price_up = len(close) >= 2 and _safe_float(close) > _safe_float(close, -2)
        sigs["Volume Above Average"] = int(_vol_ok and _price_up)

    total_sectors = len(sector_slices)
    if total_sectors:
        above = 0
        for df in sector_slices.values():
            if df.empty:
                continue
            try:
                sc = _squeeze(df, "Close")
                if len(sc) < 50:
                    continue
                above += int(_safe_float(sc) > _safe_float(sc.rolling(50).mean()))
            except Exception:
                continue
        sigs["Sector Breadth ≥ 50%"] = int((above / total_sectors) >= 0.5)
        sigs["Sector Breadth ≥ 85%"] = int((above / total_sectors) >= 0.85)

    # Context / Gap
    if "Open" in spx_sl.columns:
        open_s = _squeeze(spx_sl, "Open")
        if len(open_s) >= 2 and len(close) >= 2:
            _gap_pts = _safe_float(open_s) - _safe_float(close, -2)
            sigs["Gap Up Day"] = int(_gap_pts > GAP_THRESHOLD)
            if _gap_pts < -GAP_THRESHOLD:
                sigs["Gap Down Contrarian"] = 1

    # Extremes
    sigs["Stoch Bullish"] = int(_safe_float(stoch_k) > _safe_float(stoch_d))

    # Position
    if len(close) >= 252:
        high_52  = _safe_float(close.rolling(252).max())
        low_52   = _safe_float(close.rolling(252).min())
        range_52 = max(high_52 - low_52, 1.0)
        range_pos = (c - low_52) / range_52
        sigs["52w Range Upper Half"] = int(range_pos > 0.5)
    elif len(close) >= 20:
        high_20  = _safe_float(high.rolling(20).max())
        low_20   = _safe_float(low.rolling(20).min())
        range_20 = max(high_20 - low_20, 1.0)
        range_pos = (c - low_20) / range_20
        sigs["52w Range Upper Half"] = int(range_pos > 0.5)

    if len(close) >= 2:
        sigs["Above Prior Day High"] = int(c > _safe_float(high, -2))
        pivot = (_safe_float(high, -2) + _safe_float(low, -2) + _safe_float(close, -2)) / 3.0
        sigs["Above Pivot"] = int(c > pivot)
    if len(high) >= 6:
        sigs["Above 5d High"] = int(c > float(high.iloc[-6:-1].max()))

    # ── PCR signal (probe addition) ──────────────────────────────────────────
    # Only added to sigs if pcr_sl is provided and has data.
    # Omit-not-zero pattern: absent key → group falls back to remaining signals.
    if pcr_sl is not None and len(pcr_sl) >= 2:
        try:
            pcr_today = float(pcr_sl.iloc[-1])
            pcr_prev  = float(pcr_sl.iloc[-2])
            # PCR-A: elevated fear premium = contrarian bull signal
            sigs["Put/Call Fear Premium"] = int(pcr_today > 1.0)
            # PCR-B: fear winding down from elevated level = bull context
            sigs["Put/Call Fear Abating"] = int(pcr_today > 0.85 and pcr_today < pcr_prev)
        except Exception:
            pass  # omit on error

    return sigs


def run_ablation_probe(pcr_close: pd.Series | None) -> None:
    """Walk-forward 2yr backtest comparing baseline vs +PCR variants."""
    print("[probe] Fetching 2y data for walk-forward test …")
    spx     = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
    vix     = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix_df = yf.download("^VVIX", period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix    = _squeeze(vvix_df, "Close") if not vvix_df.empty else pd.Series(dtype=float)

    sectors = {}
    for ticker in SECTOR_TICKERS:
        try:
            sectors[ticker] = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[ticker] = pd.DataFrame()

    close = _squeeze(spx, "Close")
    opens = _squeeze(spx, "Open")
    n     = len(spx)
    eval_start = max(252, n - 505)  # ~2yr evaluation window

    results_base, results_pcr = [], []

    for i in range(eval_start, n - 1):
        cutoff_ts     = spx.index[i]
        spx_slice     = spx.iloc[:i + 1]
        vix_slice     = vix[vix.index <= cutoff_ts]
        vvix_sl       = vvix[vvix.index <= cutoff_ts] if not vvix.empty else pd.Series(dtype=float)
        sector_slices = {t: df[df.index <= cutoff_ts] for t, df in sectors.items()}

        # PCR slice up to cutoff
        pcr_sl = None
        if pcr_close is not None and not pcr_close.empty:
            pcr_sl = pcr_close[pcr_close.index <= cutoff_ts]
            pcr_sl = pcr_sl if not pcr_sl.empty else None

        # Baseline: no PCR
        sigs_base = _compute_signals(spx_slice, vix_slice, sector_slices, vvix_sl, pcr_sl=None)
        # +PCR: with PCR signals
        sigs_pcr  = _compute_signals(spx_slice, vix_slice, sector_slices, vvix_sl, pcr_sl=pcr_sl)

        if not sigs_base:
            continue

        day_gap = _safe_float(opens, i) - _safe_float(close, i - 1) if i > 0 else 0.0
        nxt = float(close.iloc[i + 1])
        cur = float(close.iloc[i])
        up  = nxt > cur + 5
        dn  = nxt < cur - 5
        if not up and not dn:
            continue

        for sigs, bucket in [(sigs_base, results_base), (sigs_pcr, results_pcr)]:
            score     = _grp_score(sigs)
            bull_call = score >= 55
            bear_call = score <= 44
            if not bull_call and not bear_call:
                continue
            if day_gap < -GAP_THRESHOLD and bear_call:
                continue  # gap-down abstain
            correct = (bull_call and up) or (bear_call and dn)
            bucket.append({"correct": correct, "score": score,
                           "pcr_a": sigs.get("Put/Call Fear Premium"),
                           "pcr_b": sigs.get("Put/Call Fear Abating")})

    # ── stats ─────────────────────────────────────────────────────────────────
    def pct(rows):
        if not rows:
            return 0.0, 0, 0
        hits  = sum(r["correct"] for r in rows)
        total = len(rows)
        return round(hits / total * 100, 1), hits, total

    base_acc, base_hits, base_n = pct(results_base)
    pcr_acc,  pcr_hits,  pcr_n  = pct(results_pcr)

    print(f"\n{'─'*55}")
    print(f"  Baseline (no PCR):     {base_hits}/{base_n} = {base_acc}%")
    print(f"  +PCR signals:          {pcr_hits}/{pcr_n}  = {pcr_acc}%")
    delta = pcr_acc - base_acc
    print(f"  Delta vs baseline:     {delta:+.1f}pp  ({'improves' if delta > 0 else 'hurts' if delta < 0 else 'neutral'})")
    print(f"{'─'*55}")

    # PCR coverage stats (how often each signal fires)
    if results_pcr:
        a_fire = sum(1 for r in results_pcr if r.get("pcr_a") == 1)
        b_fire = sum(1 for r in results_pcr if r.get("pcr_b") == 1)
        print(f"  PCR-A fires (out of {pcr_n} calls): {a_fire}  ({round(a_fire/pcr_n*100,1)}%)")
        print(f"  PCR-B fires (out of {pcr_n} calls): {b_fire}  ({round(b_fire/pcr_n*100,1)}%)")


if __name__ == "__main__":
    print("[probe] Checking ^CPC data availability …")
    try:
        cpc_df = yf.download("^CPC", period="2y", interval="1d", progress=False, auto_adjust=True)
        if cpc_df.empty:
            print("[probe] ^CPC returned empty DataFrame — signal NOT available historically")
            pcr_close = None
        else:
            pcr_close = _squeeze(cpc_df, "Close").dropna()
            print(f"[probe] ^CPC available: {len(pcr_close)} daily bars "
                  f"({pcr_close.index[0].date()} → {pcr_close.index[-1].date()})")
            print(f"[probe] PCR range: min={pcr_close.min():.3f}  max={pcr_close.max():.3f}  "
                  f"mean={pcr_close.mean():.3f}  >1.0: {(pcr_close > 1.0).sum()} bars")
    except Exception as e:
        print(f"[probe] ^CPC fetch error: {e}")
        pcr_close = None

    run_ablation_probe(pcr_close)
    print("\n[probe] done.")
