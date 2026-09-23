"""
fetch_dukascopy_3y.py — משיכת 3 שנים של XAUUSD (M15 + H1 + H4) מ-Dukascopy, ל-Colab (21/09/2026)
==========================================================================================
מריצים בגוגל קולאב (לא כאן — dukascopy-python צריך גישה לשרתי Dukascopy שאין לי בסביבה שלי).

שלבים ב-Colab:
1. תא חדש:  !pip install dukascopy-python -q
2. תא חדש: מדביקים את כל הקובץ הזה ומריצים.
3. משם מורידים את שלושת קבצי ה-CSV (Files ▸ הורדה) ומעלים ל-data/ בריפו, באותם שמות
   שכבר קיימים שם (xauusd_m15.csv, xauusd_h1.csv, xauusd_h4.csv) — מחליפים את הישנים.

הערות:
- offer_side=BID, בדיוק כמו הקבצים הקיימים בריפו (כותרת volume קיימת גם היא, tick volume).
- טווח: 3 שנים אחורה מהיום, כדי לכסות גם את מה שהיה חסר (קבצי ה-M15 הישנים נעצרו ב-29/07/2026,
  וה-H1 ב-14/09/2026) — כולל את כל איתותי שיטה 1 (Renko) החיים של ספטמבר.
- הפונקציה fetch() לפעמים מחזירה עד ~30,000 שורות בקריאה אחת; לכן המשיכה מפוצלת לחודשים
  ומאוחדת, כדי לכסות 3 שנים במלואן בלי לפספס טווח.
- הפלט נשמר באותו פורמט עמודות כמו הקבצים הקיימים: timestamp,open,high,low,close,volume.
"""
import dukascopy_python
from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
import pandas as pd
from datetime import datetime, timedelta, timezone

END = datetime.now(timezone.utc)
START = END - timedelta(days=365 * 3)

INTERVALS = {
    "m15": dukascopy_python.INTERVAL_MIN_15,
    "h1":  dukascopy_python.INTERVAL_HOUR_1,
    "h4":  dukascopy_python.INTERVAL_HOUR_4,
}

def fetch_range(interval_const, start, end):
    """מושך בפיצול חודשי כדי לא לפגוע במגבלת ~30,000 השורות לקריאה, ומאחד לדאטהפריים אחד."""
    frames = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        df = dukascopy_python.fetch(
            instrument=INSTRUMENT_FX_METALS_XAU_USD,
            interval=interval_const,
            offer_side=dukascopy_python.OFFER_SIDE_BID,
            start=cur,
            end=nxt,
        )
        if df is not None and len(df):
            frames.append(df)
        cur = nxt
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    return out

for name, interval_const in INTERVALS.items():
    print(f"מושך {name} ...")
    df = fetch_range(interval_const, START, END)
    df = df.rename(columns={"volume": "volume"})  # השם כבר תואם; נשאר רק ליישור
    df.index.name = "timestamp"
    fname = f"xauusd_{name}.csv"
    df.reset_index()[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(fname, index=False)
    print(f"  נשמר {fname}: {len(df)} שורות, {df.index.min()} → {df.index.max()}")

print("\nסיימנו. מורידים את שלושת קבצי ה-CSV ומעלים ל-data/ בריפו turko2308/trading-bot.")
