"""
tools/portfolio.py — תיק שיטות 2 / 3(6H) / 5(דריפט פתיחה) + גודל פוזיציה לפי drawdown (20/09/2026)
הרצה מתוך tools/: python portfolio.py   (דורש trading_bot.py בשורש, data/xauusd_{h1,h4,m15}.csv, be.py, tf_engine_v2.py, m4_range_renko.py)
⚠️ שיטה 3 נספרת כאן עם כל האיתותים (לא עסקה-אחת-בכל-רגע כמו בחי) — מנפח את מספר העסקאות והרווח. הרווחים משנות 2024-2026 (שור חזק): להפחית חצי.
"""
import sys, os, datetime, numpy as np, collections
sys.path.insert(0,".")
import tf_engine_v2 as tf
import be
# ---------- Method 3 (6H) : $/oz per trade, exit time
h1=tf.load_h1(); cfg=[c for c in tf.TF_CONFIGS if c["name"]=="6H"][0]
bars=tf.tf_aggregate(h1,cfg["hours"]); closes=[b["c"] for b in bars]
ema=tf.tb.calc_ema_series(closes,tf.TF_EMA_PERIOD); atr=tf.tf_atr_series(bars,tf.TF_ATR_PERIOD)
sigs=tf.generate_signals(bars,ema,atr,"6H"); cl=tf.simulate_exits(sigs,h1,cfg["hours"],0.0)
m3=[(c["exit_t"].replace(tzinfo=None),c["pts"]-tf.SPREAD_POINTS) for c in cl]
# ---------- Method 2 : $/oz per trade
h4=be.load("../data/xauusd_h4.csv"); d2=be.sim(h4)["detail"]
m2=[(x["ct"].replace(tzinfo=None),x["pts"]-be.SPREAD_PTS) for x in d2]
# ---------- Reopen drift (2h) : $/oz, on M15
import m4_range_renko as m4
m15=m4.load_m15("../data/xauusd_m15.csv"); m5=[]
for i in range(1,len(m15)-8):
    b=m15[i];p=m15[i-1]
    if (b["t"]-p["t"]).total_seconds()/60>=30 and 21<=b["t"].hour<=23 and b["t"].weekday()<5:
        seg=m15[i:i+8]
        if (seg[-1]["t"]-seg[0]["t"]).total_seconds()==105*60: m5.append((seg[-1]["t"],seg[-1]["c"]-seg[0]["o"]-0.77))
LO=datetime.datetime(2024,1,1); HI=datetime.datetime(2026,7,29)
def win(x): return [(t,v) for t,v in x if LO<=t.replace(tzinfo=None)<=HI]
S={"Method 2 (Donchian 20/3)":win(m2),"Method 3 (6H)":win(m3),"Method 5 (reopen 2h)":win(m5)}
months=[]; y,m=2024,1
while (y,m)<=(2026,7): months.append((y,m)); m+=1; y+=(m>12); m=1 if m>12 else m
def monthly(tr):
    d=collections.defaultdict(float)
    for t,v in tr: d[(t.year,t.month)]+=v
    return np.array([d[k] for k in months])
M={k:monthly(v) for k,v in S.items()}
print(f"window 2024-01 … 2026-07 ({len(months)} months). P&L in $ per 1 oz, after 0.77 spread")
for k,v in S.items(): print(f"  {k:26s} trades={len(v):4d}  total={sum(x for _,x in v):+7.1f}$/oz  month mean {M[k].mean():+6.2f}  month std {M[k].std(ddof=1):5.2f}  worst month {M[k].min():+6.1f}  months>0 {100*(M[k]>0).mean():.0f}%")
ks=list(S); C=np.corrcoef([M[k] for k in ks]); print("monthly P&L correlation:"); 
for i,a in enumerate(ks): print("  ",a[:22].ljust(22),np.round(C[i],2))
# risk-parity weights (inverse monthly std), normalised so combined monthly std == 1.0 oz-equivalent scale later
w=np.array([1/M[k].std(ddof=1) for k in ks]); w=w/w.sum()
print("risk-parity weights:",{k[:9]:round(float(x),2) for k,x in zip(ks,w)})
comb=sum(w[i]*M[k] for i,k in enumerate(ks))    # $ per (weighted) oz-unit per month
eq=np.cumsum(comb); dd=(np.maximum.accumulate(eq)-eq).max()
print(f"combined (weights sum=1 oz): mean {comb.mean():+.2f}$/month, std {comb.std(ddof=1):.2f}, hist max DD {dd:.1f}$/oz, months>0 {100*(comb>0).mean():.0f}%")
# monte-carlo (block bootstrap of months, 12-month paths) -> 95th percentile drawdown and 12m outcome, per 1 oz total exposure
rng=np.random.default_rng(3); ddp=[];end=[]
for _ in range(5000):
    path=rng.choice(comb,size=24,replace=True); e=np.cumsum(path)
    ddp.append((np.maximum.accumulate(np.concatenate([[0],e]))-np.concatenate([[0],e])).max()); end.append(e[-1])
ddp=np.array(ddp); end=np.array(end)
print(f"MC 24 months per 1 oz: DD median {np.median(ddp):.1f}$ | 95th pct {np.percentile(ddp,95):.1f}$ | 99th {np.percentile(ddp,99):.1f}$ | P(lose money over 24m) {100*(end<0).mean():.0f}%")
USD=3.0014
print("\nSIZING (drawdown budget = 30% of account at the 95th-pct MC drawdown; ₪ = $ × 3.0014):")
d95=np.percentile(ddp,95)*USD
mean_m=comb.mean()*USD
for A in (500,2000,5000,10000,20000):
    oz=0.30*A/d95
    print(f" account {A:6d}₪ → max total exposure ≈ {oz:5.2f} oz | expected ≈ {mean_m*oz:6.1f}₪/month ({100*mean_m*oz/A:4.1f}%/month) | 95th-pct drawdown ≈ {d95*oz:6.0f}₪ {'⚠ below Plus500 min 0.75oz' if oz<0.75 else ''}")
