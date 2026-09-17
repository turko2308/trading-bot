"""
tools/range_breakout.py — פריצת טווח לילי מסוננת ב-Renko · מועמד לשיטה 1
================================================================================================
נבנה 16/09/2026. הרקע: כל גרסאות שיטה 1 התוך-יומיות נמדדו כזהות לאקראי (ראה
tools/randomness_test.py ו-DECISIONS סעיף 31). הכיוון הזה נמצא בחיפוש אחר מבנה
זמן במקום אינדיקטורים, והוא הראשון שעבר את מבחן האקראיות.

המנגנון:
  1. מודדים שיא/שפל של 00:00-07:00 UTC (אסיה). טווח ממוצע ~31.6$.
  2. נכנסים כשגבול הטווח נפרץ, בחלון 07:00-20:00 UTC.
  3. סינון: רק אם Renko (קופסה 20$, אישור 2 לבנים) מסכים עם כיוון הפריצה.
     — הרעיון של איזק: "Renko לא טועה בכיוון, הכניסות דפוקות". הנתונים אישרו.
  4. יעד = סטופ = גודל הטווח. סגירה בסוף החלון.

למה זה עובד איפה שכל השאר נכשל: הטווח הלילי (31.6$) גדול מספיק שהספרד (0.77$)
זניח. כל הניסיונות התוך-יומיים הקודמים עבדו עם יעדים של 5-15$.

תפעולית: 100% מהכניסות ב-10:00-23:00 שעון ישראל, 46% מהן ב-10:00 בבוקר בדיוק
(פתיחת לונדון). ~0.37 עסקאות ליום.

אימות (2024-07/2026, נתוני M15):
  אקראיות כיוון:  אמיתי +6.43$ מול -0.85$  p=0.003  ✅
  Renko מעורבל:   אמיתי +6.43$ מול +2.38$  p=0.023  ✅ הסינון נושא מידע
  Bootstrap:      99.4%
  plateau:        24/24 וריאציות חיוביות (3.13$ עד 7.40$)
  שנים:           2024 +2.94 · 2025 +6.08 · 2026 +9.33  (3/3)
  שני הכיוונים:   שורט +8.00 · לונג +5.46  ← לא תלוי במגמת שור
  עמידות:         חיובי עד 6$/עסקה (פי 8 מהספרד)
  DD:רווח:        1:2.1
  🔴 PBO = 0.841  נכשל

⚠️ משמעות ה-PBO: האפקט אמיתי, הכיול לא. הפער בין הווריאציה הטובה לגרועה (פי 2.4)
הוא רעש. להשתמש בערכי אמצע שמרניים בלבד, בלי אופטימיזציה, ולצפות לקצה התחתון
(~3-4$ לעסקה = 14-18 ש"ח), לא לממוצע שנמדד.
⚠️ סינון "טווח >15$" נראה מצוין (+17.4 ש"ח) — לא להשתמש, זו בדיוק הבחירה ש-PBO מזהיר ממנה.

הרצה: python tools/range_breakout.py   (מצפה ל-../data/xauusd_m15.csv)
"""
import csv, datetime, sys
from collections import defaultdict
import numpy as np

SPREAD = 0.77

def load_m15(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            ts=r["timestamp"].split("+")[0].strip()
            rows.append({"t":datetime.datetime.fromisoformat(ts),"o":float(r["open"]),
                         "h":float(r["high"]),"l":float(r["low"]),"c":float(r["close"])})
    return sorted(rows,key=lambda x:x["t"])

def renko_states(bars_h1, box=20.0, confirm=2):
    """מחזיר {timestamp: כיוון} לפי סגירות שעתיות."""
    ref=bars_h1[0]["c"]; dirs=[]; out={}
    for b in bars_h1:
        c=b["c"]
        while c-ref>=box: ref+=box; dirs.append(1)
        while ref-c>=box: ref-=box; dirs.append(-1)
        v=0
        if len(dirs)>=confirm:
            r=dirs[-confirm:]
            v=1 if all(x==1 for x in r) else (-1 if all(x==-1 for x in r) else 0)
        out[b["t"]]=v
    return out

def to_h1(m15):
    out=[];cur=None
    for b in m15:
        slot=b["t"].replace(minute=0,second=0,microsecond=0)
        if cur is None or cur["t"]!=slot:
            if cur: out.append(cur)
            cur={"t":slot,"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"]}
        else:
            cur["h"]=max(cur["h"],b["h"]); cur["l"]=min(cur["l"],b["l"]); cur["c"]=b["c"]
    if cur: out.append(cur)
    return out

def backtest(m15, box=20.0, range_end=7, trade_end=20, tgt_m=1.0, stp_m=1.0, use_renko=True):
    h1=to_h1(m15); rs=renko_states(h1,box)
    rs_times=sorted(rs)
    by_day=defaultdict(list)
    for b in m15: by_day[b["t"].date()].append(b)

    def renko_at(t):
        lo,hi=0,len(rs_times)-1; best=None
        while lo<=hi:
            mid=(lo+hi)//2
            if rs_times[mid]<=t: best=rs_times[mid]; lo=mid+1
            else: hi=mid-1
        return rs[best] if best else 0

    trades=[]
    for day in sorted(by_day):
        g=by_day[day]
        rw=[b for b in g if 0<=b["t"].hour<range_end]
        rest=[b for b in g if range_end<=b["t"].hour<trade_end]
        if len(rw)<12 or len(rest)<20: continue
        hi=max(b["h"] for b in rw); lo=min(b["l"] for b in rw); R=hi-lo
        if R<=0: continue
        e=None
        for k,b in enumerate(rest):
            if b["h"]>hi: e,d,idx=hi,1,k; break
            if b["l"]<lo: e,d,idx=lo,-1,k; break
        if e is None: continue
        if use_renko and renko_at(rest[idx]["t"])!=d: continue
        after=rest[idx:]; T=tgt_m*R; S=stp_m*R; out=None
        for b in after:
            up=(b["h"]-e)*d if d==1 else (e-b["l"])
            dn=(b["l"]-e)*d if d==1 else (e-b["h"])
            if dn<=-S: out=-S; break
            if up>=T: out=T; break
        if out is None: out=(after[-1]["c"]-e)*d
        trades.append({"pnl":out-SPREAD,"date":day,"dir":d,"R":R,"t":rest[idx]["t"]})
    return trades

if __name__=="__main__":
    path=sys.argv[1] if len(sys.argv)>1 else "../data/xauusd_m15.csv"
    m15=load_m15(path)
    print(f"נטען: {len(m15)} נרות M15, {m15[0]['t'].date()} עד {m15[-1]['t'].date()}\n")

    for label,use in [("בסיס (ללא סינון Renko)",False),("עם סינון Renko 20$",True)]:
        tr=backtest(m15,use_renko=use)
        r=np.array([x["pnl"] for x in tr])
        days=(m15[-1]["t"]-m15[0]["t"]).days
        by_year=defaultdict(list)
        for x in tr: by_year[x["date"].year].append(x["pnl"])
        yrs={y:round(float(np.mean(v)),2) for y,v in sorted(by_year.items())}
        print(f"{label}")
        print(f"  N={len(r)} ({len(r)/days:.2f}/יום)  הצלחה={100*(r>0).mean():.1f}%  "
              f"לעסקה={r.mean():+.2f}$ = {r.mean()*4.54:+.1f} ש\"ח")
        print(f"  שנים: {yrs}")
        print(f"  עלות: " + "  ".join(f"{c}$→{(r-(c-SPREAD)).mean():+.2f}" for c in [0.77,2.5,4.0,6.0]))
        print()

    tr=backtest(m15,use_renko=True)
    from collections import Counter
    il=Counter((x["t"]+datetime.timedelta(hours=3)).hour for x in tr)
    print("שעת כניסה (שעון ישראל):", dict(sorted(il.items())))
