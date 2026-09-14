"""
tools/regime_analysis.py — גילוי רג'ים תנודתיות 2026 + השפעה על שיטות 1/2/3
=================================================================================
נבנה 14/09/2026, באותו יום שנבדק.

ממצא מרכזי: ATR ממוצע על H4 עלה פי ~2.7 מ-2025H2 ל-2026H1 (25.2$→52.9$),
בעוד Efficiency Ratio (מגמתיות) נשאר יציב (~0.25) — זה רג'ים-תנודתיות, לא
אובדן-מגמה. השפעה שונה לפי שיטה: שיטה 2 (סטופ נגרר) שגשגה ב-2026H1
(+501₪, שני הטוב ביותר אי-פעם), Core A/שיטה 3 6H (סטופ קבוע 2×ATR) נחלשה
אך נשארה חיובית, Core A 4H נשבר לגמרי לראשונה.

שער-תנודתיות אדפטיבי על שיטה 3 (חסימת כניסות ב-ATR-percentile גבוה) — נבדק
ונדחה: פוגע ברווח הכולל (40-70% קיצוץ) בלי לשפר את 2026H1 באופן עקבי,
כי העסקאות הכי טובות של שיטה 3 קורות דווקא בתנודתיות גבוהה.

הרצה: python tools/regime_analysis.py   (מצפה ל-xauusd_h4.csv + xauusd_h1.csv)
"""
import csv, datetime

def load(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({"t":datetime.datetime.fromisoformat(r["timestamp"]),
                         "o":float(r["open"]),"h":float(r["high"]),
                         "l":float(r["low"]),"c":float(r["close"])})
    return sorted(rows, key=lambda x:x["t"])

def aggregate(bars,hours):
    out={}
    for x in bars:
        k=x["t"].replace(minute=0,second=0,microsecond=0)
        k=k.replace(hour=(k.hour//hours)*hours)
        g=out.setdefault(k,{"t":k,"o":x["o"],"h":x["h"],"l":x["l"],"c":x["c"]})
        g["h"]=max(g["h"],x["h"]); g["l"]=min(g["l"],x["l"]); g["c"]=x["c"]
    return [out[k] for k in sorted(out)]

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

def half(t): return f"{t.year}-H{1 if t.month<=6 else 2}"

def atr_regime_by_half(bars):
    atr=atr_series(bars,14)
    buckets={}
    for i,b in enumerate(bars):
        if atr[i] is None: continue
        buckets.setdefault(half(b["t"]),[]).append(atr[i])
    return {h: round(sum(v)/len(v),2) for h,v in sorted(buckets.items())}

def core_a(bars, gate_max_pctl=None, gate_win=120, donch_n=20, target_mult=2.0, stop_mult=2.0, max_hold=120):
    closes=[b["c"] for b in bars]; ema=ema_series(closes,50); atr=atr_series(bars,14)
    atr_pctl=[None]*len(bars)
    if gate_max_pctl is not None:
        for i in range(gate_win,len(bars)):
            window=[a for a in atr[i-gate_win:i] if a is not None]
            if window and atr[i] is not None:
                atr_pctl[i]=100*sum(1 for a in window if a<atr[i])/len(window)
    trades=[]; pos=None
    start=max(60,gate_win if gate_max_pctl is not None else 0)
    for i in range(start,len(bars)):
        c=bars[i]
        if pos is not None:
            exit_px=None
            if c["l"]<=pos["stop"]: exit_px=pos["stop"]
            elif c["h"]>=pos["target"]: exit_px=pos["target"]
            elif i-pos["i"]>=max_hold: exit_px=c["c"]
            if exit_px is not None:
                trades.append({"pnl":exit_px-pos["entry"],"t":c["t"]}); pos=None
            continue
        donch_high=max(x["h"] for x in bars[i-donch_n:i])
        cond = c["c"]>donch_high and atr[i] and c["c"]>ema[i]
        if gate_max_pctl is not None:
            cond = cond and atr_pctl[i] is not None and atr_pctl[i]<=gate_max_pctl
        if cond:
            entry=c["c"]
            pos={"i":i,"entry":entry,"stop":entry-stop_mult*atr[i],"target":entry+target_mult*atr[i]}
    return trades

def report_by_half(trades,label):
    print(f"\n{label}")
    buckets={}
    for t in trades: buckets.setdefault(half(t["t"]),[]).append(t["pnl"])
    for h,v in sorted(buckets.items()):
        print(f"  {h}:  {len(v):3d} עסקאות   {sum(v):9.1f}")
    print(f"  סה״כ: N={len(trades)}  total={sum(t['pnl'] for t in trades):.1f}")

if __name__=="__main__":
    h4=load("xauusd_h4.csv")
    h1=load("xauusd_h1.csv")
    h6=aggregate(h1,6)

    print("="*70); print("רג'ים תנודתיות — ATR ממוצע לפי חצי-שנה (H4)"); print("="*70)
    for h,v in atr_regime_by_half(h4).items(): print(f"  {h}: {v}")

    report_by_half(core_a(h4), "Core A על 4H — פילוח חצי-שנתי")
    report_by_half(core_a(h6), "Core A על 6H (=שיטה 3 חיה) — פילוח חצי-שנתי")

    print("\n"+"="*70); print("שער-תנודתיות אדפטיבי על שיטה 3 (6H) — נדחה"); print("="*70)
    base=core_a(h6)
    print(f"בסיס (ללא שער): N={len(base)}  total={sum(t['pnl'] for t in base):.1f}$")
    for pctl in [90,80,70,60,50]:
        g=core_a(h6, gate_max_pctl=pctl)
        print(f"  חוסם מעל פרצנטיל {pctl}: N={len(g)}  total={sum(t['pnl'] for t in g):.1f}$")
