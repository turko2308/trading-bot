"""
tools/asia_range.py — פריצת הטווח האסייתי (DECISIONS §59), 26/09/2026
טווח = High/Low 00:00–06:59 UTC; פקודות Stop בקצוות 07:00–15:59 UTC; סטופ = הצד השני; יעד = גודל הטווח; סגירה 20:00 UTC.
חלק 1: הגרסה המוגדרת מראש (Renko 20$ בפיגור) בעלויות 0/0.5/1$. חלק 2: ביקורות — שני הצדדים / לונג / שורט / אקראי.
הרצה מתוך tools/: python asia_range.py   (קורא ../data/xauusd_m15.csv, ../data/xauusd_h1.csv)
"""
"""
Pre-declared (written before running, not tuned):
 Asian range = high/low of 00:00–06:59 UTC (M15). Skip if range < 5$.
 Renko 20$, 2-brick confirm, built on H1 CLOSES; direction = state after the last H1 bar that CLOSED by 07:00 UTC (lagged/known).
 One pending stop order in the Renko direction: buy at range high / sell at range low. Active 07:00–15:59 UTC.
 Stop = opposite side of the Asian range; target = 1 × range. Anything open closed at 20:00 UTC. One trade per day.
 Fills and exits on M15; stop checked first inside a bar. Costs: spread 0.77$ + slippage 0 / 0.5 / 1.0$ (entry and stop).
 Train < 2025-07-01, holdout ≥ 2025-07-01.
"""
import numpy as np, pandas as pd, collections
m=pd.read_csv("../data/xauusd_m15.csv"); m["t"]=pd.to_datetime(m["timestamp"],utc=True).dt.tz_localize(None)
h=pd.read_csv("../data/xauusd_h1.csv"); h["t"]=pd.to_datetime(h["timestamp"],utc=True).dt.tz_localize(None); h=h[h.high>h.low].reset_index(drop=True)
T=m.t.values;O=m.open.values;H=m.high.values;L=m.low.values;C=m.close.values;n=len(m)
HT=h.t.values;HC=h.close.values
# Renko 20$ on H1 closes, state known at each H1 close time
ref=HC[0]; dirs=[]; st=np.zeros(len(HC),int)
for k,c in enumerate(HC):
    while c-ref>=20: ref+=20; dirs.append(1)
    while ref-c>=20: ref-=20; dirs.append(-1)
    if len(dirs)>=2 and dirs[-1]==dirs[-2]: st[k]=dirs[-1]
    elif k>0: st[k]=st[k-1] if (dirs and dirs[-1]==st[k-1]) else 0
close_time=HT+np.timedelta64(3600,"s")
dates=pd.to_datetime(T).normalize().values
SPLIT=np.datetime64("2025-07-01"); K=4.5
res={0.0:[],0.5:[],1.0:[]}
for dd in np.unique(dates):
    i0=np.searchsorted(T,dd); i7=np.searchsorted(T,dd+np.timedelta64(7,"h")); i16=np.searchsorted(T,dd+np.timedelta64(16,"h")); i20=np.searchsorted(T,dd+np.timedelta64(20,"h"))
    if i7-i0<20 or i16<=i7: continue
    rh=H[i0:i7].max(); rl=L[i0:i7].min(); R=rh-rl
    if R<5: continue
    kk=np.searchsorted(close_time,dd+np.timedelta64(7,"h"),side="right")-1   # last H1 closed by 07:00
    if kk<0 or st[kk]==0: continue
    d=st[kk]; lvl=rh if d==1 else rl
    fill=None
    for i in range(i7,i16):
        if (d==1 and H[i]>=lvl) or (d==-1 and L[i]<=lvl): fill=i; break
    if fill is None: continue
    for s in res:
        e=(max(lvl,O[fill]) if d==1 else min(lvl,O[fill]))+d*s
        stop=rl if d==1 else rh; tgt=e+d*R; out=None
        for i in range(fill,max(fill+1,i20)):
            if (L[i]<=stop) if d==1 else (H[i]>=stop): out=d*(stop-e)-s; break
            if i>fill and ((H[i]>=tgt) if d==1 else (L[i]<=tgt)): out=d*(tgt-e); break
        if out is None: out=d*(C[max(fill,i20-1)]-e)
        res[s].append((dd,out-0.77))
days=len(np.unique(dates))*5/7; months=len(np.unique(dates))/30.4
for s,tr in res.items():
    v=np.array([x[1] for x in tr]); a=np.array([x[1] for x in tr if x[0]<SPLIT]); b=np.array([x[1] for x in tr if x[0]>=SPLIT]); y=collections.defaultdict(list)
    for t,p in tr: y[str(t)[:4]].append(p)
    t_=v.mean()/(v.std(ddof=1)/np.sqrt(len(v)))
    print(f"slip {s}$ (cost {0.77+s:.2f}$+): N={len(v)} ({len(v)/months:.0f}/month) win {100*(v>0).mean():3.0f}% {v.mean()*K:+6.1f}₪/tr {v.sum()*K/months:+6.0f}₪/mo t={t_:+5.2f} | TRAIN {a.mean()*K:+6.1f}₪ (N={len(a)}) | HOLDOUT {b.mean()*K:+6.1f}₪ (N={len(b)}) | yrs {[round(float(np.mean(x))*K,1) for k,x in sorted(y.items())]}")


# ---- controls ----
import numpy as np, collections
rng=np.random.default_rng(11)
def run(mode,s=0.5):
    out=[]
    for dd in np.unique(dates):
        i0=np.searchsorted(T,dd); i7=np.searchsorted(T,dd+np.timedelta64(7,"h")); i16=np.searchsorted(T,dd+np.timedelta64(16,"h")); i20=np.searchsorted(T,dd+np.timedelta64(20,"h"))
        if i7-i0<20 or i16<=i7: continue
        rh=H[i0:i7].max(); rl=L[i0:i7].min(); R=rh-rl
        if R<5: continue
        kk=np.searchsorted(close_time,dd+np.timedelta64(7,"h"),side="right")-1
        if mode=="renko":
            if kk<0 or st[kk]==0: continue
            sides=[st[kk]]
        elif mode=="long": sides=[1]
        elif mode=="short": sides=[-1]
        elif mode=="random": sides=[rng.choice([1,-1])]
        elif mode=="both": sides=[1,-1]
        fill=None
        for i in range(i7,i16):
            hu=H[i]>=rh and 1 in sides; hd=L[i]<=rl and -1 in sides
            if hu and hd: fill=(i,"both"); break
            if hu: fill=(i,1); break
            if hd: fill=(i,-1); break
        if fill is None: continue
        i,d=fill
        if d=="both": out.append((dd,0,-R-s-0.77)); continue
        lvl=rh if d==1 else rl; e=(max(lvl,O[i]) if d==1 else min(lvl,O[i]))+d*s
        stop=rl if d==1 else rh; tgt=e+d*R; r=None
        for j in range(i,max(i+1,i20)):
            if (L[j]<=stop) if d==1 else (H[j]>=stop): r=d*(stop-e)-s; break
            if j>i and ((H[j]>=tgt) if d==1 else (L[j]<=tgt)): r=d*(tgt-e); break
        if r is None: r=d*(C[max(i,i20-1)]-e)
        out.append((dd,d,r-0.77))
    return out
print("Controls, slip 0.5$ (same Asian range / stop / target / hours):")
for mode in ("renko","both","long","short","random"):
    tr=run(mode); v=np.array([x[2] for x in tr]); a=np.array([x[2] for x in tr if x[0]<SPLIT]); b=np.array([x[2] for x in tr if x[0]>=SPLIT])
    L_=[x[2] for x in tr if x[1]==1]; S_=[x[2] for x in tr if x[1]==-1]
    t_=v.mean()/(v.std(ddof=1)/np.sqrt(len(v)))
    print(f"  {mode:7s} N={len(v):3d} {v.mean()*K:+6.1f}₪/tr t={t_:+5.2f} | train {a.mean()*K:+6.1f} hold {b.mean()*K:+6.1f} | longs {np.mean(L_)*K if L_ else 0:+6.1f} (N{len(L_)}) shorts {np.mean(S_)*K if S_ else 0:+6.1f} (N{len(S_)})")
months=len(np.unique(dates))/30.4
print("\n'both sides' (first break of the Asian range, either direction) — costs and years:")
for s in (0.0,0.5,1.0):
    tr=run("both",s); v=np.array([x[2] for x in tr]); y=collections.defaultdict(list)
    for x in tr: y[str(x[0])[:4]].append(x[2])
    wh=sum(1 for x in tr if x[1]==0)
    t_=v.mean()/(v.std(ddof=1)/np.sqrt(len(v)))
    print(f"  slip {s}: N={len(v)} ({len(v)/months:.0f}/mo) win {100*(v>0).mean():.0f}% {v.mean()*K:+5.1f}₪/tr {v.sum()*K/months:+5.0f}₪/mo t={t_:+.2f} whipsaw-days {wh} | yrs {[round(float(np.mean(x))*K,1) for k,x in sorted(y.items())]}")
