"""
tools/m4_atr_renko.py — שיטה 4 החדשה: Renko עם קופסה לפי ATR, כניסה בפקודת Stop ברמת הלבנה (23/09/2026)
==========================================================================================
מקור הממצא ב-DECISIONS §46. משווה Renko קופסה קבועה 3$ (שיטה 1) מול קופסה = f×ATR14(H1),
סטופ 3 קופסאות, יעד 6, תפוגה 6 לבנים. כניסה: market אחרי סגירת H1 או pending ברמת הלבנה הבאה.
סטופ/יעד נבדקים בתוך הנר (M15). ספרד 0.77$. ₪ = 1.5oz × 3.0 ₪/$.
הרצה מתוך tools/: python m4_atr_renko.py   (קורא ../data/xauusd_m15.csv, ../data/xauusd_h1.csv)
"""
import numpy as np, pandas as pd, collections
m=pd.read_csv("../data/xauusd_m15.csv"); m["t"]=pd.to_datetime(m["timestamp"],utc=True).dt.tz_localize(None)
h=pd.read_csv("../data/xauusd_h1.csv"); h["t"]=pd.to_datetime(h["timestamp"],utc=True).dt.tz_localize(None)
h=h[(h.high>h.low)]                                             # no flat bars
T=m.t.values; O=m.open.values; Hm=m.high.values; Lm=m.low.values; Cm=m.close.values; n=len(m)
HT=h.t.values; HC=h.close.values; HH=h.high.values; HL=h.low.values; nh=len(HC)
end_idx=np.searchsorted(T,HT+np.timedelta64(3600,"s"))
tr=np.maximum(HH-HL,np.maximum(abs(HH-np.roll(HC,1)),abs(HL-np.roll(HC,1)))); atr=pd.Series(tr).rolling(14).mean().values
SPREAD=0.77; K=4.5; SPLIT=np.datetime64("2025-07-01")
def run(boxf,slip=0.0,mode="market"):
    ref=HC[20]; dirs=[]; pos=None; out=[]
    for k in range(20,nh-1):
        box=boxf(k); a=end_idx[k]; b=end_idx[k+1]
        if pos is None and mode=="pending" and dirs:
            d=dirs[-1]; lvl=ref+d*box
            for i in range(a,b):
                if (d==1 and Hm[i]>=lvl) or (d==-1 and Lm[i]<=lvl):
                    f=(max(lvl,O[i]) if d==1 else min(lvl,O[i]))+d*slip
                    pos=dict(d=d,e=f,stop=f-d*3*box,tgt=f+d*6*box,br=0,t=T[i],i0=i+1); break
        if pos is not None:
            for i in range(max(a,pos["i0"]),b):
                d=pos["d"]
                if (Lm[i]<=pos["stop"]) if d==1 else (Hm[i]>=pos["stop"]):
                    out.append((pos["t"],d,d*(pos["stop"]-pos["e"])-SPREAD-slip)); pos=None; break
                if (Hm[i]>=pos["tgt"]) if d==1 else (Lm[i]<=pos["tgt"]):
                    out.append((pos["t"],d,d*(pos["tgt"]-pos["e"])-SPREAD)); pos=None; break
            if pos is not None: pos["i0"]=max(pos["i0"],b)
        c=HC[k+1]; new=[]
        while c-ref>=box: ref+=box; new.append(1)
        while ref-c>=box: ref-=box; new.append(-1)
        for d_ in new:
            dirs.append(d_); dirs=dirs[-10:]
            if pos is not None:
                pos["br"]+=1
                if pos["br"]>=6 and b<n: out.append((pos["t"],pos["d"],pos["d"]*(O[b]-pos["e"])-SPREAD)); pos=None
            if mode=="market" and pos is None and len(dirs)>=2 and dirs[-1]==dirs[-2] and b<n:
                d=dirs[-1]; f=O[b]+d*slip; lvl=ref
                pos=dict(d=d,e=f,stop=lvl-d*3*box,tgt=lvl+d*6*box,br=0,t=T[b],i0=b)
    return out
days=(T[-1]-T[0])/np.timedelta64(1,"D")*5/7
def rep(lbl,tr):
    v=np.array([x[2] for x in tr]); a=np.array([x[2] for x in tr if x[0]<SPLIT]); b=np.array([x[2] for x in tr if x[0]>=SPLIT])
    L=np.array([x[2] for x in tr if x[1]==1]); S=np.array([x[2] for x in tr if x[1]==-1]); yrs=collections.defaultdict(list)
    for t,_,p in tr: yrs[str(t)[:4]].append(p)
    t_=v.mean()/(v.std(ddof=1)/np.sqrt(len(v)))
    print(f"{lbl:34s} {len(v)/days:4.1f}/d win={100*(v>0).mean():3.0f}% {v.mean()*K:+6.1f}₪ t={t_:+5.2f} | train {a.mean()*K:+6.1f} hold {b.mean()*K:+6.1f} | L {L.mean()*K:+6.1f} S {S.mean()*K:+6.1f} | yrs {[round(float(np.mean(x))*K,1) for k,x in sorted(yrs.items())]}")
print("Method 1 with the box sized to volatility (standard Renko practice) — stop 3 boxes, target 6 boxes, same rules. 3 years.")
for mode,slip in (("market",0.0),("pending",0.0),("pending",0.5)):
    print(f"\n--- entry: {mode}, slip {slip}$ ---")
    rep("fixed 3$ (today)",run(lambda k:3.0,slip,mode))
    for f in (0.25,0.5,1.0):
        rep(f"box = {f}×ATR(H1)",run(lambda k,f=f:max(1.0,f*atr[k]),slip,mode))

def run2(f,conf,slip):
    ref=HC[20]; dirs=[]; pos=None; out=[]
    for k in range(20,nh-1):
        box=max(1.0,f*atr[k]); a=end_idx[k]; b=end_idx[k+1]
        if pos is None and len(dirs)>=conf-1 and dirs:
            d=dirs[-1]
            ok = conf==1 or all(x==d for x in dirs[-(conf-1):])
            if ok:
                lvl=ref+d*box
                for i in range(a,b):
                    if (d==1 and Hm[i]>=lvl) or (d==-1 and Lm[i]<=lvl):
                        e=(max(lvl,O[i]) if d==1 else min(lvl,O[i]))+d*slip
                        pos=dict(d=d,e=e,stop=e-d*3*box,tgt=e+d*6*box,br=0,t=T[i],i0=i+1); break
        if pos is not None:
            for i in range(max(a,pos["i0"]),b):
                d=pos["d"]
                if (Lm[i]<=pos["stop"]) if d==1 else (Hm[i]>=pos["stop"]):
                    out.append((pos["t"],d,d*(pos["stop"]-pos["e"])-SPREAD-slip)); pos=None; break
                if (Hm[i]>=pos["tgt"]) if d==1 else (Lm[i]<=pos["tgt"]):
                    out.append((pos["t"],d,d*(pos["tgt"]-pos["e"])-SPREAD)); pos=None; break
            if pos is not None: pos["i0"]=max(pos["i0"],b)
        c=HC[k+1]; new=[]
        while c-ref>=box: ref+=box; new.append(1)
        while ref-c>=box: ref-=box; new.append(-1)
        for d_ in new:
            dirs.append(d_); dirs=dirs[-10:]
            if pos is not None:
                pos["br"]+=1
                if pos["br"]>=6 and b<n: out.append((pos["t"],pos["d"],pos["d"]*(O[b]-pos["e"])-SPREAD)); pos=None
    return out

print("\n=== grid: f×ATR, 2 bricks, pending entry (A=0.4, C=0.5) ===")
for f in (0.3,0.35,0.4,0.5):
    for sl in (0.0,0.5):
        rep(f"f={f} slip {sl}",[(t,d,p) for t,d,p in run2(f,2,sl)])
