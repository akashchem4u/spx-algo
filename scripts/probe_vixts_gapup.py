#!/usr/bin/env python3
"""
Probe batch 4:

  VIXTS-01  "VIX Term Contango" — VIX < VIX3M (3-month VIX)
            = near-term vol below medium-term vol = contango = market expects calm to persist
            Independent of VIX level: even when VIX=30 if VIX3M=35 that's still contango (bullish)
            Ticker: ^VIX3M (CBOE 3-Month VIX)

  GAPUP-PRUNE  Remove Gap Up Day from scoring (currently +1.1% ablation drag, above 1.0% threshold)
               It was previously kept "for mechanistic reasons" despite being above the prune threshold.
               Re-test: does removing it now help in the current data window?
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

# Context group WITHOUT Gap Up Day (for GAPUP-PRUNE test)
SIGNAL_GROUPS_NO_GUPD = dict(SIGNAL_GROUPS)
SIGNAL_GROUPS_NO_GUPD["Context"] = ["Gap/ATR Normal", "VIX No Spike", "Gap Down Contrarian"]

# Context group WITH VIX Term Contango added to Volatility
SIGNAL_GROUPS_VIXTS = dict(SIGNAL_GROUPS)
SIGNAL_GROUPS_VIXTS["Volatility"] = list(SIGNAL_GROUPS["Volatility"]) + ["VIX Term Contango"]

SECTOR_TICKERS = ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]


def _sq(df, col):
    s = df[col] if col in df.columns else df.iloc[:, 0]
    return s.squeeze() if isinstance(s, pd.DataFrame) else s

def _sf(s, idx=-1, default=0.0):
    try: return float(s.iloc[idx])
    except: return default

def _rsi(close, p=14):
    d = close.diff(); g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100-(100/(1+g/l.replace(0, np.nan)))

def _macd(close):
    e12=close.ewm(span=12,adjust=False).mean(); e26=close.ewm(span=26,adjust=False).mean()
    ln=e12-e26; return ln, ln.ewm(span=9,adjust=False).mean()

def _atr(df, p=14):
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close"); pc=c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1).rolling(p).mean()

def _grp_score(sigs, groups):
    ws = []
    for _, gs in groups.items():
        present = [sigs[n] for n in gs if n in sigs]
        if present: ws.append(sum(present)/len(present))
    return round(sum(ws)/len(ws)*100) if ws else 50

def _compute_core(spx_sl, vix_sl, sector_slices, vvix_sl=None, vix3m_sl=None) -> dict[str, int]:
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl) < 20: return sigs
    close=_sq(spx_sl,"Close"); high=_sq(spx_sl,"High"); low=_sq(spx_sl,"Low")
    volume=_sq(spx_sl,"Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close)<20: return sigs
    c=_sf(close); sma20=close.rolling(20).mean(); sma50=close.rolling(50).mean(); sma200=close.rolling(200).mean()
    rsi_v=_rsi(close).dropna(); ml,ms=_macd(close); atr_v=_atr(spx_sl)
    stk_r=high.rolling(14).max()-low.rolling(14).min()
    stk_k=(100*(close-low.rolling(14).min())/stk_r.where(stk_r>0)).fillna(50).clip(0,100)
    stk_d=stk_k.rolling(3).mean().fillna(50)

    sigs["Above 20 SMA"]  = int(c>_sf(sma20))
    sigs["Above 50 SMA"]  = int(c>_sf(sma50))  if len(close)>=50  else 0
    sigs["Above 200 SMA"] = int(c>_sf(sma200)) if len(close)>=200 else 0
    rl=_sf(rsi_v,default=50.0)
    sigs["Higher Close (1d)"] = int(len(close)>=2 and c>_sf(close,-2))
    sigs["Higher Close (5d)"] = int(len(close)>=6 and c>_sf(close,-6))
    sigs["MACD Bullish"]      = int(_sf(ml)>_sf(ms))
    sigs["RSI Strong Trend"]  = int(60<=rl<=75)

    if not vix_sl.empty:
        vc=_sq(vix_sl,"Close"); vv=_sf(vc,default=20.0)
        sigs["VIX Below 20"]=int(vv<20); sigs["VIX Below 15"]=int(vv<15)
        sigs["VIX Falling"]=int(len(vc)>=6 and vv<_sf(vc,-6))
        sigs["VIX 1d Down"]=int(len(vc)>=2 and vv<_sf(vc,-2))
        if len(vc)>=4:
            v3d=_sf(vc,-4,default=vv); sigs["VIX No Spike"]=int((vv-v3d)/max(v3d,1)<=0.08)
        else:
            sigs["VIX No Spike"]=1
        sigs["ATR Contracting"]=int(len(atr_v.dropna())>=20 and _sf(atr_v)<_sf(atr_v,-5))

        # VIX Term Structure: VIX < VIX3M = contango = near-term fear below medium-term
        if vix3m_sl is not None and len(vix3m_sl)>=1:
            try:
                vix3m_val = float(vix3m_sl.iloc[-1])
                sigs["VIX Term Contango"] = int(vv < vix3m_val)
            except Exception:
                pass

    if vvix_sl is not None and len(vvix_sl)>=1:
        try:
            sigs["VVIX Below 100"]=int(float(vvix_sl.iloc[-1])<100)
        except Exception:
            pass

    if len(volume)>=20:
        sigs["Volume Above Average"]=int(_sf(volume)>_sf(volume.rolling(20).mean()) and len(close)>=2 and _sf(close)>_sf(close,-2))

    total_s=len(sector_slices)
    if total_s:
        above=0
        for df in sector_slices.values():
            if df.empty: continue
            try:
                sc=_sq(df,"Close")
                if len(sc)<50: continue
                above+=int(_sf(sc)>_sf(sc.rolling(50).mean()))
            except Exception: continue
        sigs["Sector Breadth ≥ 50%"]=int((above/total_s)>=0.5)
        sigs["Sector Breadth ≥ 85%"]=int((above/total_s)>=0.85)

    if "Open" in spx_sl.columns:
        op=_sq(spx_sl,"Open")
        if len(op)>=2 and len(close)>=2:
            gp=_sf(op)-_sf(close,-2)
            sigs["Gap Up Day"]=int(gp>GAP_THRESHOLD)
            if gp<-GAP_THRESHOLD: sigs["Gap Down Contrarian"]=1

    sigs["Stoch Bullish"]=int(_sf(stk_k)>_sf(stk_d))

    if len(close)>=252:
        h52=_sf(close.rolling(252).max()); l52=_sf(close.rolling(252).min())
        sigs["52w Range Upper Half"]=int((c-l52)/max(h52-l52,1.0)>0.5)
    elif len(close)>=20:
        h20=_sf(high.rolling(20).max()); l20=_sf(low.rolling(20).min())
        sigs["52w Range Upper Half"]=int((c-l20)/max(h20-l20,1.0)>0.5)

    if len(close)>=2:
        sigs["Above Prior Day High"]=int(c>_sf(high,-2))
        sigs["Above Pivot"]=int(c>(_sf(high,-2)+_sf(low,-2)+_sf(close,-2))/3)
    if len(high)>=6:
        sigs["Above 5d High"]=int(c>float(high.iloc[-6:-1].max()))

    return sigs


def run_probe():
    print("[vixts] Checking ^VIX3M availability …")
    try:
        vix3m_df = yf.download("^VIX3M", period="2y", interval="1d", progress=False, auto_adjust=True)
        if vix3m_df.empty:
            print("[vixts] ^VIX3M unavailable, trying VIX3M …")
            vix3m_df = yf.download("VIX3M", period="2y", interval="1d", progress=False, auto_adjust=True)
        vix3m = _sq(vix3m_df,"Close").dropna() if not vix3m_df.empty else pd.Series(dtype=float)
        if not vix3m.empty:
            print(f"[vixts] ^VIX3M available: {len(vix3m)} bars  range {vix3m.min():.1f}–{vix3m.max():.1f}")
        else:
            print("[vixts] ^VIX3M not available — will still run Gap Up prune test")
    except Exception as e:
        print(f"[vixts] ^VIX3M error: {e}")
        vix3m = pd.Series(dtype=float)

    print("[vixts] Fetching 2y market data …")
    spx     = yf.download("^GSPC", period="2y", interval="1d", progress=False, auto_adjust=True)
    vix     = yf.download("^VIX",  period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix_df = yf.download("^VVIX", period="2y", interval="1d", progress=False, auto_adjust=True)
    vvix    = _sq(vvix_df,"Close") if not vvix_df.empty else pd.Series(dtype=float)
    sectors = {}
    for t in SECTOR_TICKERS:
        try:
            sectors[t] = yf.download(t, period="2y", interval="1d", progress=False, auto_adjust=True)
        except Exception:
            sectors[t] = pd.DataFrame()

    close=_sq(spx,"Close"); opens=_sq(spx,"Open"); n=len(spx)
    eval_start=max(252, n-505)

    rows_base, rows_nogup, rows_vixts, rows_both = [], [], [], []
    vixts_fires = 0
    gapup_fires = 0

    for i in range(eval_start, n-1):
        cutoff  = spx.index[i]
        spx_sl  = spx.iloc[:i+1]
        vix_sl  = vix[vix.index<=cutoff]
        vvix_sl = vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        vix3m_sl= vix3m[vix3m.index<=cutoff] if not vix3m.empty else None
        sec_sl  = {t:df[df.index<=cutoff] for t,df in sectors.items()}

        sigs=_compute_core(spx_sl,vix_sl,sec_sl,vvix_sl,vix3m_sl)
        if not sigs: continue

        nxt=float(close.iloc[i+1]); cur=float(close.iloc[i])
        up=nxt>cur+5; dn=nxt<cur-5
        if not up and not dn: continue
        day_gap=_sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        if sigs.get("Gap Up Day",0)==1: gapup_fires+=1
        if sigs.get("VIX Term Contango") is not None: vixts_fires+=1

        # Variant scores
        score_base  = _grp_score(sigs, SIGNAL_GROUPS)

        # GAPUP-PRUNE: exclude Gap Up Day from scoring
        sigs_nogup = {k:v for k,v in sigs.items() if k!="Gap Up Day"}
        score_nogup = _grp_score(sigs_nogup, SIGNAL_GROUPS_NO_GUPD)

        # VIXTS: add VIX Term Contango to Volatility group
        score_vixts = _grp_score(sigs, SIGNAL_GROUPS_VIXTS)

        # BOTH: no Gap Up Day + VIX Term Contango
        score_both = _grp_score(sigs_nogup, {
            **SIGNAL_GROUPS_NO_GUPD,
            "Volatility": SIGNAL_GROUPS_VIXTS["Volatility"]
        })

        for score, bucket in [(score_base,rows_base),(score_nogup,rows_nogup),
                               (score_vixts,rows_vixts),(score_both,rows_both)]:
            bull=score>=55; bear=score<=44
            if not bull and not bear: continue
            if day_gap<-GAP_THRESHOLD and bear: continue
            correct=(bull and up)or(bear and dn)
            bucket.append({"correct":correct})

    def acc(rows):
        if not rows: return 0,0,0.0
        h=sum(r["correct"] for r in rows); t=len(rows)
        return h,t,round(h/t*100,1)

    bh,bt,ba=acc(rows_base); nh,nt,na=acc(rows_nogup)
    vh,vt,va=acc(rows_vixts); oh,ot,oa=acc(rows_both)

    n_total = len([i for i in range(eval_start, n-1)])  # approx
    print(f"\n{'─'*65}")
    print(f"  Baseline:                        {bh}/{bt} = {ba}%")
    print(f"  -Gap Up Day (prune):             {nh}/{nt} = {na}%  Δ={na-ba:+.1f}pp")
    print(f"  +VIX Term Contango (Volatility): {vh}/{vt} = {va}%  Δ={va-ba:+.1f}pp")
    print(f"  -Gap Up Day +VIX Term Contango:  {oh}/{ot} = {oa}%  Δ={oa-ba:+.1f}pp")
    print(f"{'─'*65}")
    print(f"  VIX Term Contango fires: {vixts_fires} bars  Gap Up Day fires: {gapup_fires} bars")
    if not vix3m.empty:
        vix_np = vix[["Close"]].squeeze() if "Close" in vix.columns else vix.iloc[:,0].squeeze()
        contango_n = 0; total_n = 0
        for idx in vix_np.index:
            if idx in vix3m.index:
                total_n += 1
                if float(vix_np.loc[idx]) < float(vix3m.loc[idx]):
                    contango_n += 1
        if total_n:
            print(f"  VIX<VIX3M (contango) overall: {contango_n}/{total_n} = {round(contango_n/total_n*100,1)}% of 2yr bars")


if __name__ == "__main__":
    run_probe()
    print("\n[vixts] done.")
