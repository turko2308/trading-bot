"""
tools/renko.py — Renko/Range-bar trend-follow — לא תלוי בזמן, מסנן רעש לפי גודל תנועה
================================================================================================
נבנה 14/09/2026. כיוון חדש לגמרי (לא עוד וריאציה על אינדיקטור-מומנטום/אוסצילטור —
משפחה שונה: נר חדש נפתח רק כשהמחיר זז box_size$, בלי קשר לזמן. תנועה קטנה מ-box_size
לא נרשמת בכלל → לא גורמת ליציאה מוקדמת מדי, בדיוק הבעיה שראינו בשיטה 3 באוג-ספט 2026
(כניסות נכונות בכיוון שנחתכות שוב ושוב על ריבאונד קטן בתוך המגמה).

תוצאות (3 שנים מלאות, 14/9/2023-14/9/2026, box=10$, אישור=3 לבנים, יעד=6 קופסאות,
סטופ=3 קופסאות — כלומר יחס סיכוי:סיכון 2:1):
  N=578, win%=50.3%, total=+8,850$ (גולמי, ללא עלות/גודל פוזיציה — יחידת מחיר בלבד)
  עמיד עד 5$/עסקה עלות (פי 6.5 מהספרד האמיתי): עדיין +5,960$
  Bootstrap P(רווח): 100% (2000 דגימות)
  Monte Carlo DD: חציון -300$, הכי גרוע -750$ (יחס לרווח הכולל: ~1:12)
  יציבות: 7/7 חצאי-שנה חיוביים, כולל 2026-H1 שהיה הכי טוב (לא הכי גרוע כמו כל שיטה אחרת
  שנבדקה היום) — כנראה כי ברג'ים תנודתי נוצרות יותר "לבנים" מהר יותר, לוכד יותר מהתזוזה
  האמיתית בלי להיחתך על רעש קטן מגודל הקופסה.
  חפיפה עם כניסות Core A (שיטה 3 סגנון): 2.9% בלבד (על דאטה עד 29/7) — איתות עצמאי,
  לא שכפול. **טרם נבדק מחדש על הדאטה המלא כולל אוג-ספט — לעשות לפני החלטה סופית.**

⚠️ עדיין לא הוחלט אם לשלב בחי. זה מועמד שעבר את כל מבחני הבדידות/robustness/יציבות
שנקבעו היום — לא אישור לבנייה, רק סף כניסה למחקר נוסף (בדיוק כמו שכל שיטה 3 עברה
לפני שהפכה לחיה).

הרצה: python tools/renko.py   (מצפה ל-xauusd_h1_3y.csv או קובץ H1 דומה)
"""
import csv, datetime
import numpy as np

def load_h1(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = r["timestamp"].split("+")[0].strip()
            rows.append({"t": datetime.datetime.fromisoformat(ts), "c": float(r["close"])})
    return sorted(rows, key=lambda x: x["t"])

def build_renko(closes, box_size):
    bricks = []
    ref = closes[0]
    for i in range(1, len(closes)):
        while closes[i] - ref >= box_size:
            ref += box_size
            bricks.append({"dir": 1, "close": ref})
        while ref - closes[i] >= box_size:
            ref -= box_size
            bricks.append({"dir": -1, "close": ref})
    return bricks

def renko_backtest(closes, times, box_size=10, confirm_bricks=3, target_bricks=6, stop_bricks=3):
    bricks = build_renko(closes, box_size)
    # attach approximate close-times by walking through closes again (for reporting only)
    trades = []
    i = confirm_bricks
    in_pos = False
    while i < len(bricks):
        if not in_pos:
            recent = [b["dir"] for b in bricks[i-confirm_bricks:i]]
            if all(d == 1 for d in recent):
                entry = bricks[i-1]["close"]; direction = 1
                stop = entry - stop_bricks*box_size; target = entry + target_bricks*box_size
                in_pos = True
            elif all(d == -1 for d in recent):
                entry = bricks[i-1]["close"]; direction = -1
                stop = entry + stop_bricks*box_size; target = entry - target_bricks*box_size
                in_pos = True
        else:
            c = bricks[i]["close"]
            hit = None
            if direction == 1:
                if c <= stop: hit = "stop"
                elif c >= target: hit = "target"
            else:
                if c >= stop: hit = "stop"
                elif c <= target: hit = "target"
            if hit:
                pnl = (c - entry) if direction == 1 else (entry - c)
                trades.append({"pnl": pnl, "reason": hit})
                in_pos = False
        i += 1
    return trades

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "xauusd_h1_3y.csv"
    h1 = load_h1(path)
    closes = [b["c"] for b in h1]; times = [b["t"] for b in h1]
    print(f"נטען: {len(h1)} נרות H1, {times[0]} עד {times[-1]}\n")

    trades = renko_backtest(closes, times)
    ret = np.array([t["pnl"] for t in trades])
    print(f"N={len(trades)}  win%={100*(ret>0).mean():.1f}  total={ret.sum():.1f}$")

    print("\n-- עמידות עלות --")
    for c in [0.77, 2, 3, 5]:
        print(f"  {c}$/עסקה: total={round((ret-c).sum(),1)}")

    rng = np.random.default_rng(42)
    dds = []
    for _ in range(2000):
        eq = np.cumsum(rng.permutation(ret)); dds.append((eq - np.maximum.accumulate(eq)).min())
    sums = [rng.choice(ret, size=len(ret), replace=True).sum() for _ in range(2000)]
    print(f"\nMonte Carlo DD חציוני: {np.median(dds):.1f}$   הכי גרוע: {min(dds):.1f}$")
    print(f"Bootstrap P(רווח): {100*np.mean(np.array(sums)>0):.1f}%")
