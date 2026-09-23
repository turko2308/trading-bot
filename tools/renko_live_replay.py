"""
tools/renko_live_replay.py — הרצה חוזרת של כל איתות חי של שיטה 1 (Renko) על מחירי M15 אמיתיים (23/09/2026)
מודד: איחור כניסה (מחיר בזמן ההודעה מול רמת הכניסה), ורווח אמיתי עם סטופ/יעד תוך-נרי — מול ה-pnl בגיסט,
שמחושב במחירי לבנים ונבדק רק בסגירת שעה.
הרצה מתוך tools/: python renko_live_replay.py /path/to/bot_data.json   (קורא ../data/xauusd_m15.csv)
"""
import json, datetime, numpy as np, pandas as pd, collections
d=json.load(open(__import__("sys").argv[1] if len(__import__("sys").argv)>1 else "bot_data.json"))
m=pd.read_csv("../data/xauusd_m15.csv"); m["t"]=pd.to_datetime(m["timestamp"],utc=True).dt.tz_localize(None)
T=m.t.values; O=m.open.values; H=m.high.values; L=m.low.values; C=m.close.values
IL=datetime.timedelta(hours=3)     # ישראל קיץ = UTC+3
rows=[]
for s in d["renko_signals"]:
    if s["status"]!="closed": continue
    t0=datetime.datetime.fromisoformat(s["time"])-IL          # זמן שליחת האיתות ב-UTC
    t1=datetime.datetime.fromisoformat(s["close_time"])-IL
    dr=1 if s["direction"]=="קנייה" else -1
    i=np.searchsorted(T,np.datetime64(t0))                   # הבר הראשון שמתחיל אחרי שליחת האיתות
    j=np.searchsorted(T,np.datetime64(t1))
    if i>=len(T) or j>=len(T) or j<=i: continue
    fill=O[i]                                                # המחיר האמיתי כשאתה יכול להיכנס
    gap=dr*(fill-s["entry"])                                 # + = נכנסת גרוע מהרמה שבהודעה
    def sim(stop,tgt):
        for k in range(i,j):
            hs = L[k]<=stop if dr==1 else H[k]>=stop
            ht = H[k]>=tgt if dr==1 else L[k]<=tgt
            if hs: return dr*(stop-fill),"stop"              # פסימי: סטופ קודם אם שניהם באותו בר
            if ht: return dr*(tgt-fill),"target"
        return dr*(C[j-1]-fill),"timeout"
    pa,ra=sim(s["stop"],s["target"])                         # (א) הרמות בדיוק כמו בהודעה
    pb,rb=sim(fill-dr*9,fill+dr*18)                          # (ב) אותם מרחקים מהמילוי בפועל
    rows.append(dict(id=s["id"],day=s["time"][:10],gap=gap,live=s["pnl"],real_a=pa-0.77,res_a=ra,real_b=pb-0.77,res_b=rb,live_reason=s["reason"]))
df=pd.DataFrame(rows)
print(f"איתותים שנבדקו: {len(df)} מתוך {sum(1 for s in d['renko_signals'] if s['status']=='closed')}")
print(f"\nאיחור כניסה (מחיר אמיתי בזמן ההודעה מול רמת הכניסה בהודעה):")
print(f"  ממוצע {df.gap.mean():+.2f}$ | חציון {df.gap.median():+.2f}$ | גרוע מ-5$: {int((df.gap>5).sum())} | טוב יותר מהרמה: {int((df.gap<0).sum())}")
print(f"\n{'':32s}{'הצלחה':>8s}{'$ לעסקה':>10s}{'סה\"כ $':>10s}")
for lab,col in (("בגיסט (מחירי לבנים)","live"),("אמיתי: רמות ההודעה","real_a"),("אמיתי: מרחקים מהמילוי","real_b")):
    v=df[col]; print(f"  {lab:30s}{100*(v>0).mean():7.0f}%{v.mean():+10.2f}{v.sum():+10.1f}")
print("\nסיבות יציאה בפועל (רמות ההודעה):",dict(collections.Counter(df.res_a)))
print("\nלפי יום (אמיתי, רמות ההודעה):")
for k,g in df.groupby("day"): print(f"  {k}: N={len(g):2d} gist {g.live.sum():+7.1f}$ | real {g.real_a.sum():+7.1f}$ | avg gap {g.gap.mean():+.2f}$")

