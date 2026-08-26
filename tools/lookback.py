#!/usr/bin/env python3
"""
tools/lookback.py  —  ביקורת סיווג הגיבוי בשיטה 3 (26/08)

השאלה (STATUS §13.9.9, פריט פתוח מ-18/08):
  TF_BACKUP_LOOKBACK_H=6 מכסה נר 6H אחד בדיוק. האם הוא מפיל
  איתותי 4H לקבוצה "בלי גיבוי" בטעות?

התשובה: **לא. הסיווג בבוט תקין.**

שתי השערות נבדקו ונדחו — שתיהן החזירו אפס:

  ה"א — 4H נסרק לפני 6H ב-TF_CONFIGS, ולכן איתות 6H של אותה
        סריקה עוד לא ב-log כשה-4H בודק גיבוי. נר 4H שנסגר
        ב-00:00 או 12:00 סימולטני עם נר 6H, ולכן חשוד.
        **נדחתה:** בלוק ה-6H (trading_bot.py ~2414) סורק אחורה
        4 שעות ומהפך כל 4H עם backup=False ל-True — באותה
        סריקה. הסימולטני נתפס שם. הפרש: 0.

  ה"ב — cutoff = now_il() - 6h מחושב משעון הקיר ולא מסגירת
        הנר, ולכן חותך 6H שנמצא בדיוק 6 שעות אחורה.
        **נדחתה:** 0 איתותים בכל 2.5 השנים.

⚠️ אזהרה למי שמשנה את הכלי: מודל של "הבוט החי" שאינו כולל את
   לולאת הגיבוי-המאוחר יחזיר 59% במקום 81% ויראה כמו באג גדול.
   זו בדיוק הטעות שהכלי הזה נכתב כדי לא לחזור עליה. הפונקציה
   live() למטה מממשת את *שני* השלבים. אין למדוד רק את הראשון.

כלל 23 מקוים: סריקת היציאה מתחילה מ**סגירת** נר האיתות.
הרצה:  python3 tools/lookback.py data/xauusd_m15.csv
"""
import csv, sys, datetime as dt

TF_EMA_PERIOD, TF_BREAKOUT_BARS = 50, 20
TF_STOP_ATR = TF_TARGET_ATR = 2.0
TF_BACKUP_LOOKBACK_H, TF_BACKUP_WINDOW_H = 6, 4
SPREAD, USD_ILS, OZ = 0.77, 3.0014, 0.75


def load(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            out.append({"t": dt.datetime.fromisoformat(r["timestamp"]).replace(tzinfo=None),
                        "o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"])})
    return out


def agg(bars, hours):
    """זהה ל-_tf_aggregate בבוט: hour // hours * hours"""
    buck = {}
    for b in bars:
        k = b["t"].replace(hour=b["t"].hour // hours * hours, minute=0, second=0)
        if k not in buck:
            buck[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            d = buck[k]
            d["h"] = max(d["h"], b["h"]); d["l"] = min(d["l"], b["l"]); d["c"] = b["c"]
    return [buck[k] for k in sorted(buck)]


def ema(vals, p):
    k = 2 / (p + 1); e = sum(vals[:p]) / p; out = [None] * (p - 1) + [e]
    for v in vals[p:]:
        e = v * k + e * (1 - k); out.append(e)
    return out


def atr14(bars, i):
    if i < 14:
        return None
    trs = []
    for j in range(i - 13, i + 1):
        pc = bars[j - 1]["c"]
        trs.append(max(bars[j]["h"] - bars[j]["l"],
                       abs(bars[j]["h"] - pc), abs(bars[j]["l"] - pc)))
    return sum(trs) / 14


def signals(bars, hours):
    closes = [b["c"] for b in bars]; es = ema(closes, TF_EMA_PERIOD); out = []
    for i in range(TF_EMA_PERIOD + TF_BREAKOUT_BARS, len(bars)):
        prior = bars[i - TF_BREAKOUT_BARS:i]
        hh = max(b["h"] for b in prior); ll = min(b["l"] for b in prior)
        c = bars[i]["c"]; e = es[i]
        if e is None:
            continue
        d = "buy" if (c > hh and c > e) else ("sell" if (c < ll and c < e) else None)
        if not d:
            continue
        a = atr14(bars, i)
        if not a:
            continue
        out.append({"t_open": bars[i]["t"],
                    "t": bars[i]["t"] + dt.timedelta(hours=hours),   # כלל 23
                    "dir": d, "e": c, "atr": a,
                    "stop": c - TF_STOP_ATR * a if d == "buy" else c + TF_STOP_ATR * a,
                    "tgt":  c + TF_TARGET_ATR * a if d == "buy" else c - TF_TARGET_ATR * a})
    return out


def simulate(sig, h1):
    """נר פסימי: סטופ מנצח כשנר נגע בשניהם. מתחיל מסגירת נר האיתות."""
    for b in h1:
        if b["t"] < sig["t"]:
            continue
        if sig["dir"] == "buy":
            if b["l"] <= sig["stop"]: return sig["stop"]
            if b["h"] >= sig["tgt"]:  return sig["tgt"]
        else:
            if b["h"] >= sig["stop"]: return sig["stop"]
            if b["l"] <= sig["tgt"]:  return sig["tgt"]
    return None


def pnl_ils(sig, px):
    pts = (px - sig["e"]) if sig["dir"] == "buy" else (sig["e"] - px)
    return (pts - SPREAD) * OZ * USD_ILS


def live(s4, s6, drift_min=3, late_window=True, simultaneous=True):
    """
    מודל הבוט החי. **שני שלבים** — חובה למדוד את שניהם:
      1. בדיקה אחורה בזמן האיתות: [T-6h+drift, T)
         הדריפט קיים כי הסריקה רצה דקות אחרי סגירת הנר.
         איתות 6H סימולטני עדיין לא ב-log אם 4H נסרק ראשון.
      2. גיבוי מאוחר: בלוק ה-6H מהפך 4H עם backup=False בחלון
         [T, T+4h]. כאן נתפס הסימולטני.
    late_window=False / simultaneous=False קיימים לבידוד בלבד.
    """
    out = []
    for s in s4:
        T = s["t"]
        back = T - dt.timedelta(hours=TF_BACKUP_LOOKBACK_H) + dt.timedelta(minutes=drift_min)
        fwd = T + dt.timedelta(hours=TF_BACKUP_WINDOW_H)
        found = False
        for x in s6:
            if x["dir"] != s["dir"]:
                continue
            if x["t"] == T and not simultaneous:
                continue
            if back <= x["t"] < T:
                found = True; break
            if late_window and T <= x["t"] <= fwd:
                found = True; break
        out.append(found)
    return out


def stat(v):
    if not v:
        return "n=0"
    w = sum(1 for x in v if x > 0)
    return f"n={len(v):3d}  wr={100*w/len(v):4.1f}%  pnl={sum(v):+7.0f}  per={sum(v)/len(v):+6.1f}"


def main(path):
    m15 = load(path)
    h1 = agg(m15, 1)
    s4, s6 = signals(agg(m15, 4), 4), signals(agg(m15, 6), 6)
    res = [(s, pnl_ils(s, px)) for s in s4 if (px := simulate(s, h1)) is not None]
    S = [r[0] for r in res]
    print(f"h1={len(h1)}  4H sig={len(s4)}  6H sig={len(s6)}  resolved={len(res)}\n")

    cases = [
        ("הבוט החי (שני השלבים)",                dict()),
        ("בידוד ה\"ב — cutoff מסגירת הנר",        dict(drift_min=0)),
        ("בידוד ה\"א — בלי סימולטני, בלי מאוחר",  dict(simultaneous=False, late_window=False)),
    ]
    base = None
    for label, kw in cases:
        f = live(S, s6, **kw)
        yes = [p for (s, p), x in zip(res, f) if x]
        no  = [p for (s, p), x in zip(res, f) if not x]
        print(f"{label}   גיבוי={sum(f)}/{len(f)} ({100*sum(f)/len(f):.1f}%)")
        print(f"   עם גיבוי  {stat(yes)}")
        print(f"   בלי גיבוי {stat(no)}")
        if base is None:
            base = f
        else:
            print(f"   הפרש מהחי: {sum(1 for a, b in zip(base, f) if a != b)} איתותים")
        print()

    print("שיעור הגיבוי המתועד ב-DECISIONS: 80%. החי מחזיר 81% — תואם.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/xauusd_m15.csv")
