#!/usr/bin/env python3
"""
Probe batch 3: activate the dead Macro group via daily-close proxies.

Current Macro group signals: "Yield Curve Positive", "Credit Spread Calm"
Both have 0% coverage (live-only). Goal: replace with daily-close backtestable versions.

Candidates:
  MACRO-A  "HYG Above 50 SMA"  — high-yield ETF above 50-day SMA = credit spreads calm = bull
  MACRO-B  "HYG 5d Trending"   — HYG close today > HYG close 5 sessions ago = spreads tightening
  MACRO-C  "Yield Curve Pos"   — ^TNX (10yr) > ^IRX (3-month bill) = non-inverted = bull context
  MACRO-D  "HYG + Yield Curve" — both MACRO-A and MACRO-C active together

Methodology:
  Walk-forward 2yr backtest using same gap-down abstain gate.
  Each candidate replaces the 0-coverage Macro group signals in sigs{},
  causing the Macro group to become active in _grp_score().

Key risk: activating a new group changes the group-weight denominator from 7→7 (if Macro
was never contributing) to 7→8 active groups. Net effect depends on signal direction.
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
    "Macro":      ["Yield Curve Positive", "Credit Spread Calm"],  # 0% coverage currently
    "Context":    ["Gap/ATR Normal", "VIX No Spike", "Gap Up Day", "Gap Down Contrarian"],
    "Position":   ["52w Range Upper Half", "Above Prior Day High", "Above Pivot", "Above 5d High"],
}
SECTOR_TICKERS = ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]


def _sq(df, col):
    s = df[col] if col in df.columns else df.iloc[:, 0]
    return s.squeeze() if isinstance(s, pd.DataFrame) else s

def _sf(s, idx=-1, default=0.0):
    try:
        return float(s.iloc[idx])
    except Exception:
        return default

def _rsi(close, period=14):
    d = close.diff(); g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def _macd(close):
    e12 = close.ewm(span=12, adjust=False).mean()
    e26 = close.ewm(span=26, adjust=False).mean()
    ln = e12 - e26
    return ln, ln.ewm(span=9, adjust=False).mean()

def _atr(df, p=14):
    h = _sq(df,"High"); l = _sq(df,"Low"); c = _sq(df,"Close"); pc = c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1).rolling(p).mean()

def _grp_score(sigs):
    ws = []
    for _, gs in SIGNAL_GROUPS.items():
        present = [sigs[n] for n in gs if n in sigs]
        if present:
            ws.append(sum(present)/len(present))
    return round(sum(ws)/len(ws)*100) if ws else 50

def _compute_core(spx_sl, vix_sl, sector_slices, vvix_sl=None) -> dict[str, int]:
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20:
        return sigs
    close  = _sq(spx_sl,"Close"); high = _sq(spx_sl,"High"); low = _sq(spx_sl,"Low")
    volume = _sq(spx_sl,"Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close) < 20:
        return sigs
    c = _sf(close)
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi_v  = _rsi(close).dropna(); ml, ms = _macd(close); atr_v = _atr(spx_sl)
    stk_r = high.rolling(14).max()-low.rolling(14).min()
    stk_k = (100*(close-low.rolling(14).min())/stk_r.where(stk_r>0)).fillna(50).clip(0,100)
    stk_d = stk_k.rolling(3).mean().fillna(50)

    sigs["Above 20 SMA"]  = int(c>_sf(sma20))
    sigs["Above 50 SMA"]  = int(c>_sf(sma50))  if len(close)>=50  else 0
    sigs["Above 200 SMA"] = int(c>_sf(sma200)) if len(close)>=200 else 0
    rl = _sf(rsi_v, default=50.0)
    sigs["Higher Close (1d)"] = int(len(close)>=2 and c>_sf(close,-2))
    sigs["Higher Close (5d)"] = int(len(close)>=6 and c>_sf(close,-6))
    sigs["MACD Bullish"]      = int(_sf(ml)>_sf(ms))
    sigs["RSI Strong Trend"]  = int(60<=rl<=75)

    if not vix_sl.empty:
        vc = _sq(vix_sl,"Close"); vv = _sf(vc, default=20.0)
        sigs["VIX Below 20"]    = int(vv<20)
        sigs["VIX Below 15"]    = int(vv<15)
        sigs["VIX Falling"]     = int(len(vc)>=6 and vv<_sf(vc,-6))
        sigs["VIX 1d Down"]     = int(len(vc)>=2 and vv<_sf(vc,-2))
        if len(vc)>=4:
            v3d = _sf(vc,-4,default=vv)
            sigs["VIX No Spike"] = int((vv-v3d)/max(v3d,1)<=0.08)
        else:
            sigs["VIX No Spike"] = 1
        sigs["ATR Contracting"] = int(len(atr_v.dropna())>=20 and _sf(atr_v)<_sf(atr_v,-5))

    if vvix_sl is not None and len(vvix_sl)>=1:
        try:
            sigs["VVIX Below 100"] = int(float(vvix_sl.iloc[-1])<100)
        except Exception:
            pass

    if len(volume)>=20:
        sigs["Volume Above Average"] = int(_sf(volume)>_sf(volume.rolling(20).mean()) and len(close)>=2 and _sf(close)>_sf(close,-2))

    total_s = len(sector_slices)
    if total_s:
        above = 0
        for df in sector_slices.values():
            if df.empty: continue
            try:
                sc = _sq(df,"Close")
                if len(sc)<50: continue
                above += int(_sf(sc)>_sf(sc.rolling(50).mean()))
            except Exception: continue
        sigs["Sector Breadth ≥ 50%"] = int((above/total_s)>=0.5)
        sigs["Sector Breadth ≥ 85%"] = int((above/total_s)>=0.85)

    if "Open" in spx_sl.columns:
        op = _sq(spx_sl,"Open")
        if len(op)>=2 and len(close)>=2:
            gp = _sf(op)-_sf(close,-2)
            sigs["Gap Up Day"] = int(gp>GAP_THRESHOLD)
            if gp<-GAP_THRESHOLD:
                sigs["Gap Down Contrarian"] = 1

    sigs["Stoch Bullish"] = int(_sf(stk_k)>_sf(stk_d))

    if len(close)>=252:
        h52=_sf(close.rolling(252).max()); l52=_sf(close.rolling(252).min())
        sigs["52w Range Upper Half"] = int((c-l52)/max(h52-l52,1.0)>0.5)
    elif len(close)>=20:
        h20=_sf(high.rolling(20).max()); l20=_sf(low.rolling(20).min())
        sigs["52w Range Upper Half"] = int((c-l20)/max(h20-l20,1.0)>0.5)

    if len(close)>=2:
        sigs["Above Prior Day High"] = int(c>_sf(high,-2))
        sigs["Above Pivot"] = int(c>(_sf(high,-2)+_sf(low,-2)+_sf(close,-2))/3)
    if len(high)>=6:
        sigs["Above 5d High"] = int(c>float(high.iloc[-6:-1].max()))

    return sigs


def run_probe():
    print("[macro] Downloading 2y data …")
    spx     = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
    vix     = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix_df = yf.download("^VVIX", period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix    = _sq(vvix_df,"Close") if not vvix_df.empty else pd.Series(dtype=float)
    hyg_df  = yf.download("HYG",   period="2y", interval="1d", progress=False, auto_adjust=True)
    hyg     = _sq(hyg_df,"Close")  if not hyg_df.empty else pd.Series(dtype=float)
    tnx_df  = yf.download("^TNX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    tnx     = _sq(tnx_df,"Close")  if not tnx_df.empty else pd.Series(dtype=float)
    irx_df  = yf.download("^IRX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    irx     = _sq(irx_df,"Close")  if not irx_df.empty else pd.Series(dtype=float)
    sectors = {}
    for t in SECTOR_TICKERS:
        try:
            sectors[t] = yf.download(t, period="2y", interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[t] = pd.DataFrame()

    close = _sq(spx,"Close"); opens = _sq(spx,"Open"); n = len(spx)
    eval_start = max(252, n-505)

    base_rows, hyg_a_rows, hyg_b_rows, yc_rows, combo_rows = [], [], [], [], []

    for i in range(eval_start, n-1):
        cutoff  = spx.index[i]
        spx_sl  = spx.iloc[:i+1]
        vix_sl  = vix[vix.index<=cutoff]
        vvix_sl = vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        sec_sl  = {t:df[df.index<=cutoff] for t,df in sectors.items()}
        hyg_sl  = hyg[hyg.index<=cutoff]  if not hyg.empty  else pd.Series(dtype=float)
        tnx_sl  = tnx[tnx.index<=cutoff]  if not tnx.empty  else pd.Series(dtype=float)
        irx_sl  = irx[irx.index<=cutoff]  if not irx.empty  else pd.Series(dtype=float)

        sigs = _compute_core(spx_sl, vix_sl, sec_sl, vvix_sl)
        if not sigs: continue

        nxt = float(close.iloc[i+1]); cur = float(close.iloc[i])
        up = nxt>cur+5; dn = nxt<cur-5
        if not up and not dn: continue
        day_gap = _sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        # Macro signal candidates
        hyg_a = 0
        if len(hyg_sl)>=50:
            try:
                hyg_a = int(_sf(hyg_sl)>_sf(hyg_sl.rolling(50).mean()))
            except Exception:
                pass

        hyg_b = 0
        if len(hyg_sl)>=6:
            try:
                hyg_b = int(_sf(hyg_sl)>_sf(hyg_sl,-6))
            except Exception:
                pass

        yc = 0
        if len(tnx_sl)>=1 and len(irx_sl)>=1:
            try:
                yc = int(_sf(tnx_sl)>_sf(irx_sl))
            except Exception:
                pass

        def _score_variant(extra_sigs):
            s = dict(sigs, **extra_sigs)
            return _grp_score(s)

        # Variants
        score_base  = _grp_score(sigs)
        # MACRO-A: HYG above 50 SMA → Credit Spread Calm
        sigs_a = dict(sigs); sigs_a["Credit Spread Calm"] = hyg_a
        score_a = _grp_score(sigs_a) if len(hyg_sl)>=50 else score_base
        # MACRO-B: HYG 5d trending → Credit Spread Calm
        sigs_b = dict(sigs); sigs_b["Credit Spread Calm"] = hyg_b
        score_b = _grp_score(sigs_b) if len(hyg_sl)>=6 else score_base
        # MACRO-C: TNX > IRX → Yield Curve Positive
        sigs_c = dict(sigs); sigs_c["Yield Curve Positive"] = yc
        score_c = _grp_score(sigs_c) if (len(tnx_sl)>=1 and len(irx_sl)>=1) else score_base
        # MACRO-D: both HYG-A and YC
        sigs_d = dict(sigs); sigs_d["Credit Spread Calm"] = hyg_a; sigs_d["Yield Curve Positive"] = yc
        score_d = _grp_score(sigs_d) if len(hyg_sl)>=50 else score_base

        for score, bucket in [(score_base,base_rows),(score_a,hyg_a_rows),
                               (score_b,hyg_b_rows),(score_c,yc_rows),(score_d,combo_rows)]:
            bull=score>=55; bear=score<=44
            if not bull and not bear: continue
            if day_gap<-GAP_THRESHOLD and bear: continue
            correct=(bull and up)or(bear and dn)
            bucket.append({"correct":correct,"up":up,"dn":dn})

    def acc(rows):
        if not rows: return 0,0,0.0
        h=sum(r["correct"] for r in rows); t=len(rows)
        return h,t,round(h/t*100,1)

    bh,bt,ba = acc(base_rows)
    ah,at,aa = acc(hyg_a_rows)
    bxh,bxt,bxa = acc(hyg_b_rows)
    ch,ct,ca = acc(yc_rows)
    dh,dt,da = acc(combo_rows)

    print(f"\n{'─'*65}")
    print(f"  Baseline (Macro dead):           {bh}/{bt} = {ba}%")
    print(f"  +HYG>50SMA  (Credit Spread Calm):{ah}/{at} = {aa}%  Δ={aa-ba:+.1f}pp")
    print(f"  +HYG>5d     (Credit Spread Calm):{bxh}/{bxt} = {bxa}%  Δ={bxa-ba:+.1f}pp")
    print(f"  +TNX>IRX    (Yield Curve Pos):   {ch}/{ct} = {ca}%  Δ={ca-ba:+.1f}pp")
    print(f"  +HYG>50SMA+YC  (both Macro):     {dh}/{dt} = {da}%  Δ={da-ba:+.1f}pp")
    print(f"{'─'*65}")

    # Describe macro signal distributions
    if not hyg.empty and len(hyg)>=50:
        hyg_50sma = hyg.rolling(50).mean()
        hyg_bull_pct = round((hyg > hyg_50sma).mean()*100,1)
        print(f"  HYG>50SMA fires: {hyg_bull_pct}% of all 2yr bars")
    if not hyg.empty and len(hyg)>=6:
        hyg_5d_pct = round((hyg > hyg.shift(5)).mean()*100,1)
        print(f"  HYG>5d fires:    {hyg_5d_pct}% of all 2yr bars")
    if not tnx.empty and not irx.empty:
        yc_pct = round((tnx > irx).mean()*100,1)
        print(f"  TNX>IRX fires:   {yc_pct}% of all 2yr bars")


if __name__ == "__main__":
    run_probe()
    print("\n[macro] done.")
