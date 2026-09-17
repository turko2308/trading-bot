"""
tools/randomness_test.py — מבחן האקראיות: האם התזמון של האיתות נושא מידע?
================================================================================================
נבנה 16/09/2026. השאלה שלא נשאלה שנתיים: האם האיתות עדיף על כניסה בזמן אקראי?
כל המבחנים הקודמים (robustness, MC, Bootstrap, יציבות, חפיפה, CPCV) בודקים אם
התוצאה יציבה — אף אחד לא בודק אם התזמון נושא מידע בכלל.

שרשרת מעודכנת: בר-ביצוע → אקראיות → plateau → slippage → DSR/PBO → CPCV

השיטה: מריצים את אותו מנוע יציאה בדיוק, אבל עם כניסות בזמנים אקראיים וכיוון
אקראי, 200-500 פעמים. אם התוצאה האמיתית לא בולטת מהתפלגות האקראית (p>0.05),
האיתות שקול להימור.

תוצאות 16/09:
  שיטה 3 (6H)   אמיתי +12.60$  אקראי  +0.10$  p=0.003  ✅
  שיטה 2 (4H)   אמיתי +47.86$  אקראי +12.36$  p=0.014  ✅
  שיטה 3 (4H)   אמיתי  +5.24$  אקראי  -0.30$  p=0.095  ❌  (הושבת 14/09)
  שיטה 1 (כל הגרסאות התוך-יומיות)                       ❌  נסגרה

הרצה: python tools/randomness_test.py   (מצפה ל-../data/xauusd_h1.csv)
"""
import csv, datetime, sys
import numpy as np

def load_h1(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            ts=r["timestamp"].split("+")[0].strip()
            rows.append({"t":datetime.datetime.fromisoformat(ts),"o":float(r["open"]),
                         "h":float(r["high"]),"l":float(r["low"]),"c":float(r["close"])})
    return sorted(rows,key=lambda x:x["t"])

def aggregate(bars,hours):
    out=[];cur=None
    for b in bars:
        slot=b["t"].replace(minute=0,second=0,microsecond=0)
        slot=slot.replace(hour=(slot.hour//hours)*hours)
        if cur is None or cur["t"]!=slot:
            if cur: out.append(cur)
            cur={"t":slot,"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"]}
        else:
            cur["h"]=max(cur["h"],b["h"]); cur["l"]=min(cur["l"],b["l"]); cur["c"]=b["c"]
    if cur: out.append(cur)
    return out

def atr_series(bars,n=14):
    trs=[]
    for i in range(len(bars)):
        if i==0: trs.append(bars[i]["h"]-bars[i]["l"]); continue
        pc=bars[i-1]["c"]
        trs.append(max(bars[i]["h"]-bars[i]["l"],abs(bars[i]["h"]-pc),abs(bars[i]["l"]-pc)))
    out=[None]*len(bars)
    for i in range(n-1,len(bars)): out[i]=sum(trs[i-n+1:i+1])/n
    return out

def ema_series(closes,span):
    out=[None]*len(closes); k=2/(span+1); e=closes[0]; out[0]=e
    for i in range(1,len(closes)): e=closes[i]*k+e*(1-k); out[i]=e
    return out

def core_a(bars, entries=None, max_hold=120):
    """שיטה 3. entries=None ⇒ האיתותים האמיתיים; אחרת רשימת (index, direction)."""
    c=[b["c"] for b in bars]; h=[b["h"] for b in bars]; l=[b["l"] for b in bars]
    n=len(bars); atr=atr_series(bars); ema=ema_series(c,50)
    if entries is None:
        entries=[]
        for i in range(60,n-1):
            if c[i]>max(h[i-20:i]) and c[i]>ema[i]: entries.append((i,1))
            elif c[i]<min(l[i-20:i]) and c[i]<ema[i]: entries.append((i,-1))
    trades=[]; busy=-1
    for i,d in entries:
        if i<=busy or i>=n-1 or atr[i] is None: continue
        e=c[i]; stop=e-d*2*atr[i]; tgt=e+d*2*atr[i]
        for j in range(i+1,min(i+max_hold,n)):
            ex=None
            if d==1:
                if l[j]<=stop: ex=stop
                elif h[j]>=tgt: ex=tgt
            else:
                if h[j]>=stop: ex=stop
                elif l[j]<=tgt: ex=tgt
            if ex is None and j-i>=max_hold: ex=c[j]
            if ex is not None:
                trades.append((ex-e)*d); busy=j; break
    return np.array(trades)

def randomness_test(bars, engine=core_a, n_sims=400, seed=42):
    real=engine(bars)
    if len(real)<20: return None
    n=len(bars); rng=np.random.default_rng(seed); means=[]
    for _ in range(n_sims):
        idx=sorted(rng.choice(range(60,n-130),size=min(len(real)*3,n-200),replace=False))
        ent=[(i,int(rng.choice([1,-1]))) for i in idx]
        rt=engine(bars,entries=ent)
        if len(rt)>0: means.append(rt.mean())
    means=np.array(means)
    return {"n":len(real),"real":real.mean(),"rand_median":float(np.median(means)),
            "p":float(np.mean(means>=real.mean()))}

if __name__=="__main__":
    path=sys.argv[1] if len(sys.argv)>1 else "../data/xauusd_h1.csv"
    h1=load_h1(path)
    print(f"נטען: {len(h1)} נרות H1, {h1[0]['t'].date()} עד {h1[-1]['t'].date()}\n")
    print(f"{'טווח':>6s} {'N':>5s} {'אמיתי$':>9s} {'אקראי$':>9s} {'p':>7s}  תוצאה")
    print("="*62)
    for hrs in [12,8,6,4,3,2,1]:
        bars=aggregate(h1,hrs) if hrs>1 else h1
        res=randomness_test(bars)
        if not res: continue
        mark="✅ שונה מאקראי" if res["p"]<0.05 else ("⚠️ גבולי" if res["p"]<0.10 else "❌ כמו אקראי")
        print(f"{hrs:5d}H {res['n']:5d} {res['real']:+9.2f} {res['rand_median']:+9.2f} {res['p']:7.3f}  {mark}")
