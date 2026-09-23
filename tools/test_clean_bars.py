"""
tools/test_clean_bars.py — בדיקה שהסינון _clean_bars מחזיר את ה-ATR לערכו האמיתי (20/09/2026)
מדמה פיד עם נרות שטוחים בסוף השבוע (שבת 01:00 עד שני 01:00 שעון ישראל), מריץ את _tf_aggregate/_tf_atr של הבוט
לפני ואחרי הסינון, ומשווה ל-ATR על נתונים נקיים (Dukascopy). הרצה מהשורש של הריפו: python tools/test_clean_bars.py
"""
import sys, csv, datetime, numpy as np
sys.path.insert(0, ".")
import trading_bot as tb
assert hasattr(tb, "_clean_bars"), "הבוט לא כולל _clean_bars"
clean = []
for r in csv.DictReader(open("data/xauusd_h1.csv")):
    t = datetime.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None) + datetime.timedelta(hours=3)
    clean.append({"t": t, "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"])})
polluted = []
for i, b in enumerate(clean):
    polluted.append(b)
    if i + 1 < len(clean):
        gap = clean[i + 1]["t"] - b["t"]
        if gap > datetime.timedelta(hours=2):        # מילוי פערים (סוף שבוע) בנרות שטוחים במחיר הסגירה
            t = b["t"] + datetime.timedelta(hours=1)
            while t < clean[i + 1]["t"]:
                polluted.append({"t": t, "o": b["c"], "h": b["c"], "l": b["c"], "c": b["c"]}); t += datetime.timedelta(hours=1)
fixed = tb._clean_bars(polluted)
print(f"נרות: נקי={len(clean)} מזוהם={len(polluted)} אחרי סינון={len(fixed)}")
def atr_at(bars, hrs, when):
    h = [b for b in bars if b["t"] < when + datetime.timedelta(hours=hrs)]
    ag = tb._tf_aggregate(h[-800:], hrs)
    return tb._tf_atr(ag) if len(ag) > 20 else None
worst = 0; bad_before = 0; n = 0
for hrs in (4, 6):
    when = datetime.datetime(2026, 8, 31, 4, 0) if hrs == 4 else datetime.datetime(2026, 9, 1, 6, 0)
    a, b, c = atr_at(clean, hrs, when), atr_at(polluted, hrs, when), atr_at(fixed, hrs, when)
    print(f"{hrs}H @ {when:%m-%d %H:%M}: נקי {a:6.2f} | מזוהם {b:6.2f} ({b/a:.2f}x) | אחרי סינון {c:6.2f} ({c/a:.2f}x)")
# כל ימי שני-שלישי בחודשיים האחרונים
days = [d for d in (datetime.datetime(2026, 8, 3) + datetime.timedelta(days=k) for k in range(0, 45)) if d.weekday() in (0, 1)]
for d in days:
    for hrs in (4, 6):
        when = d.replace(hour=12)
        a, b, c = atr_at(clean, hrs, when), atr_at(polluted, hrs, when), atr_at(fixed, hrs, when)
        if a and b and c:
            n += 1; bad_before += (b / a < 0.7); worst = max(worst, abs(c / a - 1))
print(f"\nימי שני-שלישי שנבדקו: {n}. ATR מנופח (<0.7) לפני סינון: {bad_before}. סטייה מקסימלית אחרי סינון: {100*worst:.1f}%")
