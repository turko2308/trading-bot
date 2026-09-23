"""
tools/renko_early_signal_test.py — Early Signal מול Renko רגיל, בדיקה נקייה (21/09/2026)
==========================================================================================
בודק את רעיון ה-Early Signal של איזק: כניסה לפני שלבנת Renko נסגרת, כאשר המחיר התקדם
frac × גודל-לבנה לכיוון הלבנה הבאה, עם דרישת תנועה נמשכת (progress(t) > progress(t-1))
כדי לא להיכנס על נגיעה חד-פעמית. מיועד לפתור את האיחור שנמדד בשיטה 1 (Renko) החיה
(ראו tools/live_atr_check.py ואת השיחה מ-20-21/09 ב-DECISIONS.md).

מתודולוגיה:
- progress נמדד על סגירות M15 בלבד (ללא הצצה תוך-נרית).
- מילוי ב-open() של הבר הבא אחרי שה-trigger התקבל (לא באותו בר).
- Baseline: מערכת היפוך Renko רגילה, כניסה/היפוך ברמת הלבנה (ref) — רמה ידועה מראש,
  ניתנת להצבה כפקודת Stop ממתינה מבעוד מועד.
- 3 תרחישי עלות: Base (0.77$ ספרד), Realistic (+1$ סליפג'), Stress (1.5× ספרד+סליפג').
- Latency Capture: כמה דולר Early Signal "חסך" ביחס לרמת ה-baseline, לכל עסקה — כולל חציון
  ואחוז המקרים שבהם הכניסה הייתה גרועה יותר (לא רק ממוצע, שיכול להטעות).
- אימון/החזק: 2024–06/2025 מול 07/2025 ואילך, כדי לבדוק יציבות.

ממצא 21/09/2026: Early Signal שלילי בכל frac (0.25–0.75) ובכל תרחיש עלות; PF<1 בכולם.
Latency Capture עם ממוצע חיובי אך חציון שלילי/אפס ו-~50% מהעסקאות נכנסו גרוע יותר —
הממוצע נגרר ע"י מיעוט מקרים קיצוניים, לא מייצג עסקה טיפוסית. Baseline עצמו רגיש מאוד
להנחת המילוי (+8.5₪ במילוי ברמת הלבנה, לעומת -2.7₪ במילוי בסגירה בפועל בבדיקה קודמת) —
כלומר גם Renko הרגיל חי על סף היתרון.

הרצה: python renko_early_signal_test.py /path/to/xauusd_m15.csv
תלות: pandas, numpy בלבד.
"""
import sys, numpy as np, pandas as pd

BOX = 3.0                      # גודל לבנת Renko בדולרים (תואם RENKO_BOX בבוט)
K = 4.5                        # ILS/USD בקירוב — עדכן לפי השער הנוכחי
SPLIT = np.datetime64("2025-07-01")   # אימון/החזק

path = sys.argv[1] if len(sys.argv) > 1 else "xauusd_m15.csv"
m = pd.read_csv(path)
m["t"] = pd.to_datetime(m["timestamp"], utc=True).dt.tz_localize(None)
O, H, L, C, T = m.open.values, m.high.values, m.low.values, m.close.values, m.t.values
n = len(m)


def build_confirmed(box=BOX, conf=2):
    """לכל בר: הכיוון המאושר (2 לבנים רצופות לפי סגירה) ורמת הייחוס (ref) העדכנית."""
    ref = C[0]; dirs = []
    out_dir = np.zeros(n, int); out_ref = np.full(n, ref)
    for i in range(n):
        c = C[i]
        while c - ref >= box: ref += box; dirs.append(1)
        while ref - c >= box: ref -= box; dirs.append(-1)
        out_dir[i] = dirs[-1] if len(dirs) >= conf and all(x == dirs[-1] for x in dirs[-conf:]) else 0
        out_ref[i] = ref
    return out_dir, out_ref


conf_dir, conf_ref = build_confirmed()


def baseline_trades():
    """מערכת היפוך Renko: כניסה/היפוך ברמת הלבנה (ref) — פקודת Stop הניתנת להצבה מראש."""
    pos = 0; entry = None; et = None; tr = []
    for i in range(n):
        d = conf_dir[i]
        if d != 0 and d != pos:
            if pos != 0:
                tr.append(dict(t=et, dir=pos, entry_px=entry, exit_px=conf_ref[i],
                                pnl=pos * (conf_ref[i] - entry), hold_h=(T[i] - et) / np.timedelta64(1, "h")))
            pos = d; entry = conf_ref[i]; et = T[i]
    return tr


def early_trades(frac):
    """
    Early Signal: progress(t) = d × (close(t) − ref) / BOX בכיוון d (על סגירות בלבד).
    טריגר: progress(t) ≥ frac וגם progress(t) > progress(t−1) (תנועה נמשכת, לא נגיעה חד-פעמית).
    מילוי ב-open(t+1) — הבר הבא אחרי שה-trigger נודע.
    """
    pos = 0; entry = None; et = None; tr = []
    prev_prog = None; wait_d = 0; wait_ref = None
    for i in range(n - 1):
        d = conf_dir[i]
        if d != 0 and d != pos:
            if wait_d != d:
                wait_d = d; wait_ref = conf_ref[i]; prev_prog = None
            prog = d * (C[i] - wait_ref) / BOX
            trig = (prog >= frac) and (prev_prog is not None) and (prog > prev_prog)
            if trig:
                fill = O[i + 1]
                if pos != 0:
                    tr.append(dict(t=et, dir=pos, entry_px=entry, exit_px=fill,
                                    pnl=pos * (fill - entry), hold_h=(T[i + 1] - et) / np.timedelta64(1, "h"),
                                    baseline_ref=wait_ref))
                pos = d; entry = fill; et = T[i + 1]; wait_d = 0
            prev_prog = prog
        elif d == pos:
            wait_d = 0; prev_prog = None
    return tr


def metrics(tr, cost, lbl):
    if len(tr) < 10:
        print(lbl, "N<10"); return
    v = np.array([x["pnl"] - cost for x in tr])
    win, loss = v[v > 0], v[v <= 0]
    pf = win.sum() / abs(loss.sum()) if len(loss) and loss.sum() != 0 else float("inf")
    a = np.array([x["pnl"] - cost for x in tr if x["t"] < SPLIT])
    b = np.array([x["pnl"] - cost for x in tr if x["t"] >= SPLIT])
    eq = np.cumsum(v); dd = (np.maximum.accumulate(eq) - eq).max()
    t_ = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    days = (T[-1] - T[0]) / np.timedelta64(1, "D")
    print(f"{lbl:32s} N={len(v):4d} ({len(v)/days:.2f}/d) win={100*np.mean(v>0):4.0f}% "
          f"net {v.mean()*K:+7.1f}\u20aa PF={pf:5.2f} t={t_:+.2f} DD={dd*K:7.0f}\u20aa | "
          f"hold {np.mean([x['hold_h'] for x in tr]):5.1f}h | "
          f"train {a.mean()*K if len(a) else float('nan'):+7.1f} "
          f"hold {b.mean()*K if len(b) else float('nan'):+7.1f}(N={len(b)})")


print(f"=== Baseline Renko (כניסה ברמת הלבנה, מערכת היפוך, box={BOX}$) ===")
base = baseline_trades()
for cost, lab in [(0.77, "Base (מילוי במחיר הרמה)"), (1.77, "Realistic (+1$ סליפג')"), (2.655, "Stress (1.5× ספרד+סליפג')")]:
    metrics(base, cost, f"  {lab}")

print(f"\n=== Early Signal (טריגר לפי progress, אישור תנועה נמשכת, מילוי בפתיחת הבר הבא) ===")
for frac in (0.25, 0.40, 0.50, 0.60, 0.75):
    tr = early_trades(frac)
    print(f"\n-- frac={frac} --")
    for cost, lab in [(0.77, "Base"), (1.77, "Realistic (+1$)"), (2.655, "Stress (1.5×)")]:
        metrics(tr, cost, f"  {lab}")
    if len(tr) >= 10:
        saved = [x["dir"] * (x["baseline_ref"] - x["entry_px"]) for x in tr]
        print(f"  Latency Capture: ממוצע {np.mean(saved):+.2f}$ | חציון {np.median(saved):+.2f}$ | "
              f"% כניסה גרועה מה-baseline {100*np.mean(np.array(saved)<0):.0f}%")
