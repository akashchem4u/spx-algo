#!/usr/bin/env python3
"""
Probe: VIX relative signals.

The existing model uses absolute VIX thresholds (VIX < 20, VIX < 15).
These rarely fire in sustained bear markets (VIX consistently above 20).

Alternative: VIX relative to its own recent history — does it HELP on the margins?

  VREL-A  "VIX Below 20d Avg"   — VIX today < 20d average VIX
            Fires on relatively calm days within bear markets.
            Partially independent from VIX Below 20 (can fire even when VIX=25
            if average has been 30).

  VREL-B  "VIX Below 5d Avg"    — VIX today < 5d average VIX
            Faster-responding version; captures short-term VIX compression.
            Already partially covered by VIX 1d Down, but 5d average is smoother.

  VREL-C  "VIX Z-Score <0"      — (VIX - 20d_avg) / 20d_std < 0
            VIX below its own 20d mean by any amount (Z < 0).
            Very similar to VREL-A conceptually.

  VREL-D  "VIX Z-Score <-0.5"   — (VIX - 20d_avg) / 20d_std < -0.5
            VIX meaningfully below its own 20d mean (1/2 std below).
            More selective — requires substantial relative calm.
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
SECTOR_TICKERS = ["XLF","XLK","XLE","XLV","XLI","XLC","XLY","XLP","XLB","XLRE","XLU"]

def _sq(df,c): s=df[c] if c in df.columns else df.iloc[:,0]; return s.squeeze() if isinstance(s,pd.DataFrame) else s
def _sf(s,idx=-1,default=0.0):
    try: return float(s.iloc[idx])
    except: return default
def _rsi(c,p=14):
    d=c.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    return 100-(100/(1+g/l.replace(0,np.nan)))
def _macd(c):
    e12=c.ewm(span=12,adjust=False).mean(); e26=c.ewm(span=26,adjust=False).mean()
    ln=e12-e26; return ln,ln.ewm(span=9,adjust=False).mean()
def _atr(df,p=14):
    h=_sq(df,"High"); l=_sq(df,"Low"); c=_sq(df,"Close"); pc=c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1).rolling(p).mean()

def _grp_score(sigs, groups):
    ws=[]
    for _,gs in groups.items():
        present=[sigs[n] for n in gs if n in sigs]
        if present: ws.append(sum(present)/len(present))
    return round(sum(ws)/len(ws)*100) if ws else 50

def _compute_core(spx_sl, vix_sl, sector_slices, vvix_sl=None):
    sigs: dict[str, int] = {}
    if spx_sl.empty or len(spx_sl)<20: return sigs
    close=_sq(spx_sl,"Close"); high=_sq(spx_sl,"High"); low=_sq(spx_sl,"Low")
    volume=_sq(spx_sl,"Volume") if "Volume" in spx_sl.columns else pd.Series(dtype=float)
    if len(close)<20: return sigs
    c=_sf(close); sma20=close.rolling(20).mean(); sma50=close.rolling(50).mean(); sma200=close.rolling(200).mean()
    rsi_v=_rsi(close).dropna(); ml,ms=_macd(close); atr_v=_atr(spx_sl)
    stk_r=high.rolling(14).max()-low.rolling(14).min()
    stk_k=(100*(close-low.rolling(14).min())/stk_r.where(stk_r>0)).fillna(50).clip(0,100)
    stk_d=stk_k.rolling(3).mean().fillna(50)
    sigs["Above 20 SMA"]=int(c>_sf(sma20)); sigs["Above 50 SMA"]=int(c>_sf(sma50)) if len(close)>=50 else 0
    sigs["Above 200 SMA"]=int(c>_sf(sma200)) if len(close)>=200 else 0
    rl=_sf(rsi_v,default=50.0)
    sigs["Higher Close (1d)"]=int(len(close)>=2 and c>_sf(close,-2))
    sigs["Higher Close (5d)"]=int(len(close)>=6 and c>_sf(close,-6))
    sigs["MACD Bullish"]=int(_sf(ml)>_sf(ms)); sigs["RSI Strong Trend"]=int(60<=rl<=75)

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

        # VIX relative signals (probe)
        if len(vc)>=20:
            try:
                vix_20d_avg = _sf(vc.rolling(20).mean(), default=vv)
                vix_20d_std = _sf(vc.rolling(20).std(), default=1.0)
                sigs["VIX Below 20d Avg"] = int(vv < vix_20d_avg)
                if vix_20d_std > 0:
                    z = (vv - vix_20d_avg) / vix_20d_std
                    sigs["VIX Z <0"]   = int(z < 0)     # below own avg
                    sigs["VIX Z <-0.5"] = int(z < -0.5)  # meaningfully below avg
            except Exception:
                pass
        if len(vc)>=6:
            try:
                vix_5d_avg = _sf(vc.rolling(5).mean(), default=vv)
                sigs["VIX Below 5d Avg"] = int(vv < vix_5d_avg)
            except Exception:
                pass

    if vvix_sl is not None and len(vvix_sl)>=1:
        try: sigs["VVIX Below 100"]=int(float(vvix_sl.iloc[-1])<100)
        except: pass

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
            except: continue
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
    print("[vrel] Fetching 2y data …")
    spx    =yf.download("^GSPC",period="2y",interval="1d",progress=False,auto_adjust=True)
    vix    =yf.download("^VIX", period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix_df=yf.download("^VVIX",period="2y",interval="1d",progress=False,auto_adjust=True)
    vvix   =_sq(vvix_df,"Close") if not vvix_df.empty else pd.Series(dtype=float)
    sectors={}
    for t in SECTOR_TICKERS:
        try: sectors[t]=yf.download(t,period="2y",interval="1d",progress=False,auto_adjust=True)
        except: sectors[t]=pd.DataFrame()

    close=_sq(spx,"Close"); opens=_sq(spx,"Open"); n=len(spx)
    eval_start=max(252,n-505)

    # Variants: each adds one VIX relative signal to Volatility group
    G_A = dict(SIGNAL_GROUPS); G_A["Volatility"]=list(SIGNAL_GROUPS["Volatility"])+["VIX Below 20d Avg"]
    G_B = dict(SIGNAL_GROUPS); G_B["Volatility"]=list(SIGNAL_GROUPS["Volatility"])+["VIX Below 5d Avg"]
    G_C = dict(SIGNAL_GROUPS); G_C["Volatility"]=list(SIGNAL_GROUPS["Volatility"])+["VIX Z <0"]
    G_D = dict(SIGNAL_GROUPS); G_D["Volatility"]=list(SIGNAL_GROUPS["Volatility"])+["VIX Z <-0.5"]

    rows_base,rows_a,rows_b,rows_c,rows_d=[],[],[],[],[]
    fire_a=fire_b=fire_c=fire_d=0

    for i in range(eval_start,n-1):
        cutoff=spx.index[i]
        spx_sl=spx.iloc[:i+1]; vix_sl=vix[vix.index<=cutoff]
        vvix_sl=vvix[vvix.index<=cutoff] if not vvix.empty else pd.Series(dtype=float)
        sec_sl={t:df[df.index<=cutoff] for t,df in sectors.items()}

        sigs=_compute_core(spx_sl,vix_sl,sec_sl,vvix_sl)
        if not sigs: continue

        nxt=float(close.iloc[i+1]); cur=float(close.iloc[i])
        up=nxt>cur+5; dn=nxt<cur-5
        if not up and not dn: continue
        day_gap=_sf(opens,i)-_sf(close,i-1) if i>0 else 0.0

        if sigs.get("VIX Below 20d Avg",0): fire_a+=1
        if sigs.get("VIX Below 5d Avg",0): fire_b+=1
        if sigs.get("VIX Z <0",0): fire_c+=1
        if sigs.get("VIX Z <-0.5",0): fire_d+=1

        for sc,bucket,groups in [
            (_grp_score(sigs,SIGNAL_GROUPS),rows_base,SIGNAL_GROUPS),
            (_grp_score(sigs,G_A),rows_a,G_A),
            (_grp_score(sigs,G_B),rows_b,G_B),
            (_grp_score(sigs,G_C),rows_c,G_C),
            (_grp_score(sigs,G_D),rows_d,G_D),
        ]:
            bull=sc>=55; bear=sc<=44
            if not bull and not bear: continue
            if day_gap<-GAP_THRESHOLD and bear: continue
            correct=(bull and up)or(bear and dn)
            bucket.append({"correct":correct})

    def acc(r):
        if not r: return 0,0,0.0
        h=sum(x["correct"] for x in r); t=len(r)
        return h,t,round(h/t*100,1)

    bh,bt,ba=acc(rows_base)
    ah,at,aa=acc(rows_a); bbh,bbt,bba=acc(rows_b)
    ch,ct,ca=acc(rows_c); dh,dt,da=acc(rows_d)

    print(f"\n{'─'*70}")
    print(f"  Baseline:                          {bh}/{bt} = {ba}%")
    print(f"  +VIX Below 20d Avg (Volatility):   {ah}/{at} = {aa}%  Δ={aa-ba:+.1f}pp  fires:{fire_a}/{bt}")
    print(f"  +VIX Below 5d Avg  (Volatility):   {bbh}/{bbt} = {bba}%  Δ={bba-ba:+.1f}pp  fires:{fire_b}/{bt}")
    print(f"  +VIX Z <0          (Volatility):   {ch}/{ct} = {ca}%  Δ={ca-ba:+.1f}pp  fires:{fire_c}/{bt}")
    print(f"  +VIX Z <-0.5       (Volatility):   {dh}/{dt} = {da}%  Δ={da-ba:+.1f}pp  fires:{fire_d}/{bt}")
    print(f"{'─'*70}")
    # Show how often VIX is above 20 in the eval window
    vix_close_eval = _sq(vix,"Close")
    vix_above20_pct = round((vix_close_eval > 20).mean()*100, 1)
    print(f"  VIX>20 overall: {vix_above20_pct}% of 2yr bars (so VIX Below 20 fires rarely in bear market)")


if __name__ == "__main__":
    run_probe()
    print("\n[vrel] done.")
