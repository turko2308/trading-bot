"""
tools/m4_lookahead_check.py — בדיקת הצצה-לעתיד בסינון Renko של שיטה 4 (20/09/2026)
==========================================================================================
הבעיה: ב-tools/m4_range_renko.py, renko_dirs() ממפתח את מצב ה-Renko לפי t = תחילת נר H1,
אבל המצב מחושב אחרי סגירת אותו נר. הפריצה נבדקת על אותו נר (bar["h"] > hi), אז הפילטר
"יודע" איך הנר הפורץ נסגר — הצצה לעתיד. (בבוט החי: m4_scan פועלת על closed[-1] ו-rk_dir
כולל את סגירתו — אותו דבר, והכניסה מתועדת בגבול הטווח שהמחיר כבר עבר.)

התיקון הנבדק: מצב Renko של הנר הקודם (סגור), כלומר בר-ביצוע אמיתי.
הרצה: python tools/m4_lookahead_check.py   (צריך m4_range_renko.py ו-data/xauusd_m15.csv בתיקייה הנוכחית/../data)
"""
import sys, numpy as np
from collections import defaultdict
sys.path.insert(0, ".")
import m4_range_renko as m4

path = sys.argv[1] if len(sys.argv) > 1 else "../data/xauusd_m15.csv"
m15 = m4.load_m15(path)
orig = m4.renko_dirs

def run(label, lag):
    def rd(h1, box, confirm):
        o = orig(h1, box, confirm)
        if not lag: return o
        return {b["t"]: (o[h1[i-1]["t"]] if i > 0 else 0) for i, b in enumerate(h1)}
    m4.renko_dirs = rd
    for name, kw in [("עם Renko", {}), ("בלי Renko", {"use_renko": False})]:
        tr = m4.backtest(m15, **kw)
        r = np.array([x["pnl"] for x in tr]); by = defaultdict(list)
        for x in tr: by[x["t"].year].append(x["pnl"])
        yrs = {y: round(float(np.mean(v)) * m4.ILS_PER_POINT, 1) for y, v in sorted(by.items())}
        print(f"{label:12s}{name:10s} N={len(r)} הצלחה={100*(r>0).mean():.1f}% לעסקה={r.mean()*m4.ILS_PER_POINT:+.1f}₪ שנים={yrs}")

run("כמו בקוד", False)
run("Renko בפיגור נר", True)
