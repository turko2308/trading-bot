"""
tools/cpcv.py — CSCV / PBO: הסתברות שהבקטסט מוטה מבחירה (Backtest Overfitting)
================================================================================================
נבנה 15/09/2026. סוגר פער ידוע מ-24/08: הקובץ הופיע ברשימת 22 הכלים הפיקטיביים
ומעולם לא נבנה. שרשרת האימות שנקבעה היא plateau → slippage → DSR/PBO → CPCV,
וזה היה החוליה החסרה.

מה הוא עונה עליו: כשבודקים N וריאציות של אסטרטגיה ובוחרים את הטובה, חלק
מהעליונות שלה הוא מזל. PBO (Probability of Backtest Overfitting) מודד כמה.
השיטה: CSCV מבית López de Prado — מחלקים את ציר הזמן ל-S מקטעים, עוברים על
כל החלוקות של מחצית-מחצית ל-IS/OOS, בכל חלוקה בוחרים את הווריאציה הטובה
ביותר ב-IS ובודקים איפה היא מדורגת ב-OOS.
  PBO = השכיחות שבה "הזוכה ב-IS" נופלת למחצית התחתונה ב-OOS.
  PBO < 0.10 — הבחירה איתנה. 0.10-0.30 — סביר. > 0.50 — הבחירה היא רעש.

Purging ו-embargo אינם נדרשים כאן: כל וריאציה מיוצגת כסדרת P&L יומית מצטברת,
עסקאות אינן חופפות בין מקטעים ברמה היומית, ואין label overlap כמו בסיווג.

הרצה: python tools/cpcv.py   (מצפה ל-../data/xauusd_h1.csv)
"""
import csv, datetime, itertools, sys
from collections import defaultdict
import numpy as np


def load_h1(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = r["timestamp"].split("+")[0].strip()
            rows.append({"t": datetime.datetime.fromisoformat(ts), "c": float(r["close"])})
    return sorted(rows, key=lambda x: x["t"])


def renko_trades(closes, times, box, confirm, target_b, stop_b, max_hold):
    bricks = []
    ref = closes[0]
    for i in range(1, len(closes)):
        while closes[i] - ref >= box:
            ref += box; bricks.append({"dir": 1, "close": ref, "t": times[i]})
        while ref - closes[i] >= box:
            ref -= box; bricks.append({"dir": -1, "close": ref, "t": times[i]})

    trades = []; i = confirm; in_pos = False
    while i < len(bricks):
        if not in_pos:
            recent = [b["dir"] for b in bricks[i-confirm:i]]
            if all(d == 1 for d in recent) or all(d == -1 for d in recent):
                direction = recent[0]; entry = bricks[i-1]["close"]
                stop = entry - direction*stop_b*box
                target = entry + direction*target_b*box
                in_pos = True; entry_i = i
        else:
            c = bricks[i]["close"]; hit = None
            if direction == 1:
                if c <= stop: hit = True
                elif c >= target: hit = True
            else:
                if c >= stop: hit = True
                elif c <= target: hit = True
            if not hit and (i - entry_i) >= max_hold:
                hit = True
            if hit:
                pnl = (c - entry) * direction
                trades.append((bricks[i]["t"], pnl))
                in_pos = False
        i += 1
    return trades


def daily_series(trades, all_days):
    """הופך רשימת עסקאות לסדרת P&L יומית על ציר זמן אחיד."""
    by_day = defaultdict(float)
    for t, pnl in trades:
        by_day[t.date()] += pnl
    return np.array([by_day.get(d, 0.0) for d in all_days])


def cscv_pbo(M, n_splits=10):
    """M: מטריצה (זמן × וריאציות). מחזיר PBO ופירוט."""
    T, N = M.shape
    chunk = T // n_splits
    chunks = [M[i*chunk:(i+1)*chunk] for i in range(n_splits)]

    half = n_splits // 2
    logits = []
    n_below = 0; n_total = 0
    for is_idx in itertools.combinations(range(n_splits), half):
        oos_idx = [i for i in range(n_splits) if i not in is_idx]
        IS = np.vstack([chunks[i] for i in is_idx])
        OOS = np.vstack([chunks[i] for i in oos_idx])

        def sharpe(x):
            s = x.std()
            return x.mean()/s if s > 0 else 0.0

        is_perf = np.array([sharpe(IS[:, j]) for j in range(N)])
        oos_perf = np.array([sharpe(OOS[:, j]) for j in range(N)])
        best = int(np.argmax(is_perf))
        # דירוג יחסי של הזוכה-ב-IS בתוך ה-OOS
        rank = (oos_perf < oos_perf[best]).sum() / (N - 1) if N > 1 else 1.0
        w = max(min(rank, 1 - 1e-9), 1e-9)
        logits.append(np.log(w / (1 - w)))
        n_total += 1
        if rank < 0.5:
            n_below += 1
    return n_below / n_total, np.array(logits), n_total


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/xauusd_h1.csv"
    h1 = load_h1(path)
    closes = [b["c"] for b in h1]; times = [b["t"] for b in h1]
    print(f"נטען: {len(h1)} נרות H1, {times[0].date()} עד {times[-1].date()}\n")

    # אותו גריד שממנו נבחר renko_scalp (box=3, confirm=2) — זו נקודת הבדיקה
    grid = [
        (3, 2, 6, 3, 6), (3, 3, 6, 3, 6), (3, 3, 6, 3, None),
        (5, 2, 6, 3, 6), (5, 3, 6, 3, 6), (5, 3, 6, 3, None),
        (8, 2, 6, 3, 6), (8, 3, 6, 3, 6),
        (3, 2, 4, 2, 6), (3, 2, 8, 4, 6),
        (5, 2, 4, 2, 6), (5, 2, 8, 4, 6),
    ]
    all_days = sorted({t.date() for t in times})

    cols = []; labels = []; totals = []
    for box, conf, tgt, stp, mh in grid:
        mh_eff = mh if mh else 10**9
        tr = renko_trades(closes, times, box, conf, tgt, stp, mh_eff)
        if len(tr) < 50:
            continue
        cols.append(daily_series(tr, all_days))
        labels.append(f"box={box} conf={conf} tgt={tgt} stop={stp} hold={mh}")
        totals.append(sum(p for _, p in tr))

    M = np.column_stack(cols)
    print(f"וריאציות שנבדקו: {M.shape[1]} | ימים: {M.shape[0]}\n")
    print("סה\"כ רווח לפי וריאציה (גולמי):")
    order = np.argsort(totals)[::-1]
    for j in order:
        mark = "  ← הנבחרת" if labels[j].startswith("box=3 conf=2 tgt=6") else ""
        print(f"  {labels[j]:45s} {totals[j]:9.1f}${mark}")

    pbo, logits, n = cscv_pbo(M, n_splits=10)
    print(f"\n{'='*62}")
    print(f"CSCV: {n} חלוקות IS/OOS")
    print(f"PBO = {pbo:.3f}   ({pbo*100:.1f}% מהמקרים הזוכה-ב-IS נפל למחצית התחתונה ב-OOS)")
    print(f"לוגיט חציוני: {np.median(logits):+.3f}  (חיובי = הזוכה נוטה להישאר טוב מחוץ למדגם)")
    if pbo < 0.10:   verdict = "איתן — הבחירה כמעט ודאי לא מוטית"
    elif pbo < 0.30: verdict = "סביר — הטיה קלה, מקובל"
    elif pbo < 0.50: verdict = "גבולי — חלק ניכר מהעליונות הוא מזל"
    else:            verdict = "נכשל — הבחירה היא רעש"
    print(f"מסקנה: {verdict}")
