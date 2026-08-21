"""be_test.py — האם SLOW_BE_TRIGGER עוזר או מזיק?
מבחן מישור על סף הברייקאיבן, כולל מבחן החלקה (כלל §10).
"""
import trading_bot as B, tools_overlap as O, tools_tfoffset as T
import statistics

m15 = T.load_m15(); h1 = T.to_h1(m15)
h1_il = [{"t": b["t"].astimezone(O.IL), "o": b["o"], "h": b["h"],
          "l": b["l"], "c": b["c"]} for b in h1]
h4 = O.agg_local(h1_il, 4)
K = T.LOT_OZ * T.USD_ILS


def run(be, off, slip=0.0, pyr=1):
    r = B._simulate_slow(h4, entry_days=B.SLOW_ENTRY_DAYS,
                         trail_days=B.SLOW_TRAIL_DAYS,
                         be_trigger=be, be_offset=off,
                         slippage_points=slip, pyramid_units=pyr)
    d = r["detail"]
    pnl = sum(x["pts"] for x in d) * K
    eq = peak = dd = 0.0
    for x in d:
        eq += x["pts"] * K
        peak = max(peak, eq); dd = min(dd, eq - peak)
    w = sum(1 for x in d if x["pts"] > 0)
    return len(d), 100.0*w/len(d) if d else 0, pnl, dd, r["avg_days"]


print("=== מבחן מישור: סף ברייקאיבן (0.75oz, יחידה אחת) ===")
print(f"{'BE':>10} {'עסק':>5} {'הצלחה':>8} {'רווח':>9} {'MaxDD':>9} {'ימים':>7} {'slip3':>9} {'slip6':>9}")
rows = []
for be, off, lbl in [(None, 0.0, "כבוי"), (15.0, 3.0, "15/3"), (20.0, 3.0, "20/3"),
                     (25.0, 3.0, "25/3 ←חי"), (30.0, 3.0, "30/3"),
                     (40.0, 3.0, "40/3"), (60.0, 3.0, "60/3"), (100.0, 3.0, "100/3")]:
    n, wr, p, dd, days = run(be, off)
    _, _, p3, _, _ = run(be, off, slip=3.0)
    _, _, p6, _, _ = run(be, off, slip=6.0)
    rows.append((lbl, p, p6))
    print(f"{lbl:>10} {n:>5} {wr:>7.1f}% {p:>9.0f} {dd:>9.0f} {days:>7.1f} {p3:>9.0f} {p6:>9.0f}")

print("\n=== אותו דבר עם פירמידינג (2 יחידות, כמו בחי) ===")
print(f"{'BE':>10} {'עסק':>5} {'הצלחה':>8} {'רווח':>9} {'MaxDD':>9} {'slip6':>9}")
for be, off, lbl in [(None, 0.0, "כבוי"), (25.0, 3.0, "25/3 ←חי"),
                     (40.0, 3.0, "40/3"), (60.0, 3.0, "60/3")]:
    n, wr, p, dd, _ = run(be, off, pyr=2)
    _, _, p6, _, _ = run(be, off, slip=6.0, pyr=2)
    print(f"{lbl:>10} {n:>5} {wr:>7.1f}% {p:>9.0f} {dd:>9.0f} {p6:>9.0f}")

print("\n=== גם על היסטים אחרים (בדיקת עמידות ליישור) ===")
for off_h in (0, 1, 2, 3):
    h4o = O.agg_local(h1_il, 4) if off_h == 0 else None
    if off_h:
        sh = [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]} for b in h1_il]
        import datetime
        sh = [{**b, "t": b["t"] - datetime.timedelta(hours=off_h)} for b in sh]
        h4o = [{**c, "t": c["t"] + datetime.timedelta(hours=off_h)} for c in O.agg_local(sh, 4)]
    res = []
    for be, off in [(None, 0.0), (25.0, 3.0)]:
        r = B._simulate_slow(h4o, entry_days=20, trail_days=4,
                             be_trigger=be, be_offset=off)
        res.append(sum(x["pts"] for x in r["detail"]) * K)
    print(f"  היסט {off_h}: כבוי {res[0]:>8.0f}  |  25/3 {res[1]:>8.0f}  |  "
          f"הפרש {res[1]-res[0]:+8.0f}")
