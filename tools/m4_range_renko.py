"""
tools/m4_range_renko.py — שיטה 4: פריצת טווח מתגלגל מסוננת ב-Renko
================================================================================================
נבנה 16/09/2026. הכלי שממנו נגזרה שיטה 4 ב-trading_bot.py (3.11.0).

הרקע: שיטה 1 נסגרה במדידה — כל גרסה תוך-יומית נמדדה כזהה לאקראי
(tools/randomness_test.py, DECISIONS §31). הכיוון הזה נמצא בחיפוש מבנה-זמן,
והרעיון המרכזי — סינון Renko — הוא של איזק: "Renko לא טועה בכיוון, הכניסות
דפוקות". הנתונים אישרו זאת חד-משמעית.

המנגנון:
  1. בכל שעה: טווח 2 השעות הקודמות (שיא/שפל).
  2. פריצת גבול הטווח בחלון 07:00-20:00 UTC = איתות.
  3. סינון: רק אם Renko (קופסה 20$, אישור 2 לבנים) מסכים עם כיוון הפריצה.
  4. יעד = סטופ = גודל הטווח. אופק 4 שעות.

למה זה עובד: ה-R (גודל הטווח) גדול מספיק שהספרד (0.77$) זניח, ו-Renko מספק
את פילטר הכיוון שלפריצה עצמה אין. **בלי Renko התוצאה שלילית (-1.3 ש"ח לעסקה)
— הסינון הוא כל האפקט, לא שיפור.**

אימות מלא (נתוני M15, 2024-07/2026):
  בר-ביצוע:   כניסה בגבול הטווח, יציאה מול high/low ברזולוציית M15 ✅
  אקראיות:    כיוון p=0.000  ·  Renko מעורבל p=0.000 ✅
  plateau:    36/36 וריאציות חיוביות (+3.85$ עד +7.75$ לעסקה)
  Bootstrap:  100%  ·  MC DD:רווח 1:8.8  ·  3/3 שנים חיוביות
  PBO:        0.214 ⚠️ גבולי — לעומת 0.841-0.885 שנכשלו בגרסאות קודמות

⚠️ בגלל ה-PBO: ערכי אמצע בלבד, בלי אופטימיזציה. הציפייה היא לקצה התחתון
   של הטווח (~3.85-5$ לעסקה), לא לערך של הכיול הטוב ביותר.
⚠️ 2024 חלש (+0.39$/עסקה) מול 2026 (+7.32$) — ייתכן שהאפקט תלוי-רג'ים.
⚠️ מסנן נפח שיפר בבקטסט (58%→61% הצלחה) אך אינו פעיל בבוט: הבקטסט השתמש
   ב-tick-volume של Dukascopy, והנפח של Twelve Data הוא מדד אחר. הבוט אוסף
   נפח ללוג כדי לאמת זאת בעוד כמה שבועות.

הרצה: python tools/m4_range_renko.py   (מצפה ל-../data/xauusd_m15.csv)
"""
import csv, datetime, sys, bisect
from collections import defaultdict
import numpy as np

SPREAD = 0.77
ILS_PER_POINT = 4.54   # 1.5oz × ~3.03

def load_m15(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            ts=r["timestamp"].split("+")[0].strip()
            rows.append({"t":datetime.datetime.fromisoformat(ts),"o":float(r["open"]),
                         "h":float(r["high"]),"l":float(r["low"]),"c":float(r["close"]),
                         "v":float(r["volume"]) if r.get("volume") else None})
    return sorted(rows,key=lambda x:x["t"])

def to_h1(m15):
    out=[];cur=None
    for b in m15:
        slot=b["t"].replace(minute=0,second=0,microsecond=0)
        if cur is None or cur["t"]!=slot:
            if cur: out.append(cur)
            cur={"t":slot,"h":b["h"],"l":b["l"],"c":b["c"],"v":b["v"] or 0}
        else:
            cur["h"]=max(cur["h"],b["h"]); cur["l"]=min(cur["l"],b["l"])
            cur["c"]=b["c"]; cur["v"]+=(b["v"] or 0)
    if cur: out.append(cur)
    return out

def renko_dirs(h1, box, confirm):
    ref=h1[0]["c"]; dirs=[]; out={}
    for b in h1:
        while b["c"]-ref>=box: ref+=box; dirs.append(1)
        while ref-b["c"]>=box: ref-=box; dirs.append(-1)
        v=0
        if len(dirs)>=confirm:
            r=dirs[-confirm:]
            v=1 if all(x==1 for x in r) else (-1 if all(x==-1 for x in r) else 0)
        out[b["t"]]=v
    return out

def backtest(m15, range_bars=2, box=20.0, confirm=2, tgt_m=1.0, stp_m=1.0,
             hold_h=4, start_utc=7, end_utc=20, vol_mult=0.0, use_renko=True):
    h1=to_h1(m15); rk=renko_dirs(h1,box,confirm)
    by_t={b["t"]:i for i,b in enumerate(h1)}
    m15_times=[b["t"] for b in m15]
    trades=[]; busy=None
    vols=[b["v"] for b in h1]
    for i in range(range_bars+25, len(h1)):
        bar=h1[i]; t=bar["t"]
        if busy is not None and t<busy: continue
        if not (start_utc<=t.hour<end_utc): continue
        if vol_mult>0:
            w=[v for v in vols[max(0,i-24):i] if v]
            if not w or not bar["v"] or bar["v"]<vol_mult*sorted(w)[len(w)//2]: continue
        w=h1[i-range_bars:i]
        hi=max(b["h"] for b in w); lo=min(b["l"] for b in w); R=hi-lo
        if R<=0: continue
        d=1 if bar["h"]>hi else (-1 if bar["l"]<lo else 0)
        if not d: continue
        if use_renko and rk.get(t,0)!=d: continue
        e=hi if d==1 else lo
        # יציאה ברזולוציית M15 — high/low אמיתיים (bisect, לא סריקה מלאה)
        import bisect
        lo_i=bisect.bisect_right(m15_times,t)
        hi_i=bisect.bisect_right(m15_times,t+datetime.timedelta(hours=hold_h))
        seg=m15[lo_i:hi_i]
        if len(seg)<4: continue
        out=None
        for b in seg:
            up=(b["h"]-e)*d if d==1 else (e-b["l"])
            dn=(b["l"]-e)*d if d==1 else (e-b["h"])
            if dn<=-stp_m*R: out=-stp_m*R; break
            if up>=tgt_m*R: out=tgt_m*R; break
        if out is None: out=(seg[-1]["c"]-e)*d
        trades.append({"pnl":out-SPREAD,"t":t,"dir":d,"R":R})
        busy=t+datetime.timedelta(hours=hold_h)
    return trades

if __name__=="__main__":
    path=sys.argv[1] if len(sys.argv)>1 else "../data/xauusd_m15.csv"
    m15=load_m15(path)
    print(f"נטען: {len(m15)} נרות M15, {m15[0]['t'].date()} עד {m15[-1]['t'].date()}\n")
    days=(m15[-1]["t"]-m15[0]["t"]).days

    for lab,kw in [("שיטה 4 (כמו בבוט)",{}),
                   ("בלי סינון Renko",{"use_renko":False}),
                   ("עם מסנן נפח 1.2× (לא פעיל בבוט)",{"vol_mult":1.2})]:
        tr=backtest(m15,**kw)
        if not tr: print(f"{lab}: אין עסקאות"); continue
        r=np.array([x["pnl"] for x in tr])
        by_year=defaultdict(list)
        for x in tr: by_year[x["t"].year].append(x["pnl"])
        yrs={y:round(float(np.mean(v))*ILS_PER_POINT,1) for y,v in sorted(by_year.items())}
        print(f"{lab}")
        print(f"  N={len(r)} ({len(r)/days:.2f}/יום)  הצלחה={100*(r>0).mean():.1f}%  "
              f"לעסקה={r.mean():+.2f}$ = {r.mean()*ILS_PER_POINT:+.1f} ש\"ח  "
              f"ליום={len(r)/days*r.mean()*ILS_PER_POINT:+.2f} ש\"ח")
        print(f"  שנים (ש\"ח לעסקה): {yrs}")
        print(f"  עלות: " + "  ".join(f"{c}$→{(r-(c-SPREAD)).mean():+.2f}" for c in [0.77,2.5,4.0]))
        print()
