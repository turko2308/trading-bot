"""be_anchor.py — האם הברייקאיבן צריך לשבת מעל הכניסה השנייה?

הרעיון (24/08): היום be_px = entry(יחידה 1) + offset. יחידה 2 נכנסה
גבוה יותר, ולכן "ברייקאיבן" יכול לצאת במינוס נטו.
נבדקות שלוש עוגנים, בלי לגעת בשום פרמטר אחר:

    first  — הכניסה הראשונה (החי היום)
    last   — הכניסה האחרונה שנוספה  ← ההצעה
    avg    — ממוצע משוקלל של כל היחידות

כלל §10: כל וריאנט נמדד גם תחת החלקה. הסף לשיטה 2 הוא 2-3$ (§14.2),
לא 6$ — אבל 6$ מוצג גם הוא כעמודה, כי כך נמדדו הווריאנטים הקודמים.
"""
import csv, datetime, sys
sys.path.insert(0, ".")
import trading_bot as B

OZ = 0.75
USD_ILS = 3.0014
SPREAD = B.SPREAD_POINTS


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({"t": datetime.datetime.fromisoformat(r["timestamp"]),
                         "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"])})
    rows.sort(key=lambda x: x["t"])
    return rows


def simulate(h4, anchor="first", entry_days=20, trail_days=4,
             risk_ils=B.SLOW_RISK_ILS, slippage_points=0.0,
             pyramid_units=2, pyramid_step_n=0.5,
             be_trigger=25.0, be_offset=3.0):
    """העתק של _simulate_slow עם שינוי אחד בלבד: מאיזו כניסה נגזר be_px."""
    CPD = B.CANDLES_PER_DAY_4H
    ew, tw = entry_days * CPD, trail_days * CPD
    pos, closed = None, []

    def be_anchor_px(p):
        if anchor == "first":
            return p["entry"]
        if anchor == "last":
            return p["units"][-1]["e"]
        tot = sum(u["ipp"] for u in p["units"])
        return sum(u["e"] * u["ipp"] for u in p["units"]) / tot

    def close(p, px, when):
        pnl = 0.0
        for u in p["units"]:
            pts = (px - u["e"]) if p["dir"] == "long" else (u["e"] - px)
            pnl += (pts - SPREAD) * OZ * USD_ILS
        closed.append({"pnl": pnl, "ct": when, "et": p["et"],
                       "units": len(p["units"]),
                       "days": max(0.0, (when - p["et"]).total_seconds() / 86400)})

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i - ew:i])
        ll = min(x["l"] for x in h4[i - ew:i])
        if pos is None:
            d = "long" if c["c"] > hh else ("short" if c["c"] < ll else None)
            if not d:
                continue
            entry = c["c"] + slippage_points if d == "long" else c["c"] - slippage_points
            t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
            t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
            trail = t_lo if d == "long" else t_hi
            sp = abs(entry - trail)
            if sp < 1e-6:
                continue
            w = h4[max(0, i - 30):i + 1]
            n_atr = B.calc_atr([x["h"] for x in w], [x["l"] for x in w],
                               [x["c"] for x in w]) if pyramid_units > 1 else None
            pos = {"dir": d, "entry": entry, "trail": trail,
                   "units": [{"e": entry, "ipp": risk_ils / sp}],
                   "last_add": entry, "n_atr": n_atr, "be_hit": False, "et": c["t"]}
            continue

        t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
        t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
        pos["trail"] = max(pos["trail"], t_lo) if pos["dir"] == "long" else min(pos["trail"], t_hi)

        if pos["be_hit"]:
            base = be_anchor_px(pos)
            be_px = base + be_offset if pos["dir"] == "long" else base - be_offset
            pos["trail"] = max(pos["trail"], be_px) if pos["dir"] == "long" else min(pos["trail"], be_px)

        if pyramid_units > 1 and pos.get("n_atr") and len(pos["units"]) < pyramid_units:
            step = pyramid_step_n * pos["n_atr"]
            while len(pos["units"]) < pyramid_units:
                if pos["dir"] == "long":
                    trg = pos["last_add"] + step
                    if c["c"] < trg:
                        break
                    add_px = trg + slippage_points
                    dist = add_px - pos["trail"]
                else:
                    trg = pos["last_add"] - step
                    if c["c"] > trg:
                        break
                    add_px = trg - slippage_points
                    dist = pos["trail"] - add_px
                if dist < 1e-6:
                    break
                pos["units"].append({"e": add_px, "ipp": risk_ils / dist})
                pos["last_add"] = trg

        exit_px = None
        if pos["dir"] == "long" and c["l"] <= pos["trail"]:
            exit_px = min(pos["trail"], c["o"])
        elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
            exit_px = max(pos["trail"], c["o"])
        if exit_px is not None:
            close(pos, exit_px, c["t"])
            pos = None
            continue

        if be_trigger is not None and not pos["be_hit"]:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= be_trigger:
                pos["be_hit"] = True

    if pos is not None and h4:
        close(pos, h4[-1]["c"], h4[-1]["t"])

    n = len(closed)
    wins = sum(1 for x in closed if x["pnl"] > 0)
    eq = peak = dd = 0.0
    for x in sorted(closed, key=lambda z: z["ct"]):
        eq += x["pnl"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    pyr = [x for x in closed if x["units"] > 1]
    pyr_loss = sum(1 for x in pyr if x["pnl"] <= 0)
    return {"n": n, "wr": 100 * wins / n if n else 0,
            "pnl": sum(x["pnl"] for x in closed), "dd": dd,
            "pyr": len(pyr), "pyr_loss": pyr_loss}


if __name__ == "__main__":
    h4 = load("xauusd_h4.csv")
    print(f"נרות: {len(h4)} | {h4[0]['t'].date()} → {h4[-1]['t'].date()}")
    print(f"קונפיג: 20/4 · BE 25/3 · 2 יחידות · 0.75oz\n")

    names = {"first": "יחידה 1  ←חי", "last": "יחידה 2  ←ההצעה", "avg": "ממוצע משוקלל"}
    print(f"{'עוגן BE':<18}{'עסק':>5}{'הצל%':>7}{'רווח':>9}{'MaxDD':>9}"
          f"{'slip2':>8}{'slip3':>8}{'slip6':>8}{'פירמ׳ במינוס':>14}")
    print("-" * 87)
    for a in ("first", "last", "avg"):
        r = simulate(h4, anchor=a)
        s2 = simulate(h4, anchor=a, slippage_points=2.0)["pnl"]
        s3 = simulate(h4, anchor=a, slippage_points=3.0)["pnl"]
        s6 = simulate(h4, anchor=a, slippage_points=6.0)["pnl"]
        print(f"{names[a]:<18}{r['n']:>5}{r['wr']:>6.0f}%{r['pnl']:>9.0f}{r['dd']:>9.0f}"
              f"{s2:>8.0f}{s3:>8.0f}{s6:>8.0f}"
              f"{r['pyr_loss']:>7}/{r['pyr']:<6}")

    print(f"\n=== גם עם BE 40/3 (הסף שכבר נמצא עדיף) ===")
    print(f"{'עוגן BE':<18}{'רווח':>9}{'MaxDD':>9}{'slip3':>8}")
    print("-" * 45)
    for a in ("first", "last", "avg"):
        r = simulate(h4, anchor=a, be_trigger=40.0)
        s3 = simulate(h4, anchor=a, be_trigger=40.0, slippage_points=3.0)["pnl"]
        print(f"{names[a]:<18}{r['pnl']:>9.0f}{r['dd']:>9.0f}{s3:>8.0f}")
