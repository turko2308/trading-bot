"""
tf_offset.py — מבחן מישור ליישור הנר בשיטה 3.
מזיז את גבול הנר ב-0..N-1 שעות ומודד את התוצאה, בלי לגעת בשום פרמטר.
מנוע: איתות בסגירת נר TF, יציאה נפתרת על נרות m15 (מדויק, בלי אמביגואיות),
עסקה אחת בכל רגע נתון (כמו המנוע המתועד).
"""
import csv, datetime, statistics

USD_ILS = 3.0014
LOT_OZ = 0.75
SPREAD = 0.77
BARS = 20          # TF_BREAKOUT_BARS
EMA_N = 50         # TF_EMA_PERIOD
ATR_N = 14         # TF_ATR_PERIOD
STOP_ATR = 2.0
TARGET_ATR = 2.0


def load_m15(path="m15.csv"):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            t = datetime.datetime.fromisoformat(r["timestamp"])
            out.append({"t": t, "o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"])})
    out.sort(key=lambda b: b["t"])
    return out


def to_h1(m15):
    """בונה נרות שעה מ-m15."""
    out, cur = [], None
    for b in m15:
        slot = b["t"].replace(minute=0, second=0, microsecond=0)
        if cur is None or cur["t"] != slot:
            if cur:
                out.append(cur)
            cur = {"t": slot, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
    if cur:
        out.append(cur)
    return out


def aggregate(h1, hours, offset=0):
    """כמו _tf_aggregate בבוט, אבל עם היסט. offset=0 == הבוט היום."""
    out, cur = [], None
    for b in h1:
        slot = b["t"].replace(minute=0, second=0, microsecond=0)
        slot = slot.replace(hour=((slot.hour - offset) % 24 // hours) * hours)
        # שחזור העוגן האמיתי (כולל מעבר יום)
        anchor = b["t"].replace(minute=0, second=0, microsecond=0)
        shifted = anchor - datetime.timedelta(hours=offset)
        bucket = shifted.replace(hour=(shifted.hour // hours) * hours)
        slot = bucket + datetime.timedelta(hours=offset)
        if cur is None or cur["t"] != slot:
            if cur:
                out.append(cur)
            cur = {"t": slot, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
    if cur:
        out.append(cur)
    return out


def ema_last(vals, n):
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def atr_simple(bars, n=ATR_N):
    if len(bars) < n + 1:
        return None
    tr = []
    for i in range(len(bars) - n, len(bars)):
        pc = bars[i - 1]["c"]
        tr.append(max(bars[i]["h"] - bars[i]["l"],
                      abs(bars[i]["h"] - pc), abs(pc - bars[i]["l"])))
    return sum(tr) / n


def signals(tf_bars):
    """מייצר איתותים בדיוק לפי לוגיקת tf_scan."""
    sigs = []
    need = max(BARS + 1, EMA_N, ATR_N + 1)
    for i in range(need, len(tf_bars)):
        closed = tf_bars[:i + 1]
        last = closed[-1]
        closes = [b["c"] for b in closed]
        ema = ema_last(closes, EMA_N)
        prior = closed[-(BARS + 1):-1]
        hh = max(b["h"] for b in prior)
        ll = min(b["l"] for b in prior)
        c = last["c"]
        d = None
        if c > hh and c > ema:
            d = "long"
        elif c < ll and c < ema:
            d = "short"
        if not d:
            continue
        atr = atr_simple(closed)
        if not atr:
            continue
        stop = c - STOP_ATR * atr if d == "long" else c + STOP_ATR * atr
        tgt = c + TARGET_ATR * atr if d == "long" else c - TARGET_ATR * atr
        sigs.append({"t": last["t"], "dir": d, "entry": c, "stop": stop, "target": tgt})
    return sigs


def backtest(sigs, m15, tf_hours, slip=0.0, one_at_a_time=True):
    """יציאה נפתרת על m15. הנר של האיתות נגמר ב-t+tf_hours; משם מתחילים."""
    idx = 0
    trades = []
    busy_until = None
    for s in sigs:
        start = s["t"] + datetime.timedelta(hours=tf_hours)
        if one_at_a_time and busy_until and start < busy_until:
            continue
        while idx < len(m15) and m15[idx]["t"] < start:
            idx += 1
        j = idx
        long = s["dir"] == "long"
        entry = s["entry"] + (slip if long else -slip)
        out = None
        while j < len(m15):
            b = m15[j]
            if long:
                if b["l"] <= s["stop"]:
                    out = (b["t"], s["stop"], "stop"); break
                if b["h"] >= s["target"]:
                    out = (b["t"], s["target"], "target"); break
            else:
                if b["h"] >= s["stop"]:
                    out = (b["t"], s["stop"], "stop"); break
                if b["l"] <= s["target"]:
                    out = (b["t"], s["target"], "target"); break
            j += 1
        if out is None:
            continue
        px = out[1] + (-slip if long else slip)
        move = (px - entry) if long else (entry - px)
        pnl = (move - SPREAD) * LOT_OZ * USD_ILS
        trades.append({"t": s["t"], "pnl": pnl, "win": out[2] == "target", "exit": out[0]})
        busy_until = out[0]
    return trades


def summarize(tr):
    if not tr:
        return (0, 0.0, 0.0, 0.0)
    n = len(tr)
    wins = sum(1 for x in tr if x["win"])
    total = sum(x["pnl"] for x in tr)
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in tr:
        eq += x["pnl"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return (n, 100.0 * wins / n, total, dd)


if __name__ == "__main__":
    m15 = load_m15()
    h1 = to_h1(m15)
    print(f"m15 bars: {len(m15)}  h1 bars: {len(h1)}")
    print(f"range: {h1[0]['t']} .. {h1[-1]['t']}\n")

    for hours in (4, 6):
        print(f"=== {hours}H — סריקת היסט ===")
        print(f"{'היסט':>5} {'עסק':>5} {'הצלחה':>7} {'רווח ILS':>11} {'MaxDD':>9} "
              f"{'slip3':>9} {'slip6':>9}")
        rows = []
        for off in range(hours):
            tf = aggregate(h1, hours, off)
            sg = signals(tf)
            t0 = backtest(sg, m15, hours, 0.0)
            t3 = backtest(sg, m15, hours, 3.0)
            t6 = backtest(sg, m15, hours, 6.0)
            n, wr, tot, dd = summarize(t0)
            _, _, p3, _ = summarize(t3)
            _, _, p6, _ = summarize(t6)
            rows.append((off, n, wr, tot, dd, p3, p6))
            print(f"{off:>5} {n:>5} {wr:>6.1f}% {tot:>11.0f} {dd:>9.0f} "
                  f"{p3:>9.0f} {p6:>9.0f}")
        tots = [r[3] for r in rows]
        print(f"  טווח: {min(tots):.0f} .. {max(tots):.0f}  "
              f"(פי {max(tots)/min(tots) if min(tots) > 0 else float('nan'):.2f})")
        print(f"  חציון {statistics.median(tots):.0f} | "
              f"כמה חיוביים: {sum(1 for t in tots if t > 0)}/{len(tots)}\n")
