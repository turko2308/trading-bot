#!/usr/bin/env python3
"""
tools/dsr.py  —  Deflated Sharpe Ratio + MinTRL + PBO   (26/08)

סוגר את החוב של כלל 25: ‏§4ה מצטט DSR 0.760/0.763 ו-PBO 27%/30%
מאז 12/08, בלי שהכלי שהפיק אותם קיים. עד היום המספרים לא היו
ניתנים לשחזור. זה הכלי.

מה זה עונה: אחרי שבדקנו ~21 וריאנטים על אותם 2.5 שנים, כמה
מה-Sharpe שנשאר הוא קצה אמיתי וכמה הוא הטוב-מתוך-21?

נוסחאות — Bailey & Lopez de Prado (2014):

  DSR = Z[ (SR - SR0) * sqrt(T-1)
           / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) ]

  SR0 = sqrt(V[SR_n]) * ( (1-gamma)*Z^-1[1 - 1/N]
                          + gamma*Z^-1[1 - 1/(N*e)] )

  SR   — שארפ לעסקה (לא מנורמל לשנה)
  T    — מספר העסקאות
  g3   — צידוד (skewness), אומדן מדגמי מתוקן-הטיה
  g4   — קורטוזיס גולמי (לא עודף)
  N    — מספר הניסויים
  V    — שונות השארפ בין הניסויים; נאמדת מהגריד (ר' אזהרה למטה)
  gamma — קבוע אוילר-מסקרוני 0.5772156649

⚠️ ‏V[SR] היא ההנחה הרכה של החישוב. אין רישום של 21 השארפים
   בפועל (‏§6 מצהיר במפורש: "הערכה, לא ספירה"), ולכן היא נאמדת
   מגריד הפרמטרים של כל שיטה. גריד שונה -> V שונה -> DSR שונה.
   הכלי מדפיס את V ואת גודל הגריד תמיד. אל תצטט DSR בלי שניהם.

⚠️ שיטה 2 נמדדת על 39 עסקאות עם skew ‏4.92+ וקורטוזיס 30.3.
   ‏MinTRL בתנאים כאלה מחזיר מספרים מטעים — ‏§4ה כבר סימן את זה.
   הכלי מדפיס אותו עם דגל, לא כמסקנה.

הרצה:
    python3 tools/dsr.py data/xauusd_m15.csv data/xauusd_h4.csv
"""
import bisect, csv, datetime as dt, importlib.util, itertools, math
import statistics as st, sys

EULER = 0.5772156649015329
SPREAD, USD_ILS, OZ = 0.77, 3.0014, 0.75
TF_EMA_PERIOD, TF_BREAKOUT_BARS = 50, 20


# ── סטטיסטיקה בסיסית ─────────────────────────────────────────

def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _nppf(p):
    """הופכי של הנורמלי התקני — Acklam, מדויק ל-~1e-9."""
    if not 0.0 < p < 1.0:
        raise ValueError(p)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def moments(v):
    """SR לעסקה + צידוד/קורטוזיס באומדן מדגמי מתוקן-הטיה (כמו scipy bias=False)."""
    n = len(v)
    if n < 4:
        return None
    m = st.mean(v)
    sd = st.stdev(v)                      # ddof=1
    if sd == 0:
        return None
    p = st.pstdev(v)
    g1 = sum((x - m) ** 3 for x in v) / n / p ** 3
    g2 = sum((x - m) ** 4 for x in v) / n / p ** 4 - 3.0
    G1 = g1 * math.sqrt(n * (n - 1)) / (n - 2)
    G2 = ((n + 1) * g2 + 6) * (n - 1) / ((n - 2) * (n - 3))
    return {"n": n, "sr": m / sd, "skew": G1, "kurt": G2 + 3.0, "pnl": sum(v)}


def sr0(var_sr, N):
    """התוחלת של השארפ המקסימלי מתוך N ניסויים תחת אפס."""
    return math.sqrt(var_sr) * ((1 - EULER) * _nppf(1 - 1.0 / N)
                                + EULER * _nppf(1 - 1.0 / (N * math.e)))


def dsr(sr, skew, kurt, T, threshold):
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if den <= 0:
        return None
    return _ncdf((sr - threshold) * math.sqrt(T - 1) / math.sqrt(den))


def mintrl(sr, skew, kurt, threshold, conf=0.95):
    """כמה עסקאות דרושות כדי ש-DSR יעבור conf. מטעה בזנבות כבדים."""
    if sr <= threshold:
        return None
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    return 1 + den * (_nppf(conf) / (sr - threshold)) ** 2


# ── טעינת נתונים ─────────────────────────────────────────────

def load(path):
    return [{"t": dt.datetime.fromisoformat(r["timestamp"]).replace(tzinfo=None),
             "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]), "c": float(r["close"])}
            for r in csv.DictReader(open(path))]


def agg(bars, hours):
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
    return sum(max(bars[j]["h"] - bars[j]["l"],
                   abs(bars[j]["h"] - bars[j - 1]["c"]),
                   abs(bars[j]["l"] - bars[j - 1]["c"]))
               for j in range(i - 13, i + 1)) / 14


# ── שיטה 3 ───────────────────────────────────────────────────

def m3_returns(bars, h1, h1_t, hours, brk=TF_BREAKOUT_BARS, atr_mult=2.0,
               dated=False):
    """כלל 23: סריקת היציאה מתחילה מסגירת נר האיתות. נר פסימי."""
    closes = [b["c"] for b in bars]
    es = ema(closes, TF_EMA_PERIOD)
    out = []
    for i in range(TF_EMA_PERIOD + brk, len(bars)):
        prior = bars[i - brk:i]
        c, e = bars[i]["c"], es[i]
        if e is None:
            continue
        if c > max(b["h"] for b in prior) and c > e:
            d = 1
        elif c < min(b["l"] for b in prior) and c < e:
            d = -1
        else:
            continue
        a = atr14(bars, i)
        if not a:
            continue
        stop, tgt = c - d * atr_mult * a, c + d * atr_mult * a
        t0 = bars[i]["t"] + dt.timedelta(hours=hours)
        px = None
        for j in range(bisect.bisect_left(h1_t, t0), len(h1)):
            b = h1[j]
            if d == 1:
                if b["l"] <= stop: px = stop; break
                if b["h"] >= tgt:  px = tgt;  break
            else:
                if b["h"] >= stop: px = stop; break
                if b["l"] <= tgt:  px = tgt;  break
        if px is None:
            continue
        r = ((px - c) * d - SPREAD) * OZ * USD_ILS
        out.append((h1[j]["t"], r) if dated else r)
    return out


# ── PBO — CSCV (Bailey, Borwein, Lopez de Prado, Zhu 2016) ───

def to_buckets(dated, edges):
    """
    ממפה (תאריך, רווח) לדליים קלנדריים קבועים.
    ⚠️ חובה. כל וריאנט מייצר מספר עסקאות שונה בזמנים שונים —
       יישור לפי אינדקס עסקה משווה תאריכים שונים זה לזה ומייצר
       PBO חסר משמעות (הסימן: חציון IS == חציון OOS בדיוק).
    """
    out = [0.0] * (len(edges) - 1)
    for t, p in dated:
        i = bisect.bisect_right(edges, t) - 1
        if 0 <= i < len(out):
            out[i] += p
    return out


def pbo(cols, S=10):
    """
    cols: רשימת סדרות מיושרות-לוח-שנה, אחת לכל וריאנט.
    מחלקים את הזמן ל-S בלוקים, עוברים על כל הצירופים של S/2
    בלוקים כתוך-מדגם, בוחרים את הווריאנט הטוב ב-IS, ובודקים
    את דירוגו ב-OOS. PBO = שכיחות הנפילה מתחת לחציון ב-OOS.
    """
    n_v = len(cols)
    T = min(len(c) for c in cols)
    M = [c[:T] for c in cols]
    bs = T // S
    blocks = [list(range(i * bs, (i + 1) * bs if i < S - 1 else T)) for i in range(S)]

    def _sr(idx, row):
        v = [row[i] for i in idx]
        if len(v) < 3:
            return None
        sd = st.stdev(v)
        return st.mean(v) / sd if sd else None

    logits, oos_loss, sel_is, sel_oos = [], 0, [], []
    for ins in itertools.combinations(range(S), S // 2):
        outs = [i for i in range(S) if i not in ins]
        i_idx = [x for b in ins for x in blocks[b]]
        o_idx = [x for b in outs for x in blocks[b]]
        i_sr = [_sr(i_idx, r) for r in M]
        o_sr = [_sr(o_idx, r) for r in M]
        ok = [k for k in range(n_v) if i_sr[k] is not None and o_sr[k] is not None]
        if len(ok) < 3:
            continue
        best = max(ok, key=lambda k: i_sr[k])          # הבחירה נעשית ב-IS בלבד
        ranked = sorted(ok, key=lambda k: o_sr[k])
        rel = (ranked.index(best) + 1) / (len(ok) + 1)
        rel = min(max(rel, 1e-6), 1 - 1e-6)
        logits.append(math.log(rel / (1 - rel)))
        if o_sr[best] < 0:
            oos_loss += 1
        sel_is.append(i_sr[best])                      # של הנבחר, לא של הגריד
        sel_oos.append(o_sr[best])
    if not logits:
        return None
    return {"pbo": sum(1 for x in logits if x <= 0) / len(logits),
            "oos_loss": oos_loss / len(logits),
            "is_med": st.median(sel_is), "oos_med": st.median(sel_oos),
            "splits": len(logits)}


# ── דוח ──────────────────────────────────────────────────────

def report(label, rets, grid_srs, grid_desc, Ns=(21, 40, 80)):
    m = moments(rets)
    if not m:
        print(f"{label}: מדגם קטן מדי\n")
        return
    V = st.variance(grid_srs)
    print(f"\n{'='*62}\n{label}")
    print(f"{'='*62}")
    print(f"  n={m['n']}  SR/עסקה={m['sr']:.3f}  skew={m['skew']:+.2f}  "
          f"kurt={m['kurt']:.1f}  pnl={m['pnl']:+.0f}")
    print(f"  גריד לאמידת V[SR]: {grid_desc} · {len(grid_srs)} תאים · "
          f"V={V:.5f} (sd={math.sqrt(V):.3f})")
    rank = sum(1 for s in grid_srs if s < m["sr"])
    print(f"  מיקום הווריאנט החי בגריד של עצמו: {rank}/{len(grid_srs)}")
    print(f"\n  {'N':>6} {'רף הטוב-מתוך-N':>16} {'DSR':>8}   {'':<4}")
    for N in Ns:
        th = sr0(V, N)
        d = dsr(m["sr"], m["skew"], m["kurt"], m["n"], th)
        mark = "✅" if d and d >= 0.95 else "❌"
        eaten = 100 * th / m["sr"] if m["sr"] else 0
        print(f"  {N:>6} {th:>16.3f} {d if d is not None else float('nan'):>8.3f}   "
              f"{mark}  (הרף בולע {eaten:.0f}% מה-SR)")
    th21 = sr0(V, 21)
    need = mintrl(m["sr"], m["skew"], m["kurt"], th21)
    flag = "  ⚠️ מטעה — זנבות כבדים" if m["kurt"] > 10 else ""
    print(f"\n  MinTRL (N=21, 95%): "
          f"{('%.0f עסקאות' % need) if need else 'לא בר-חישוב (SR מתחת לרף)'}{flag}")

    print(f"\n  רגישות ה-DSR ל-V[SR] (N=21) — V אינה נמדדת, היא נבחרת:")
    print(f"    {'sd(SR) בגריד':>14} {'רף':>8} {'DSR':>8}")
    for mult, note in ((0.5, ""), (1.0, "  ← הגריד שלמעלה"), (1.5, ""), (2.0, "")):
        sd = math.sqrt(V) * mult
        th = sr0(sd * sd, 21)
        d = dsr(m["sr"], m["skew"], m["kurt"], m["n"], th)
        print(f"    {sd:>14.3f} {th:>8.3f} {d:>8.3f}{note}")


def main(m15_path, h4_path):
    print("tools/dsr.py — Deflated Sharpe + PBO")
    print("‏DSR מנכה את החיפוש: כמה מה-SR נשאר אחרי שמורידים את "
          "הטוב-מתוך-N.\nסף מקובל: 0.95.\n")

    # ---------- שיטה 3 ----------
    m15 = load(m15_path)
    h1 = agg(m15, 1)
    h1_t = [b["t"] for b in h1]
    b6 = agg(m15, 6)

    # דליים קלנדריים שבועיים — הבסיס המשותף ליישור כל הווריאנטים
    t0, t1 = h1[0]["t"], h1[-1]["t"]
    edges, cur = [], t0
    while cur <= t1 + dt.timedelta(days=7):
        edges.append(cur); cur += dt.timedelta(days=7)
    print(f"דליים שבועיים ליישור PBO: {len(edges)-1} "
          f"({t0:%d/%m/%y} – {t1:%d/%m/%y})\n")
    live3 = m3_returns(b6, h1, h1_t, 6)

    grid3, cols3 = [], []
    for brk in (10, 14, 18, 20, 24, 28, 32, 36, 40, 44):
        for am in (1.0, 1.5, 2.0, 2.5, 3.0):
            r = m3_returns(b6, h1, h1_t, 6, brk=brk, atr_mult=am, dated=True)
            mm = moments([p for _, p in r])
            if mm:
                grid3.append(mm["sr"])
                cols3.append(to_buckets(r, edges))
    report("שיטה 3 — Core A על 6H, סטופ ויעד 2×ATR14", live3, grid3,
           "פריצה 10-44 × ATR 1.0-3.0")
    p3 = pbo(cols3)
    if p3:
        print(f"\n  PBO={100*p3['pbo']:.1f}%  הפסד ב-OOS={100*p3['oos_loss']:.1f}%  "
              f"חציון SR: IS {p3['is_med']:+.3f} → OOS {p3['oos_med']:+.3f}  "
              f"({p3['splits']} חלוקות)")

    # ---------- שיטה 2 ----------
    spec = importlib.util.spec_from_file_location("tb", "trading_bot.py")
    tb = importlib.util.module_from_spec(spec)
    sys.modules["tb"] = tb
    spec.loader.exec_module(tb)
    h4 = load(h4_path)

    def slow(ed, td, dated=False):
        o = tb._simulate_slow(h4, entry_days=ed, trail_days=td,
                              be_trigger=tb.SLOW_BE_TRIGGER,
                              be_offset=tb.SLOW_BE_OFFSET)
        if dated:
            return [(d["ct"], d["pnl"]) for d in o["detail"]]
        return [d["pnl"] for d in o["detail"]]

    live2 = slow(tb.SLOW_ENTRY_DAYS, tb.SLOW_TRAIL_DAYS)
    grid2, cols2 = [], []
    for ed in range(5, 21):
        for td in range(2, 9):
            r = slow(ed, td, dated=True)
            mm = moments([p for _, p in r])
            if mm:
                grid2.append(mm["sr"])
                cols2.append(to_buckets(r, edges))
    report(f"שיטה 2 — דונקיאן {tb.SLOW_ENTRY_DAYS}/{tb.SLOW_TRAIL_DAYS} + "
           f"BE {tb.SLOW_BE_TRIGGER:.0f}/{tb.SLOW_BE_OFFSET:.0f} (בלי פירמידינג)",
           live2, grid2, "כניסה 5-20 × נגרר 2-8")
    p2 = pbo(cols2)
    if p2:
        print(f"\n  PBO={100*p2['pbo']:.1f}%  הפסד ב-OOS={100*p2['oos_loss']:.1f}%  "
              f"חציון SR: IS {p2['is_med']:+.3f} → OOS {p2['oos_med']:+.3f}  "
              f"({p2['splits']} חלוקות)")

    print(f"\n{'='*62}")
    print("‏DSR נמוך אינו אומר שהשיטה מפסידה. הוא אומר שאין מספיק")
    print("עסקאות כדי להבדיל בין קצה אמיתי לטוב-מתוך-N. הדרך היחידה")
    print("להעלות אותו: **נתונים חדשים**. עוד וריאנט על אותן 2.5 שנים")
    print("רק מעלה את N ומוריד את ה-DSR.")


if __name__ == "__main__":
    a = sys.argv[1:] or ["data/xauusd_m15.csv", "data/xauusd_h4.csv"]
    main(a[0], a[1])
