"""
tools/fetch_td_etfs.py — TLT (אג"ח 20 שנה) ו-GDX (מניות כורי זהב) מ-Twelve Data, נרות 15 דקות, 3 שנים (11/10/2026)
לסבבים 11-12. אותו קוד כמו fetch_gld_volume.py, שני סימבולים בהרצה אחת (~10-12 קריאות, 2-3 דקות).
==========================================================================================
למה: כל בדיקות הנפח עד היום היו על tick volume של פורקס (מספר שינויי מחיר, לא כסף אמיתי).
GLD נסחרת בבורסה בניו יורק עם נפח אמיתי (מניות שהחליפו ידיים). הקובץ הזה מוריד אותו כדי לבדוק שוב
השתתפות ופרופיל נפח על זהב.

Colab — תא אחד, להדביק את כל הקובץ ולהריץ. אין צורך ב-pip.
ישאל את מפתח ה-API של Twelve Data (אותו מפתח שב-Render, משתנה TWELVE_DATA_KEY או דומה).
המפתח לא נשמר בקובץ ולא מודפס.

תקציב: כל קריאה = 1 קרדיט, עד 5000 נרות. 3 שנים × ~26 נרות ביום ≈ 20,000 נרות ≈ 4-6 קריאות.
הסקריפט מחכה 9 שניות בין קריאות (מגבלה 8 לדקה) — לא יפגע בבוט (800 ביום).
⚠️ הבוט משתמש באותו מפתח: אם מגיעים למגבלת דקה, הסקריפט מחכה 65 שניות ומנסה שוב.

נוצרים: tlt_m15.csv, gdx_m15.csv  (timestamp UTC, open, high, low, close, volume) — באותו פורמט כמו data/xauusd_m15.csv.
להעלות ל-data/ בריפו: Add file ← Upload files.
"""
import requests, time, csv, datetime, getpass

SYMBOLS  = ["TLT", "GDX"]
INTERVAL = "15min"
START    = datetime.datetime(2023, 9, 1)          # כמו נתוני הזהב בריפו
URL      = "https://api.twelvedata.com/time_series"

key = getpass.getpass("Twelve Data API key: ").strip()

for SYMBOL in SYMBOLS:
    OUT = SYMBOL.lower() + "_m15.csv"
    print("\n=====", SYMBOL)
    rows = {}
    end = None
    calls = 0
    while True:
        p = {"symbol": SYMBOL, "interval": INTERVAL, "outputsize": 5000,
             "timezone": "UTC", "order": "desc", "apikey": key}
        if end:
            p["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")
        r = requests.get(URL, params=p, timeout=60).json()
        calls += 1
        if r.get("status") == "error":
            msg = str(r.get("message", ""))
            if r.get("code") == 429 or "credits" in msg.lower() or "limit" in msg.lower():
                print("מגבלת קריאות — מחכה 65 שניות..."); time.sleep(65); continue
            print("שגיאה:", r.get("code"), msg); break
        vals = r.get("values") or []
        new = 0
        for v in vals:
            t = datetime.datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
            if t not in rows:
                rows[t] = (v["open"], v["high"], v["low"], v["close"], v.get("volume", ""))
                new += 1
        if not vals or new == 0:
            print("אין נתונים ישנים יותר."); break
        oldest = min(rows)
        print(f"קריאה {calls}: +{new} נרות, הישן ביותר עד עכשיו {oldest:%Y-%m-%d %H:%M} UTC, סה\"כ {len(rows)}")
        if oldest <= START:
            break
        end = oldest - datetime.timedelta(minutes=1)
        time.sleep(9)

    ts = sorted(t for t in rows if t >= START)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for t in ts:
            o, h, l, c, vol = rows[t]
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S") + "+00:00", o, h, l, c, vol])

    days = len({t.date() for t in ts})
    zero = sum(1 for t in ts if str(rows[t][4]) in ("", "0"))
    print(f"\nנשמר {OUT}: {len(ts)} נרות, {days} ימי מסחר, {ts[0]:%Y-%m-%d} → {ts[-1]:%Y-%m-%d}, קריאות API: {calls}")
    print(f"נרות בלי נפח: {zero} ({100*zero/max(len(ts),1):.1f}%)")
    if ts and ts[0] > START + datetime.timedelta(days=30):
        print("⚠️ ההיסטוריה התחילה מאוחר מהמבוקש — כנראה מגבלת עומק בתוכנית החינמית. לשלוח לי את השורה הזו.")

    try:
        from google.colab import files
        files.download(OUT)
    except Exception:
        pass
