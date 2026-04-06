#!/usr/bin/env python3
"""
Probe batch 2:

  SKEW-01  "SKEW Below 130" — CBOE SKEW Index < 130 = market not pricing heavy tail risk
                               (second-order options sentiment, partially independent of VVIX)

  ADUP-01  "Sectors Advancing" — >50% of 11 sectors close HIGHER on the day
                               (same-day A/D proxy; differs from Sector Breadth ≥ 50% which
                               measures sectors above 50 SMA — entirely different signal)

  ADUP-02  "Sectors Majority Up" — >60% (7+/11) sectors close higher on the day

Tests each candidate in isolation against the 2yr walk-forward baseline.
Does not modify any production file.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yfinance as yf

GAP_THRESHOLD = 25.0

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


def _squeeze(df, col):
    s = df[col] if col in df.columns else df.iloc[:, 0]
    return s.squeeze() if isinstance(s, pd.DataFrame) else s

def _sf(s, idx=-1, default=0.0):
    try:
        return float(s.iloc[idx])
    except Exception:
        return default

def _rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def _macd(close):
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    line = e12 - e26
    return line, line.ewm(span=9, adjust=False).mean()

def _atr(df, period=14):
    h = _squeeze(df, "High"); l = _squeeze(df, "Low"); c = _squeeze(df, "Close")
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _grp_score(sigs, extra_group_name=None, extra_signal_name=None):
    """Group score with optional extra signal injected into Breadth group."""
    groups = dict(SIGNAL_GROUPS)  # shallow copy
    if extra_group_name and extra_signal_name and extra_signal_name in sigs:
        # Inject into specified group
        groups[extra_group_name] = list(groups.get(extra_group_name, [])) + [extra_signal_name]
    weighted_scores = []
    for _, gsigs in groups.items():
        present = [sigs[name] for name in gsigs if name in sigs]
        if present:
            weighted_scores.append(sum(present) / len(present))
    return round(sum(weighted_scores) / len(weighted_scores) * 100) if weighted_scores else 50

def _compute_signals(spx_sl, vix_sl, sector_slices, vvix_sl=None, skew_sl=None) -> dict[str, int]:
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs
    close  = _squeeze(spx_sl, "Close")
    high   = _squeeze(spx_sl, "High")
    low    = _squeeze(spx_sl, "Low")
    volume = _squeeze(spx_sl, "Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close) < 20:
        return sigs

    c = _sf(close)
    sma20 = close.rolling(20).mean(); sma50 = close.rolling(50).mean(); sma200 = close.rolling(200).mean()
    rsi_v = _rsi(close).dropna(); macd_line, macd_sig = _macd(close); atr_v = _atr(spx_sl)
    stoch_range = high.rolling(14).max() - low.rolling(14).min()
    stoch_k = (100 * (close - low.rolling(14).min()) / stoch_range.where(stoch_range > 0)).fillna(50).clip(0, 100)
    stoch_d = stoch_k.rolling(3).mean().fillna(50)

    sigs["Above 20 SMA"]  = int(c > _sf(sma20))
    sigs["Above 50 SMA"]  = int(c > _sf(sma50))  if len(close) >= 50  else 0
    sigs["Above 200 SMA"] = int(c > _sf(sma200)) if len(close) >= 200 else 0

    rsi_last = _sf(rsi_v, default=50.0)
    sigs["Higher Close (1d)"] = int(len(close) >= 2 and c > _sf(close, -2))
    sigs["Higher Close (5d)"] = int(len(close) >= 6 and c > _sf(close, -6))
    sigs["MACD Bullish"]      = int(_sf(macd_line) > _sf(macd_sig))
    sigs["RSI Strong Trend"]  = int(60 <= rsi_last <= 75)

    if not vix_sl.empty:
        vix_c = _squeeze(vix_sl, "Close"); vv = _sf(vix_c, default=20.0)
        sigs["VIX Below 20"]    = int(vv < 20)
        sigs["VIX Below 15"]    = int(vv < 15)
        sigs["VIX Falling"]     = int(len(vix_c) >= 6 and vv < _sf(vix_c, -6))
        sigs["VIX 1d Down"]     = int(len(vix_c) >= 2 and vv < _sf(vix_c, -2))
        if len(vix_c) >= 4:
            v3d = _sf(vix_c, -4, default=vv)
            sigs["VIX No Spike"] = int((vv - v3d) / max(v3d, 1) <= 0.08)
        else:
            sigs["VIX No Spike"] = 1
        sigs["ATR Contracting"] = int(len(atr_v.dropna()) >= 20 and _sf(atr_v) < _sf(atr_v, -5))

    if vvix_sl is not None and len(vvix_sl) >= 1:
        try:
            sigs["VVIX Below 100"] = int(float(vvix_sl.iloc[-1]) < 100)
        except Exception:
            pass

    # SKEW signal (probe) — omit-not-zero
    if skew_sl is not None and len(skew_sl) >= 1:
        try:
            sigs["SKEW Below 130"] = int(float(skew_sl.iloc[-1]) < 130)
        except Exception:
            pass

    if len(volume) >= 20:
        vol_avg = volume.rolling(20).mean()
        sigs["Volume Above Average"] = int(_sf(volume) > _sf(vol_avg) and (len(close) >= 2 and _sf(close) > _sf(close, -2)))

    total_sectors = len(sector_slices)
    if total_sectors:
        above_50sma = 0
        adv_day = 0
        for df in sector_slices.values():
            if df.empty:
                continue
            try:
                sc = _squeeze(df, "Close")
                if len(sc) < 50:
                    continue
                above_50sma += int(_sf(sc) > _sf(sc.rolling(50).mean()))
                # A/D proxy: sector closed higher today vs prior close
                if len(sc) >= 2:
                    adv_day += int(_sf(sc) > _sf(sc, -2))
            except Exception:
                continue
        sigs["Sector Breadth ≥ 50%"] = int((above_50sma / total_sectors) >= 0.5)
        sigs["Sector Breadth ≥ 85%"] = int((above_50sma / total_sectors) >= 0.85)
        # A/D candidates (probe — omit-not-zero pattern; computed separately)
        sigs["_Sectors Advancing"]    = int((adv_day / total_sectors) > 0.5)   # >50% advancing
        sigs["_Sectors Majority Up"]  = int((adv_day / total_sectors) >= 0.636) # ≥7/11 advancing

    if "Open" in spx_sl.columns:
        open_s = _squeeze(spx_sl, "Open")
        if len(open_s) >= 2 and len(close) >= 2:
            _gap_pts = _sf(open_s) - _sf(close, -2)
            sigs["Gap Up Day"] = int(_gap_pts > GAP_THRESHOLD)
            if _gap_pts < -GAP_THRESHOLD:
                sigs["Gap Down Contrarian"] = 1

    sigs["Stoch Bullish"] = int(_sf(stoch_k) > _sf(stoch_d))

    if len(close) >= 252:
        h52 = _sf(close.rolling(252).max()); l52 = _sf(close.rolling(252).min())
        r52 = max(h52 - l52, 1.0)
        sigs["52w Range Upper Half"] = int((c - l52) / r52 > 0.5)
    elif len(close) >= 20:
        h20 = _sf(high.rolling(20).max()); l20 = _sf(low.rolling(20).min())
        sigs["52w Range Upper Half"] = int((c - l20) / max(h20 - l20, 1.0) > 0.5)

    if len(close) >= 2:
        sigs["Above Prior Day High"] = int(c > _sf(high, -2))
        pivot = (_sf(high, -2) + _sf(low, -2) + _sf(close, -2)) / 3.0
        sigs["Above Pivot"] = int(c > pivot)
    if len(high) >= 6:
        sigs["Above 5d High"] = int(c > float(high.iloc[-6:-1].max()))

    return sigs


def run_probe(spx, vix, vvix, skew, sectors):
    close = _squeeze(spx, "Close")
    opens = _squeeze(spx, "Open")
    n     = len(spx)
    eval_start = max(252, n - 505)

    rows = []
    for i in range(eval_start, n - 1):
        cutoff = spx.index[i]
        spx_sl  = spx.iloc[:i + 1]
        vix_sl  = vix[vix.index <= cutoff]
        vvix_sl = vvix[vvix.index <= cutoff] if not vvix.empty else pd.Series(dtype=float)
        skew_sl = skew[skew.index <= cutoff] if not skew.empty else None
        sec_sl  = {t: df[df.index <= cutoff] for t, df in sectors.items()}

        sigs = _compute_signals(spx_sl, vix_sl, sec_sl, vvix_sl, skew_sl)
        if not sigs:
            continue

        nxt = float(close.iloc[i + 1]); cur = float(close.iloc[i])
        up = nxt > cur + 5; dn = nxt < cur - 5
        if not up and not dn:
            continue

        day_gap = _sf(opens, i) - _sf(close, i - 1) if i > 0 else 0.0

        # Baseline score (no probe signals)
        base_score = _grp_score({k: v for k, v in sigs.items() if not k.startswith("_") and k != "SKEW Below 130"})
        # +SKEW score
        skew_score = _grp_score(sigs, extra_group_name="Volatility", extra_signal_name="SKEW Below 130") \
                     if "SKEW Below 130" in sigs else base_score
        # +ADUP (>50%) score — inject into Breadth
        adup_sigs = dict(sigs); adup_sigs["Sectors Advancing"] = adup_sigs.pop("_Sectors Advancing", -1)
        adup_score = _grp_score({k: v for k, v in adup_sigs.items() if not k.startswith("_") and k != "SKEW Below 130"},
                                extra_group_name="Breadth", extra_signal_name="Sectors Advancing") \
                     if "Sectors Advancing" in adup_sigs and adup_sigs["Sectors Advancing"] >= 0 else base_score
        # +ADUPMAJ (≥7/11) score
        adm_sigs = dict(sigs); adm_sigs["Sectors Majority Up"] = adm_sigs.pop("_Sectors Majority Up", -1)
        adm_score = _grp_score({k: v for k, v in adm_sigs.items() if not k.startswith("_") and k != "SKEW Below 130"},
                                extra_group_name="Breadth", extra_signal_name="Sectors Majority Up") \
                    if "Sectors Majority Up" in adm_sigs and adm_sigs["Sectors Majority Up"] >= 0 else base_score

        rows.append({
            "up": up, "dn": dn, "day_gap": day_gap,
            "base": base_score, "skew": skew_score,
            "adup": adup_score, "adm": adm_score,
            "skew_on": int("SKEW Below 130" in sigs and sigs.get("SKEW Below 130", 0)),
            "adup_on": int(sigs.get("_Sectors Advancing", 0)),
            "adm_on":  int(sigs.get("_Sectors Majority Up", 0)),
        })

    def score_calls(rows, score_key):
        hits = tot = 0
        for r in rows:
            sc = r[score_key]
            bull = sc >= 55; bear = sc <= 44
            if not bull and not bear:
                continue
            if r["day_gap"] < -GAP_THRESHOLD and bear:
                continue
            correct = (bull and r["up"]) or (bear and r["dn"])
            hits += int(correct); tot += 1
        return hits, tot

    results = {}
    for key in ("base", "skew", "adup", "adm"):
        h, t = score_calls(rows, key)
        results[key] = (h, t, round(h/t*100, 1) if t else 0)

    # Fire rates
    skew_fires = sum(r["skew_on"] for r in rows)
    adup_fires = sum(r["adup_on"] for r in rows)
    adm_fires  = sum(r["adm_on"] for r in rows)
    n_rows = len(rows)

    print(f"\n{'─'*60}")
    print(f"  Walk-forward bars evaluated: {n_rows}")
    b = results["base"]; print(f"  Baseline:                    {b[0]}/{b[1]} = {b[2]}%")
    s = results["skew"]; print(f"  +SKEW Below 130 (Volatility):{s[0]}/{s[1]} = {s[2]}%  delta={s[2]-b[2]:+.1f}pp")
    a = results["adup"]; print(f"  +Sectors Advancing (Breadth):{a[0]}/{a[1]} = {a[2]}%  delta={a[2]-b[2]:+.1f}pp")
    m = results["adm"];  print(f"  +Sectors Maj Up    (Breadth):{m[0]}/{m[1]} = {m[2]}%  delta={m[2]-b[2]:+.1f}pp")
    print(f"{'─'*60}")
    print(f"  SKEW fires (of {n_rows}): {skew_fires} ({round(skew_fires/n_rows*100,1) if n_rows else 0}%)")
    print(f"  ADUP fires (of {n_rows}): {adup_fires} ({round(adup_fires/n_rows*100,1) if n_rows else 0}%)")
    print(f"  ADM  fires (of {n_rows}): {adm_fires} ({round(adm_fires/n_rows*100,1) if n_rows else 0}%)")


if __name__ == "__main__":
    print("[probe] Checking ^SKEW availability …")
    try:
        skew_df = yf.download("^SKEW", period="2y", interval="1d", progress=False, auto_adjust=True)
        if skew_df.empty:
            print("[probe] ^SKEW unavailable")
            skew = pd.Series(dtype=float)
        else:
            skew = _squeeze(skew_df, "Close").dropna()
            print(f"[probe] ^SKEW available: {len(skew)} bars  "
                  f"range {skew.min():.1f}–{skew.max():.1f}  mean {skew.mean():.1f}  "
                  f"<130: {(skew < 130).sum()} bars ({round((skew<130).mean()*100,1)}%)")
    except Exception as e:
        print(f"[probe] ^SKEW error: {e}")
        skew = pd.Series(dtype=float)

    print("[probe] Fetching 2y SPX/VIX/VVIX/sectors …")
    spx     = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
    vix     = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix_df = yf.download("^VVIX", period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix    = _squeeze(vvix_df, "Close") if not vvix_df.empty else pd.Series(dtype=float)
    sectors = {}
    for ticker in ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]:
        try:
            sectors[ticker] = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[ticker] = pd.DataFrame()

    run_probe(spx, vix, vvix, skew, sectors)
    print("\n[probe] done.")
