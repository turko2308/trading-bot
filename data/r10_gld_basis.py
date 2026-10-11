"""
tools/r10_gld_basis.py — סבב 9: נפח אמיתי של GLD (11/10/2026)
=====================================================================
הכללים נקבעו לפני ההרצה ולא משתנים אחריה.

מה חדש: נפח אמיתי (מניות GLD שהחליפו ידיים), לא tick volume של פורקס כמו בסבבים 6 ו-8.
מסחר: XAUUSD M15 (Dukascopy). אותות: GLD M15 (Twelve Data). כל הזמנים UTC, תחילת נר.
עלות: 0.77$ ספרד + 0.5$ החלקה = 1.27$/oz (לחץ: 1.77$). כל היציאות לפני 21:00 UTC (00:00 ישראל) → אין מימון לילה.
₪ לעסקה = $/oz × 1.5oz × 3.03.  אימון < 2025-07-01, החזק מ-2025-07-01.

H9a — השתתפות אמיתית (המשך):
  RV = נפח נר GLD / ממוצע נפח באותה משבצת זמן ב-20 ימי המסחר הקודמים.
  אות: RV >= 2.0 וגוף הנר >= 60% מהטווח. כיוון = כיוון הנר.
  כניסה: פתיחת נר הזהב הבא (סגירת נר ה-GLD). שעות כניסה: 13:45–18:45 UTC (16:45–21:45 ישראל).
  סטופ 1.5×ATR14 (M15 זהב, נרות סגורים). יציאה: סגירת הנר הרביעי (שעה). עסקה אחת בכל רגע, עד 5 ביום.
  רובסטיות: RV ∈ {1.5, 2.0, 2.5} × החזקה ∈ {2, 4, 8} נרות.

H9b — פרופיל נפח (POC של אתמול):
  POC = רמת המחיר עם הכי הרבה נפח ב-GLD בסשן הקודם (תאים של 0.10$, נפח כל נר מפוזר שווה על הטווח שלו).
  המרה לזהב: POC × (סגירת זהב / סגירת GLD בנר 13:30 של היום).
  מהמחיר בסגירת 13:30: מעל POC → Buy Limit ב-POC; מתחת → Sell Limit. פעיל 13:45–19:00 UTC, פעם אחת ביום.
  סטופ 1.5×ATR14, יעד 3×ATR14, יציאה בזמן 20:00 UTC. נר מילוי שנוגע גם בסטופ = סטופ; יעד בנר המילוי לא נספר.
  רובסטיות: סטופ/יעד ∈ {1.0/2.0, 1.5/3.0, 2.0/4.0} × ATR.

מעבר: ≥200 באימון, ≥100 בהחזק, שניהם >0 אחרי 1.27$, t≥2, ≥3/4 שנים חיוביות, צעד אחד בפרמטרים לא הופך סימן.
הרצה: python tools/r9_gld_volume.py data/gld_m15.csv data/xauusd_m15.csv
"""
import sys, csv, datetime as dt, collections, numpy as np

COST, STRESS, K = 1.27, 1.77, 1.5 * 3.03
SPLIT = dt.datetime(2025, 7, 1)

def load(p):
    out = []
    for r in csv.DictReader(open(p)):
        t = dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
        out.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"] or 0)))
    return out

gld = load(sys.argv[1] if len(sys.argv) > 1 else "data/gld_m15.csv")
xau = load(sys.argv[2] if len(sys.argv) > 2 else "data/xauusd_m15.csv")
xau = [b for b in xau if b[2] > b[3]]                       # בלי נרות שטוחים
XI = {b[0]: i for i, b in enumerate(xau)}
LAST = xau[-1][0]
gld = [b for b in gld if b[0] <= LAST]

# ATR14 על נרות סגורים: atr[i] = ממוצע TR של 14 הנרות שלפני i
tr = [0.0] + [max(xau[i][2], xau[i - 1][4]) - min(xau[i][3], xau[i - 1][4]) for i in range(1, len(xau))]
cs = np.cumsum(tr)
def atr(i): return (cs[i - 1] - cs[i - 15]) / 14 if i >= 15 else None

def stats(tr_list, label):
    if not tr_list: print(f"  {label}: 0 עסקאות"); return None
    r = np.array([x[1] for x in tr_list]); t = [x[0] for x in tr_list]
    def part(mask, c):
        v = r[mask] - c
        if len(v) < 2: return len(v), 0.0, 0.0
        return len(v), v.mean() * K, v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
    isn = np.array([x < SPLIT for x in t])
    a = part(isn, COST); b = part(~isn, COST); s = part(np.ones(len(r), bool), STRESS)
    yrs = collections.defaultdict(list)
    for x, v in zip(t, r): yrs[x.year].append(v - COST)
    ypos = sum(1 for v in yrs.values() if np.mean(v) > 0)
    allv = r - COST
    print(f"  {label}: N={len(r)} הצלחה={100*(allv>0).mean():.0f}% | אימון N={a[0]} {a[1]:+.2f}₪ t={a[2]:+.2f} | "
          f"החזק N={b[0]} {b[1]:+.2f}₪ t={b[2]:+.2f} | לחץ {s[1]:+.2f}₪ | שנים חיוביות {ypos}/{len(yrs)} "
          f"{ {y: round(float(np.mean(v))*K,1) for y,v in sorted(yrs.items())} } | ממוצע ביום {len(r)/len({x.date() for x in t}):.1f}")
    return a, b

def run_exit(i0, d, stop, hold=None, target=None, tend=None):
    """כניסה בפתיחת xau[i0]. מחזיר רווח $/oz לפני עלות."""
    e = xau[i0][1]; sp = e - d * stop; tp = None if target is None else e + d * target
    j = i0
    while j < len(xau):
        b = xau[j]
        if (d > 0 and b[3] <= sp) or (d < 0 and b[2] >= sp): return (sp - e) * d, j
        if tp is not None and ((d > 0 and b[2] >= tp) or (d < 0 and b[3] <= tp)): return (tp - e) * d, j
        if hold and j - i0 + 1 >= hold: return (b[4] - e) * d, j
        if tend and b[0] + dt.timedelta(minutes=15) >= tend: return (b[4] - e) * d, j
        if j + 1 < len(xau) and xau[j + 1][0].date() != b[0].date() and xau[j+1][0].hour < 12: return (b[4] - e) * d, j
        j += 1
    return None, None


# ---------------- H10: פער GLD מול זהב ספוט
# כללים (נקבעו לפני ההרצה, 11/10/2026):
#   R = סגירת זהב / סגירת GLD באותו נר. בסיס = ממוצע R ב-520 הנרות המשותפים הקודמים (~20 יום), סטיית תקן באותו חלון.
#   z = (R - בסיס) / סטיית תקן.  z <= -th → GLD יקר יחסית לספוט → קנייה בזהב.  z >= th → מכירה.
#   כניסה בפתיחת נר הזהב הבא, 13:45-18:45 UTC, סטופ 1.5 ATR, החזקה 4 נרות. עסקה אחת בכל רגע, עד 5 ביום.
#   רובסטיות: th ∈ {1.5, 2.0, 2.5} × החזקה ∈ {2, 4, 8}.
pairs = []
for b in gld:
    i = XI.get(b[0])
    if i is not None: pairs.append((b[0], xau[i][4] / b[4]))
zs = {}
win = collections.deque(maxlen=520)
for t, r in pairs:
    if len(win) == 520:
        a = np.array(win); sd = a.std()
        if sd > 0: zs[t] = (r - a.mean()) / sd
    win.append(r)

def h10(th, hold):
    out = []; busy = None; perday = collections.Counter()
    for t, z in sorted(zs.items()):
        et = t + dt.timedelta(minutes=15)
        if not (dt.time(13, 45) <= et.time() <= dt.time(18, 45)) or abs(z) < th: continue
        i0 = XI.get(et)
        if i0 is None or atr(i0) is None: continue
        if busy is not None and et <= busy: continue
        if perday[et.date()] >= 5: continue
        d = 1 if z < 0 else -1
        p, j = run_exit(i0, d, 1.5 * atr(i0), hold=hold)
        if p is None: continue
        busy = xau[j][0]; perday[et.date()] += 1; out.append((et, p))
    return out

print("=== H10 פער GLD/ספוט (סגירת פער בזהב) ===")
for th in (1.5, 2.0, 2.5):
    for hold in (2, 4, 8):
        stats(h10(th, hold), f"|z|>={th} החזקה {hold}" + ("  ← ראשי" if (th, hold) == (2.0, 4) else ""))
