import csv, datetime, statistics

SPREAD_PTS = 0.77
RISK_ILS = 40.0
CPD = 6  # candles per day 4h

def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": datetime.datetime.fromisoformat(r["timestamp"]),
                "o": float(r["open"]), "h": float(r["high"]),
                "l": float(r["low"]), "c": float(r["close"]),
            })
    rows.sort(key=lambda x: x["t"])
    return rows

def sim(h4, entry_days=20, trail_days=3, risk_ils=RISK_ILS,
        slippage=0.0, be_trigger=None, be_offset=0.0):
    """be_trigger: after price moves X$ in favor (intrabar MFE), move stop to
       entry +/- be_offset. Stop only tightens."""
    ew = entry_days * CPD
    tw = trail_days * CPD
    pos = None
    closed = []

    def close(p, exit_px, when, open_end=False):
        pts = (exit_px - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_px)
        pnl = (pts - SPREAD_PTS) * p["ipp"]
        closed.append({"pnl": pnl, "pts": pts, "dir": p["dir"],
                       "et": p["et"], "ct": when,
                       "days": max(0.0, (when - p["et"]).total_seconds()/86400.0),
                       "be_hit": p.get("be_hit", False), "open_end": open_end})

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i-ew:i])
        ll = min(x["l"] for x in h4[i-ew:i])
        if pos is None:
            d = "long" if c["c"] > hh else ("short" if c["c"] < ll else None)
            if d:
                entry = c["c"] + slippage if d == "long" else c["c"] - slippage
                t_lo = min(x["l"] for x in h4[max(0,i-tw):i])
                t_hi = max(x["h"] for x in h4[max(0,i-tw):i])
                trail = t_lo if d == "long" else t_hi
                sp = abs(entry - trail)
                if sp < 1e-6:
                    continue
                pos = {"dir": d, "entry": entry, "trail": trail,
                       "ipp": risk_ils/sp, "et": c["t"], "be_hit": False}
            continue

        # trail update (only tightens)
        t_lo = min(x["l"] for x in h4[max(0,i-tw):i])
        t_hi = max(x["h"] for x in h4[max(0,i-tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        # breakeven check BEFORE exit check, using this bar's excursion.
        # conservative: if BE triggers on same bar, the BE stop is only armed
        # for the *next* bar (can't know intrabar order).
        armed_be = pos["be_hit"]
        if armed_be:
            be_px = pos["entry"] + be_offset if pos["dir"] == "long" else pos["entry"] - be_offset
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

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

    if pos is not None:
        close(pos, h4[-1]["c"], h4[-1]["t"], open_end=True)

    n = len(closed)
    wins = [x for x in closed if x["pnl"] > 0]
    return {"n": n, "wr": 100*len(wins)/n if n else 0,
            "pnl": sum(x["pnl"] for x in closed),
            "per": sum(x["pnl"] for x in closed)/n if n else 0,
            "avg_win": statistics.mean([x["pnl"] for x in wins]) if wins else 0,
            "avg_loss": statistics.mean([x["pnl"] for x in closed if x["pnl"]<=0]) or 0,
            "worst": min((x["pnl"] for x in closed), default=0),
            "avg_days": statistics.mean([x["days"] for x in closed]) if n else 0,
            "be_hits": sum(1 for x in closed if x["be_hit"]),
            "detail": closed}

def maxdd(det):
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(det, key=lambda x: x["ct"]):
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd

if __name__ == "__main__":
    h4 = load("xauusd_h4.csv")
    print(f"נרות: {len(h4)}  |  {h4[0]['t'].date()} → {h4[-1]['t'].date()}\n")
    base = sim(h4)
    print(f"{'וריאנט':<22}{'עסק':>5}{'הצל%':>7}{'רווח':>10}{'לעסקה':>9}{'גרוע':>9}{'MaxDD':>10}{'BE':>5}{'ימים':>7}")
    print("-"*84)
    def line(name, r):
        print(f"{name:<22}{r['n']:>5}{r['wr']:>6.0f}%{r['pnl']:>10.0f}{r['per']:>9.1f}{r['worst']:>9.0f}{maxdd(r['detail']):>10.0f}{r['be_hits']:>5}{r['avg_days']:>7.1f}")
    line("בסיס (בלי BE)", base)
    for trig in [10, 15, 20, 25, 30, 40, 50, 60, 80, 100]:
        line(f"BE @ {trig}$", sim(h4, be_trigger=trig))
