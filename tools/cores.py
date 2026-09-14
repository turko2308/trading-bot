"""
tools/cores.py — Core A (מגמה טהורה) מול Core B (מין-ריברז'ן טהור), סוויפ טווחי-זמן
========================================================================================
שוחזר 14/09/2026 — הקובץ המקורי לא נשמר בריפו מעולם (רק המסקנות שלו תועדו ב-DECISIONS.md).
זהו זה שגילה את שיטה 3: הרצה על טווחי-זמן שונים (1H-1D) חשפה ש-6H הוא נקודת האיזון
בין "מספיק עסקאות לתוקף סטטיסטי" ל"ספרד קטן מספיק יחסית לתזוזה הטיפוסית".

Core A: מגמה טהורה — פריצת 20-נר בכיוון EMA50, בלי תקרת-מתיחה. סטופ/יעד ATR14.
Core B: מין-ריברז'ן טהור — כניסה ב-2 סטיות-תקן מ-Bollinger(20), סטופ/יעד ATR14.

כלכלה: OZ=0.75 (גודל המסחר האמיתי שלו ב-Plus500 — לא ה-1.5 שבקוד החי, ר' STATUS §
"known accepted sizing mismatch"), ספרד 0.77$, USD/ILS≈3.00 — תואם למוסכמה
שכבר קיימת בשאר tools/ (s1lab.py, tf_offset.py, mfe.py).

הרצה: python tools/cores.py   (מצפה ל-xauusd_h1.csv באותה תיקייה)
"""
import csv, datetime, statistics as st

OZ = 0.75
SPREAD = 0.77
USD_ILS = 3.00
IPP = OZ * USD_ILS   # ILS per $1 move

def load_h1(path="xauusd_h1.csv"):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": datetime.datetime.fromisoformat(r["timestamp"]),
                "o": float(r["open"]), "h": float(r["high"]),
                "l": float(r["low"]), "c": float(r["close"]),
            })
    return sorted(rows, key=lambda x: x["t"])

def aggregate(bars, hours):
    """מקבץ נרות H1 לנרות של N שעות, יישור מתחילת היום."""
    out = {}
    for x in bars:
        k = x["t"].replace(minute=0, second=0, microsecond=0)
        k = k.replace(hour=(k.hour // hours) * hours)
        g = out.setdefault(k, {"t": k, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]})
        g["h"] = max(g["h"], x["h"]); g["l"] = min(g["l"], x["l"]); g["c"] = x["c"]
    return [out[k] for k in sorted(out)]

def ema_series(closes, span):
    out = [None] * len(closes)
    k = 2 / (span + 1)
    e = closes[0]
    out[0] = e
    for i in range(1, len(closes)):
        e = closes[i] * k + e * (1 - k)
        out[i] = e
    return out

def atr_series(bars, n=14):
    trs = []
    for i in range(len(bars)):
        if i == 0:
            trs.append(bars[i]["h"] - bars[i]["l"]); continue
        pc = bars[i-1]["c"]
        trs.append(max(bars[i]["h"]-bars[i]["l"], abs(bars[i]["h"]-pc), abs(bars[i]["l"]-pc)))
    out = [None]*len(bars)
    for i in range(n-1, len(bars)):
        out[i] = sum(trs[i-n+1:i+1]) / n
    return out

def core_a(bars, target_mult=2.0, stop_mult=2.0, donch_n=20, max_hold=120, slippage=0.0):
    closes = [b["c"] for b in bars]
    ema = ema_series(closes, 50)
    atr = atr_series(bars, 14)
    trades = []; pos = None
    for i in range(60, len(bars)):
        c = bars[i]
        if pos is None:
            donch_high = max(x["h"] for x in bars[i-donch_n:i])
            if c["c"] > donch_high and atr[i] and c["c"] > ema[i]:
                entry = c["c"] + slippage
                pos = {"i": i, "entry": entry, "stop": entry - stop_mult*atr[i],
                       "target": entry + target_mult*atr[i]}
            continue
        exit_px = None
        if bars[i]["l"] <= pos["stop"]: exit_px = pos["stop"]
        elif bars[i]["h"] >= pos["target"]: exit_px = pos["target"]
        elif i - pos["i"] >= max_hold: exit_px = c["c"]
        if exit_px is not None:
            pnl_pts = (exit_px - pos["entry"]) - SPREAD
            trades.append({"pnl": pnl_pts * IPP, "t": bars[i]["t"]})
            pos = None
    return trades

def core_b(bars, target_mult=2.0, stop_mult=2.0, bb_n=20, bb_k=2.0, max_hold=120, slippage=0.0):
    closes = [b["c"] for b in bars]
    atr = atr_series(bars, 14)
    trades = []; pos = None
    for i in range(60, len(bars)):
        c = bars[i]
        if pos is None:
            window = closes[i-bb_n:i]
            ma = sum(window)/bb_n
            sd = st.pstdev(window)
            lower = ma - bb_k*sd
            if c["c"] < lower and atr[i]:
                entry = c["c"] + slippage
                pos = {"i": i, "entry": entry, "stop": entry - stop_mult*atr[i],
                       "target": entry + target_mult*atr[i]}
            continue
        exit_px = None
        if bars[i]["l"] <= pos["stop"]: exit_px = pos["stop"]
        elif bars[i]["h"] >= pos["target"]: exit_px = pos["target"]
        elif i - pos["i"] >= max_hold: exit_px = c["c"]
        if exit_px is not None:
            pnl_pts = (exit_px - pos["entry"]) - SPREAD
            trades.append({"pnl": pnl_pts * IPP, "t": bars[i]["t"]})
            pos = None
    return trades

def half_year_breakdown(trades):
    buckets = {}
    for tr in trades:
        h = f"{tr['t'].year}-H{1 if tr['t'].month<=6 else 2}"
        buckets.setdefault(h, []).append(tr["pnl"])
    return {h: (len(v), round(sum(v),1)) for h, v in sorted(buckets.items())}

def slippage_stress(bars_fn, bars, base_slip_points_list=(0,1,2,3,5)):
    out = {}
    for sp in base_slip_points_list:
        trades = bars_fn(bars, slippage=sp)
        out[sp] = round(sum(t["pnl"] for t in trades), 1)
    return out

if __name__ == "__main__":
    h1 = load_h1()
    print(f"נטען: {len(h1)} נרות H1, {h1[0]['t'].date()} עד {h1[-1]['t'].date()}\n")

    print("=" * 78)
    print("סוויפ טווחי-זמן — Core A (מגמה)")
    print("=" * 78)
    for hrs, label in [(1,"1H"),(2,"2H"),(4,"4H"),(6,"6H"),(8,"8H"),(12,"12H"),(24,"1D")]:
        bars = aggregate(h1, hrs)
        trades = core_a(bars)
        total = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = 100*wins/len(trades) if trades else 0
        print(f"{label:4s}  N={len(trades):4d}  win%={wr:5.1f}  total={total:9.1f} ILS")

    print("\n" + "=" * 78)
    print("עמידות סליפג' — 6H (המועמד המוביל)")
    print("=" * 78)
    bars6 = aggregate(h1, 6)
    stress = slippage_stress(core_a, bars6)
    for sp, total in stress.items():
        print(f"  סליפג' {sp}$: total={total} ILS")

    print("\n" + "=" * 78)
    print("פילוח חצי-שנתי — Core A 6H")
    print("=" * 78)
    trades6 = core_a(bars6)
    for h, (n, s) in half_year_breakdown(trades6).items():
        print(f"  {h}:  {n:3d} עסקאות   {s:9.1f} ILS")

    print("\n" + "=" * 78)
    print("Core B (מין-ריברז'ן) — לשם השוואה, 6H")
    print("=" * 78)
    tradesB = core_b(bars6)
    totalB = sum(t["pnl"] for t in tradesB)
    wrB = 100*sum(1 for t in tradesB if t["pnl"]>0)/len(tradesB) if tradesB else 0
    print(f"N={len(tradesB)}  win%={wrB:.1f}  total={totalB:.1f} ILS")
    for h, (n, s) in half_year_breakdown(tradesB).items():
        print(f"  {h}:  {n:3d} עסקאות   {s:9.1f} ILS")
