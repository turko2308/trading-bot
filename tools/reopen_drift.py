"""
tools/reopen_drift.py — דריפט בשעתיים הראשונות אחרי הפתיחה היומית מחדש (20/09/2026)
==========================================================================================
אירוע = הנר הראשון אחרי הפסקת המסחר היומית (פער >=30 דק' בין 21:00-23:59 UTC, ימי חול).
קנייה בפתיחת הנר, יציאה אחרי 8 נרות M15 (שעתיים). בלי סטופ/יעד, בלי כוונון של האורך.
תוצאה: N=529, +2.78$ לעסקה, t=4.68, 63.5% הצלחה, 0/11 רבעונים שליליים, חיובי בנפרד בשני מצבי DST.
⚠️ נמצא בנתונים שנכרו בהם שעות (in-sample). דורש forward test. עלייה חדה ב-2026 (רג'ים/מגמת שור).
הרצה: python tools/reopen_drift.py ../data/xauusd_m15.csv
"""
import sys
import numpy as np, datetime, collections
sys.path.insert(0,".")
import m4_range_renko as m4
m15=m4.load_m15(sys.argv[1] if len(sys.argv)>1 else "../data/xauusd_m15.csv"); K=m4.ILS_PER_POINT
res=[]
for i in range(1,len(m15)-8):
    b=m15[i]; p=m15[i-1]
    gap=(b["t"]-p["t"]).total_seconds()/60
    if gap>=30 and 21<=b["t"].hour<=23 and b["t"].weekday()<5:   # weekday daily reopen after the break
        seg=m15[i:i+8]
        if (seg[-1]["t"]-seg[0]["t"]).total_seconds()!=105*60: continue
        r=seg[-1]["c"]-seg[0]["o"]; res.append((b["t"],r,b["t"].hour))
r=np.array([x[1] for x in res]); n=len(r)
print("reopen events (weekday, gap>=30m, 21-23 UTC): N=%d"%n, "reopen hours:",dict(collections.Counter(x[2] for x in res)))
print("long first 2h after reopen: mean %+.2f$ t=%.2f hit=%.1f%% median %+.2f"%(r.mean(),r.mean()/(r.std(ddof=1)/np.sqrt(n)),100*(r>0).mean(),np.median(r)))
for hh in sorted(set(x[2] for x in res)):
    v=np.array([x[1] for x in res if x[2]==hh]); print(f"  reopen at {hh}:00 UTC N={len(v)} mean {v.mean():+.2f}$ t={v.mean()/(v.std(ddof=1)/np.sqrt(len(v))):+.2f}")
by=collections.defaultdict(list)
for t,v,_ in res: by[t.year].append(v)
print("by year:",{y:round(float(np.mean(v)),2) for y,v in sorted(by.items())})
q=collections.defaultdict(list)
for t,v,_ in res: q[(t.year,(t.month-1)//3+1)].append(v)
print("negative quarters:",sum(1 for v in q.values() if np.mean(v)<0),"of",len(q))
srt=np.sort(r); print("top-5 share of total: %.0f%%"%(100*srt[-5:].sum()/r.sum()))
for c in [0.77,1.2,1.5,2.0]: print(f"cost {c}$: {(r-c).mean()*K:+.2f}₪/trade")
