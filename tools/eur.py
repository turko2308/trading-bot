"""
tools/eur.py — EURUSD כמכשיר חלופי לזהב, אותה מתודולוגיה בדיוק
====================================================================
שוחזר 14/09/2026 — הקובץ המקורי לא נשמר בריפו (31/07-01/08, לא תועד גם בשם ב-DECISIONS).

שאלה: אם הבעיה של שיטה 1 היא "קצה קטן מספרד", האם ספרד זול יותר (EUR/USD,
~1 פיפ מול 0.77$ בזהב) מציל אותה? תשובה מתועדת: לא — הקצה הגולמי חלש יותר
ב-EURUSD מלכתחילה, אז ספרד זול לא עזר.

כיול כלכלי: 1% תזוזה = 89.7 ₪ (מכוייל להיות שווה-ערך לזהב), ספרד 1 פיפ = 0.83 ₪.
Core A ו-Method-2-style (Donchian) שניהם נבדקים כאן, אותה לוגיקה מ-cores.py.

הרצה: python tools/eur.py   (מצפה ל-data/eurusd_h4.csv)
"""
import csv, datetime

PIP_ILS = 0.83          # ספרד 1 פיפ = כמה ש"ח
PCT_MOVE_ILS = 89.7      # 1% תזוזה = כמה ש"ח (מכוייל שווה-ערך לזהב)

def load_h4(path="data/eurusd_h4.csv"):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = r["timestamp"].split("+")[0].strip()
            rows.append({
                "t": datetime.datetime.fromisoformat(ts),
                "o": float(r["open"]), "h": float(r["high"]),
                "l": float(r["low"]), "c": float(r["close"]),
            })
    return sorted(rows, key=lambda x: x["t"])

def price_to_ils(price_diff, ref_price):
    """ממיר הפרש מחיר ל-ש"ח לפי הכיול (% תזוזה יחסית ל-ref_price)."""
    pct = price_diff / ref_price
    return pct * 100 * PCT_MOVE_ILS

def ema_series(closes, span):
    out = [None]*len(closes); k = 2/(span+1); e = closes[0]; out[0]=e
    for i in range(1, len(closes)):
        e = closes[i]*k + e*(1-k); out[i]=e
    return out

def atr_series(bars, n=14):
    trs = []
    for i in range(len(bars)):
        if i==0: trs.append(bars[i]["h"]-bars[i]["l"]); continue
        pc = bars[i-1]["c"]
        trs.append(max(bars[i]["h"]-bars[i]["l"], abs(bars[i]["h"]-pc), abs(bars[i]["l"]-pc)))
    out=[None]*len(bars)
    for i in range(n-1, len(bars)):
        out[i] = sum(trs[i-n+1:i+1])/n
    return out

def core_a(bars, target_mult=2.0, stop_mult=2.0, donch_n=20, max_hold=120, spread_pips=1.0):
    closes=[b["c"] for b in bars]; ema=ema_series(closes,50); atr=atr_series(bars,14)
    spread = spread_pips*0.0001
    trades=[]; pos=None
    for i in range(60, len(bars)):
        c=bars[i]
        if pos is None:
            hh=max(x["h"] for x in bars[i-donch_n:i])
            if c["c"]>hh and atr[i] and c["c"]>ema[i]:
                entry=c["c"]
                pos={"i":i,"entry":entry,"stop":entry-stop_mult*atr[i],"target":entry+target_mult*atr[i]}
            continue
        exit_px=None
        if bars[i]["l"]<=pos["stop"]: exit_px=pos["stop"]
        elif bars[i]["h"]>=pos["target"]: exit_px=pos["target"]
        elif i-pos["i"]>=max_hold: exit_px=c["c"]
        if exit_px is not None:
            diff=(exit_px-pos["entry"])-spread
            trades.append({"pnl":price_to_ils(diff,pos["entry"]),"t":bars[i]["t"]})
            pos=None
    return trades

def method2_donchian(bars, entry_days=20, trail_days=4, candles_per_day=6, spread_pips=1.0):
    ew=entry_days*candles_per_day; tw=trail_days*candles_per_day
    spread=spread_pips*0.0001
    trades=[]; pos=None
    for i in range(ew, len(bars)):
        hh=max(x["h"] for x in bars[i-ew:i]); ll=min(x["l"] for x in bars[i-ew:i])
        if pos is None:
            d=None
            if bars[i]["c"]>hh: d="long"
            elif bars[i]["c"]<ll: d="short"
            if d:
                entry=bars[i]["c"]
                t_lo=min(x["l"] for x in bars[max(0,i-tw):i]); t_hi=max(x["h"] for x in bars[max(0,i-tw):i])
                pos={"dir":d,"entry":entry,"trail":t_lo if d=="long" else t_hi}
            continue
        t_lo=min(x["l"] for x in bars[max(0,i-tw):i]); t_hi=max(x["h"] for x in bars[max(0,i-tw):i])
        if pos["dir"]=="long": pos["trail"]=max(pos["trail"],t_lo)
        else: pos["trail"]=min(pos["trail"],t_hi)
        hit=(pos["dir"]=="long" and bars[i]["c"]<=pos["trail"]) or (pos["dir"]=="short" and bars[i]["c"]>=pos["trail"])
        if hit:
            diff=(bars[i]["c"]-pos["entry"]-spread) if pos["dir"]=="long" else (pos["entry"]-bars[i]["c"]-spread)
            trades.append({"pnl":price_to_ils(diff,pos["entry"]),"t":bars[i]["t"]})
            pos=None
    return trades

if __name__=="__main__":
    h4=load_h4()
    print(f"נטען: {len(h4)} נרות EURUSD H4, {h4[0]['t'].date()} עד {h4[-1]['t'].date()}\n")

    print("="*70); print("Core A (מגמה טהורה) על EURUSD, 4H"); print("="*70)
    t=core_a(h4)
    total=sum(x["pnl"] for x in t); wr=100*sum(1 for x in t if x["pnl"]>0)/len(t) if t else 0
    print(f"N={len(t)}  win%={wr:.1f}  total={total:.1f} ILS")

    print("\n"+"="*70); print("שיטה-2 סגנון (Donchian 20/4) על EURUSD, 4H"); print("="*70)
    t2=method2_donchian(h4)
    total2=sum(x["pnl"] for x in t2); wr2=100*sum(1 for x in t2 if x["pnl"]>0)/len(t2) if t2 else 0
    print(f"N={len(t2)}  win%={wr2:.1f}  total={total2:.1f} ILS")

    print("\n(השוואה: אותם מנועים בדיוק על זהב — tools/cores.py)")
