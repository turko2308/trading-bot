import datetime, statistics
from be import load
h4=load("xauusd_h4.csv")
OZ=0.75; IPP=OZ*2.99; CARRY=6.0
CPD=6; ew=20*CPD; tw=3*CPD

# לכל עסקה: כמה היא הגיעה מקסימום, ומה קרה בסוף
pos=None; rows=[]
for i in range(ew,len(h4)):
    c=h4[i]
    hh=max(x["h"] for x in h4[i-ew:i]); ll=min(x["l"] for x in h4[i-ew:i])
    if pos is None:
        d="long" if c["c"]>hh else ("short" if c["c"]<ll else None)
        if d:
            t_lo=min(x["l"] for x in h4[max(0,i-tw):i]); t_hi=max(x["h"] for x in h4[max(0,i-tw):i])
            tr=t_lo if d=="long" else t_hi
            if abs(c["c"]-tr)<1e-6: continue
            pos={"dir":d,"entry":c["c"],"trail":tr,"et":c["t"],"mfe":0.0,"be":False,"d150":None}
        continue
    t_lo=min(x["l"] for x in h4[max(0,i-tw):i]); t_hi=max(x["h"] for x in h4[max(0,i-tw):i])
    pos["trail"]=max(pos["trail"],t_lo) if pos["dir"]=="long" else min(pos["trail"],t_hi)
    if pos["be"]:
        bp=pos["entry"]+3 if pos["dir"]=="long" else pos["entry"]-3
        pos["trail"]=max(pos["trail"],bp) if pos["dir"]=="long" else min(pos["trail"],bp)
    mfe=(c["h"]-pos["entry"]) if pos["dir"]=="long" else (pos["entry"]-c["l"])
    if mfe>pos["mfe"]: pos["mfe"]=mfe
    if pos["d150"] is None and pos["mfe"]>=150: pos["d150"]=c["t"]
    ex=None
    if pos["dir"]=="long" and c["l"]<=pos["trail"]: ex=min(pos["trail"],c["o"])
    elif pos["dir"]=="short" and c["h"]>=pos["trail"]: ex=max(pos["trail"],c["o"])
    if ex is not None:
        pts=(ex-pos["entry"]) if pos["dir"]=="long" else (pos["entry"]-ex)
        rows.append({"mfe":pos["mfe"],"final":pts,
                     "days":(c["t"]-pos["et"]).total_seconds()/86400,
                     "d150":pos["d150"],"et":pos["et"],"ct":c["t"]})
        pos=None; continue
    if not pos["be"] and pos["mfe"]>=25: pos["be"]=True

print(f"סה\"כ עסקאות: {len(rows)}\n")
big=[r for r in rows if r["mfe"]>=150]
print(f"=== עסקאות שהגיעו ל-+150$ או יותר: {len(big)} מתוך {len(rows)} ===")
print(f"{'שיא':>7}{'סוף':>8}{'ויתור':>8}{'ימים':>7}")
print("-"*32)
for r in sorted(big,key=lambda x:-x["mfe"]):
    print(f"{r['mfe']:>7.0f}{r['final']:>8.0f}{r['final']-r['mfe']:>8.0f}{r['days']:>7.1f}")
if big:
    giveback=[r["mfe"]-r["final"] for r in big]
    kept=[100*r["final"]/r["mfe"] for r in big]
    print(f"\nויתור ממוצע מהשיא: {statistics.mean(giveback):.0f}$ | חציון {statistics.median(giveback):.0f}$")
    print(f"שמרו בממוצע {statistics.mean(kept):.0f}% מהשיא | חציון {statistics.median(kept):.0f}%")
    print(f"כמה סיימו מעל 150: {sum(1 for r in big if r['final']>=150)}/{len(big)}")
    # מה היה קורה אם סוגרים מיד ב-150
    a=sum((r["final"]-0.77)*IPP - CARRY*r["days"] for r in big)
    b=sum((150-0.77)*IPP - CARRY*((r["d150"]-r["et"]).total_seconds()/86400 if r["d150"] else r["days"]) for r in big)
    print(f"\nלתת לנגרר לרוץ: {a:>7.0f} ש\"ח")
    print(f"לסגור מיד ב-150$: {b:>7.0f} ש\"ח")

print(f"\n=== תדירות איתותים ===")
gaps=[(rows[i]['et']-rows[i-1]['ct']).total_seconds()/86400 for i in range(1,len(rows))]
print(f"המתנה בין סגירה לכניסה הבאה: ממוצע {statistics.mean(gaps):.1f} ימים | חציון {statistics.median(gaps):.1f} | הכי ארוך {max(gaps):.0f}")
print(f"עסקאות בשנה: {len(rows)/2.5:.0f}")
