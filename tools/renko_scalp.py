"""
tools/renko_scalp.py — Renko קופסה-קטנה: מועמד להחלפת שיטה 1
================================================================================================
נבנה 14/09/2026. ההקשר: שיטה 1 (EMA50+RSI/Bollinger) סגורה סופית — נבדקה שוב היום
על דאטה עדכני (כולל אוג-ספט 2026) וביציאה יחסית-ל-ATR (לא דולר קבוע), ב-4 רמות
הרפיה: 24/172/192/800 עסקאות, כולן 37-41% הצלחה, כולן שליליות. ראה DECISIONS.md.

הרעיון: לא להחליף את האינדיקטור של שיטה 1 — להחליף את המנגנון. Renko עם קופסה
קטנה (3$) נותן בדיוק את הפרופיל ששיטה 1 נועדה לתת (כניסות תכופות, תוך-יומיות),
אבל מסנן רעש לפי גודל-תנועה במקום לפי זמן.

פרמטרים: box=3$, אישור=2 לבנים רצופים, יעד=6 קופסאות, סטופ=3 קופסאות (יחס 2:1),
max_hold=6 לבנים.

תוצאות (3 שנים מלאות, 14/9/2023-14/9/2026, מנרות H1):
  N=4,507, win%=68.4%, total=+33,129$, ~4.1 עסקאות/יום
  עמיד עד 5$/עסקה עלות (פי 6.5 מהספרד): עדיין +10,594$
  Bootstrap P(רווח): 100%
  Monte Carlo DD: חציון -60$, גרוע -120$ → יחס DD:רווח ≈ 1:276 (הכי טוב בפרויקט)
  יציבות: 7/7 חצאי-שנה חיוביים. 2026-H1 (שהרג כל שיטה אחרת) = התורם הכי גדול, +17,535$
  אוגוסט-ספטמבר 2026 בלבד: N=308, +2,028$, 65.6% הצלחה
  חפיפה מול שיטה 2 האמיתית: 0.0%   |   מול שיטה 3 האמיתית: 0.0%  → עצמאי לחלוטין

בדיקת רזולוציה (חשוב לבנייה):
  אותה תקופה, box=3: M15 (עדין) = 6,631 עסקאות/+33,165$ | H1 (גס) = 4,167/+30,795$
  → קירוב מרזולוציה גסה שומר ~93% מהרווח ומעלה את אחוז ההצלחה. הקצה שורד קירוב.
  המשמעות: אפשר לבנות Renko מהמחיר-החי שכבר נשלף בכל סריקה (כל 10 דק', כולל המחיר
  החי מתיקון 04/08) — בלי אף קריאת API נוספת. תקציב Twelve Data לא נפגע.

⚠️ מה שעוד לא נעשה לפני בנייה:
  1. selection bias — נבדקו 5 שילובי פרמטרים ונבחר הטוב. שכנים (box=5) חיוביים אך חלשים יותר.
  2. אין CPCV/walk-forward אמיתי (tools/cpcv.py עדיין לא קיים — פער ידוע).
  3. לא הוחלט סופית לבנות. שיטה 2 ו-3 נשארות ללא שינוי בכל מקרה.

הרצה: python tools/renko_scalp.py   (מצפה ל-../data/xauusd_h1.csv)
"""
import csv, datetime, sys
import numpy as np

def load_h1(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = r["timestamp"].split("+")[0].strip()
            rows.append({"t": datetime.datetime.fromisoformat(ts), "c": float(r["close"])})
    return sorted(rows, key=lambda x: x["t"])

def build_renko(closes, times, box_size):
    bricks = []
    ref = closes[0]
    for i in range(1, len(closes)):
        while closes[i] - ref >= box_size:
            ref += box_size
            bricks.append({"dir": 1, "close": ref, "t": times[i]})
        while ref - closes[i] >= box_size:
            ref -= box_size
            bricks.append({"dir": -1, "close": ref, "t": times[i]})
    return bricks

def renko_backtest(closes, times, box_size=3, confirm_bricks=2,
                   target_bricks=6, stop_bricks=3, max_hold_bricks=6):
    bricks = build_renko(closes, times, box_size)
    trades = []; i = confirm_bricks; in_pos = False
    while i < len(bricks):
        if not in_pos:
            recent = [b["dir"] for b in bricks[i-confirm_bricks:i]]
            if all(d == 1 for d in recent):
                entry = bricks[i-1]["close"]; direction = 1
                stop = entry - stop_bricks*box_size; target = entry + target_bricks*box_size
                in_pos = True; entry_i = i
            elif all(d == -1 for d in recent):
                entry = bricks[i-1]["close"]; direction = -1
                stop = entry + stop_bricks*box_size; target = entry - target_bricks*box_size
                in_pos = True; entry_i = i
        else:
            c = bricks[i]["close"]; hit = None
            if direction == 1:
                if c <= stop: hit = "stop"
                elif c >= target: hit = "target"
            else:
                if c >= stop: hit = "stop"
                elif c <= target: hit = "target"
            if hit is None and (i - entry_i) >= max_hold_bricks:
                hit = "timeout"
            if hit:
                pnl = (c - entry) if direction == 1 else (entry - c)
                trades.append({"pnl": pnl, "t": bricks[i]["t"], "reason": hit})
                in_pos = False
        i += 1
    return trades

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/xauusd_h1.csv"
    h1 = load_h1(path)
    closes = [b["c"] for b in h1]; times = [b["t"] for b in h1]
    print(f"נטען: {len(h1)} נרות H1, {times[0]} עד {times[-1]}\n")

    trades = renko_backtest(closes, times)
    ret = np.array([t["pnl"] for t in trades])
    days = (times[-1] - times[0]).days
    print(f"N={len(trades)}  win%={100*(ret>0).mean():.1f}  total={ret.sum():.1f}$  "
          f"(~{len(trades)/days:.2f} עסקאות/יום)")

    print("\n-- עמידות עלות --")
    for c in [0.77, 2, 3, 5]:
        print(f"  {c}$/עסקה: total={round((ret-c).sum(),1)}")

    print("\n-- יציבות בין תקופות --")
    from collections import defaultdict
    buckets = defaultdict(list)
    for tr in trades:
        h = f"{tr['t'].year}-H{1 if tr['t'].month<=6 else 2}"
        buckets[h].append(tr["pnl"])
    for h in sorted(buckets):
        v = buckets[h]
        print(f"  {h}:  {len(v):5d} עסקאות  {sum(v):9.1f}$")

    recent = [tr["pnl"] for tr in trades if tr["t"] >= datetime.datetime(2026,8,1)]
    if recent:
        r = np.array(recent)
        print(f"\nאוג-ספט 2026 בלבד: N={len(r)}  total={r.sum():.1f}$  win%={100*(r>0).mean():.1f}")

    rng = np.random.default_rng(42)
    dds = []
    for _ in range(2000):
        eq = np.cumsum(rng.permutation(ret)); dds.append((eq - np.maximum.accumulate(eq)).min())
    sums = [rng.choice(ret, size=len(ret), replace=True).sum() for _ in range(2000)]
    print(f"\nMonte Carlo DD חציוני: {np.median(dds):.1f}$   הכי גרוע: {min(dds):.1f}$")
    print(f"Bootstrap P(רווח): {100*np.mean(np.array(sums)>0):.1f}%")
