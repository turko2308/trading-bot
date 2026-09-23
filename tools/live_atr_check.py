"""
tools/live_atr_check.py — האם ה-ATR החי (שיטה 3) תואם ל-ATR שהקוד עצמו מחשב על נתונים נקיים? (20/09/2026)
==========================================================================================
משווה כל איתות ב-tf_signals של הגיסט (שדה atr) ל-ATR שמחושב ע"י _tf_aggregate/_tf_atr של הבוט על data/xauusd_h1.csv
(Dukascopy, בלי נרות סוף-שבוע), ומפצל תוצאות לפי "ATR מנופח כלפי מטה" (יחס < 0.7) וחלוקה ליום בשבוע.
ממצא ב-bot_data.json מ-20/09: 18 מתוך 20 איתותים עם ATR מנופח כלפי מטה הם בימי שני-שלישי (חלון 14 הנרות חוצה את סוף השבוע).
הרצה מתוך tools/:  python live_atr_check.py /path/to/bot_data.json
"""
import sys, json, csv, datetime, collections, numpy as np
sys.path.insert(0, "..")
import trading_bot as tb
S = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "bot_data.json"))["tf_signals"]
rows = []
for r in csv.DictReader(open("../data/xauusd_h1.csv")):
    t = datetime.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None) + datetime.timedelta(hours=3)
    rows.append({"t": t, "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])})
recs = []
for s in S:
    if s["status"] != "closed": continue
    hrs = int(s["tf"][0]); c = datetime.datetime.fromisoformat(s["candle"])
    h = [b for b in rows if b["t"] < c + datetime.timedelta(hours=hrs)]
    closed = tb._tf_aggregate(h[-800:], hrs)
    if not closed or closed[-1]["t"] != c: continue
    ratio = s["atr"] / tb._tf_atr(closed)
    wd = datetime.datetime.fromisoformat(s["time"]).replace(tzinfo=None).weekday()
    recs.append((wd, ratio, s["result"], s["pnl"], s["bars_held"]))
wdn = ["ב'", "ג'", "ד'", "ה'", "ו'"]
for i, n in enumerate(wdn):
    R = [r for r in recs if r[0] == i]
    print(f"יום {n}: {len(R)} איתותים, ATR מנופח: {sum(1 for r in R if r[1] < 0.7)}")
for nm, f in (("ATR מנופח (<0.7)", lambda r: r[1] < 0.7), ("ATR תקין", lambda r: r[1] >= 0.7)):
    R = [r for r in recs if f(r)]
    if R: print(f"{nm}: N={len(R)} הצלחה={100*sum(1 for r in R if r[2]=='win')/len(R):.0f}% רווח={sum(r[3] for r in R):+.0f}₪ חציון החזקה={np.median([r[4] for r in R]):.0f} נרות")
