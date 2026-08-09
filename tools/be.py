import random, statistics
from be import load, sim, maxdd
random.seed(42)
h4 = load("xauusd_h4.csv")

def mc(det, iters=2000):
    p = [t["pnl"] for t in det]
    dds, ends = [], []
    for _ in range(iters):
        random.shuffle(p)
        eq=peak=0.0; dd=0.0
        for x in p:
            eq+=x; peak=max(peak,eq); dd=min(dd,eq-peak)
        dds.append(dd); ends.append(eq)
    dds.sort()
    return statistics.median(dds), dds[int(0.05*len(dds))], dds[0]

def boot(det, iters=2000):
    p=[t["pnl"] for t in det]; n=len(p); tot=[]
    for _ in range(iters):
        s=sum(random.choice(p) for _ in range(n)); tot.append(s)
    tot.sort()
    return sum(1 for x in tot if x>0)/len(tot)*100, tot[int(0.05*len(tot))], tot[int(0.95*len(tot))]

print("=== Monte Carlo (2,000 ערבובי סדר) — ירידה מקסימלית ===")
print(f"{'וריאנט':<16}{'חציון':>10}{'5% גרוע':>10}{'הכי גרוע':>11}")
print("-"*48)
for trig,off in [(None,0),(25,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    d = sim(h4, be_trigger=trig, be_offset=off)["detail"]
    m,p5,w = mc(d)
    print(f"{name:<16}{m:>10.0f}{p5:>10.0f}{w:>11.0f}")

print("\n=== Bootstrap (2,000 דגימות) — הסתברות לרווח ===")
print(f"{'וריאנט':<16}{'P(רווח)':>10}{'5%':>9}{'95%':>9}")
print("-"*46)
for trig,off in [(None,0),(25,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    d = sim(h4, be_trigger=trig, be_offset=off)["detail"]
    pr,lo,hi = boot(d)
    print(f"{name:<16}{pr:>9.1f}%{lo:>9.0f}{hi:>9.0f}")

print("\n=== החלקה 10$ + Monte Carlo (התרחיש האמיתי שלך) ===")
for trig,off in [(None,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    r = sim(h4, slippage=10, be_trigger=trig, be_offset=off)
    m,p5,w = mc(r["detail"]); pr,lo,hi = boot(r["detail"])
    print(f"{name:<16} רווח {r['pnl']:>5.0f} | P(רווח) {pr:>5.1f}% | MC חציון {m:>6.0f} | גרוע {w:>6.0f}")

import csv, datetime, statistics

SPREAD_PTS = 0.77
RISK_ILS = 40.0
CPD = 6  # candles per day 4h

def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": datetime.datetime.fromisoformat(r["timestamp"]),
                "o": float(r["open"]), "h": float(r["high"]),
                "l": float(r["low"]), "c": float(r["close"]),
            })
    rows.sort(key=lambda x: x["t"])
    return rows

def sim(h4, entry_days=20, trail_days=3, risk_ils=RISK_ILS,
        slippage=0.0, be_trigger=None, be_offset=0.0):
    """be_trigger: after price moves X$ in favor (intrabar MFE), move stop to
       entry +/- be_offset. Stop only tightens."""
    ew = entry_days * CPD
    tw = trail_days * CPD
    pos = None
    closed = []

    def close(p, exit_px, when, open_end=False):
        pts = (exit_px - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_px)
        pnl = (pts - SPREAD_PTS) * p["ipp"]
        closed.append({"pnl": pnl, "pts": pts, "dir": p["dir"],
                       "et": p["et"], "ct": when,
                       "days": max(0.0, (when - p["et"]).total_seconds()/86400.0),
                       "be_hit": p.get("be_hit", False), "open_end": open_end})

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i-ew:i])
        ll = min(x["l"] for x in h4[i-ew:i])
        if pos is None:
            d = "long" if c["c"] > hh else ("short" if c["c"] < ll else None)
            if d:
                entry = c["c"] + slippage if d == "long" else c["c"] - slippage
                t_lo = min(x["l"] for x in h4[max(0,i-tw):i])
                t_hi = max(x["h"] for x in h4[max(0,i-tw):i])
                trail = t_lo if d == "long" else t_hi
                sp = abs(entry - trail)
                if sp < 1e-6:
                    continue
                pos = {"dir": d, "entry": entry, "trail": trail,
                       "ipp": risk_ils/sp, "et": c["t"], "be_hit": False}
            continue

        # trail update (only tightens)
        t_lo = min(x["l"] for x in h4[max(0,i-tw):i])
        t_hi = max(x["h"] for x in h4[max(0,i-tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        # breakeven check BEFORE exit check, using this bar's excursion.
        # conservative: if BE triggers on same bar, the BE stop is only armed
        # for the *next* bar (can't know intrabar order).
        armed_be = pos["be_hit"]
        if armed_be:
            be_px = pos["entry"] + be_offset if pos["dir"] == "long" else pos["entry"] - be_offset
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

        exit_px = None
        if pos["dir"] == "long" and c["l"] <= pos["trail"]:
            exit_px = min(pos["trail"], c["o"])
        elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
            exit_px = max(pos["trail"], c["o"])

        if exit_px is not None:
            close(pos, exit_px, c["t"])
            pos = None
            continue

        if be_trigger is not None and not pos["be_hit"]:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= be_trigger:
                pos["be_hit"] = True

    if pos is not None:
        close(pos, h4[-1]["c"], h4[-1]["t"], open_end=True)

    n = len(closed)
    wins = [x for x in closed if x["pnl"] > 0]
    return {"n": n, "wr": 100*len(wins)/n if n else 0,
            "pnl": sum(x["pnl"] for x in closed),
            "per": sum(x["pnl"] for x in closed)/n if n else 0,
            "avg_win": statistics.mean([x["pnl"] for x in wins]) if wins else 0,
            "avg_loss": statistics.mean([x["pnl"] for x in closed if x["pnl"]<=0]) or 0,
            "worst": min((x["pnl"] for x in closed), default=0),
            "avg_days": statistics.mean([x["days"] for x in closed]) if n else 0,
            "be_hits": sum(1 for x in closed if x["be_hit"]),
            "detail": closed}

def maxdd(det):
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(det, key=lambda x: x["ct"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd

if __name__ == "__main__":
    h4 = load("xauusd_h4.csv")
    print(f"נרות: {len(h4)}  |  {h4[0]['t'].date()} → {h4[-1]['t'].date()}\n")
    base = sim(h4)
    print(f"{'וריאנט':<22}{'עסק':>5}{'הצל%':>7}{'רווח':>10}{'לעסקה':>9}{'גרוע':>9}{'MaxDD':>10}{'BE':>5}{'ימים':>7}")
    print("-"*84)
    def line(name, r):
        print(f"{name:<22}{r['n']:>5}{r['wr']:>6.0f}%{r['pnl']:>10.0f}{r['per']:>9.1f}{r['worst']:>9.0f}{maxdd(r['detail']):>10.0f}{r['be_hits']:>5}{r['avg_days']:>7.1f}")
    line("בסיס (בלי BE)", base)
    for trig in [10, 15, 20, 25, 30, 40, 50, 60, 80, 100]:
        line(f"BE @ {trig}$", sim(h4, be_trigger=trig))

import statistics
from be import load, sim, maxdd
h4=load("xauusd_h4.csv")

def apply_carry(det, ils_per_day):
    out=[]
    for t in det:
        c=dict(t); c["pnl"]=t["pnl"]-ils_per_day*t["days"]; out.append(c)
    return out

def stats(det):
    n=len(det); w=[x for x in det if x["pnl"]>0]
    return n, 100*len(w)/n, sum(x["pnl"] for x in det), sum(x["pnl"] for x in det)/n

for nm,kw in [("בסיס",dict()),("BE 25+3",dict(be_trigger=25,be_offset=3))]:
    r=sim(h4,**kw); d=r["detail"]
    days=[x["days"] for x in d]
    print(f"\n### {nm}")
    print(f"החזקה: ממוצע {statistics.mean(days):.1f} ימים | חציון {statistics.median(days):.1f} | הכי ארוך {max(days):.0f}")
    print(f"סה\"כ ימי חשיפה: {sum(days):.0f}")
    print(f"{'עלות/יום':>10}{'רווח':>9}{'לעסקה':>9}{'הצל%':>8}")
    for c in [0,1,2,3,4,5,7,10]:
        n,wr,p,per = stats(apply_carry(d,c))
        print(f"{c:>9.0f}₪{p:>9.0f}{per:>9.1f}{wr:>7.0f}%")

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
