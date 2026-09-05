"""
tools/execution_slippage_consistency_test.py
==============================================
Diagnoses the slippage-methodology bug found 03/09/2026:
tb._simulate_slow(slippage_points=X) shifts the ENTRY price, which shifts
the breakeven anchor and pyramid triggers, which changes WHEN positions
exit -- so different slippage levels produce different trade sequences,
not just different costs on the same trades.

clean_simulate_slow() is a copy of tb._simulate_slow with slippage
removed from entry/BE/pyramid logic entirely (always effectively 0 there)
and instead applied as a pure cost deduction per unit at CLOSE time only
-- same mechanism as the existing spread_pts deduction, just an extra
term. Signal generation (entry/exit timing, direction, pyramid adds) is
now byte-identical across all cost levels; only $ P&L differs.
"""
import sys, os, csv, datetime
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import trading_bot as tb


def load_h4():
    h4 = []
    with open(os.path.join(REPO_ROOT, "data", "xauusd_h4.csv")) as f:
        for row in csv.DictReader(f):
            h4.append({"t": datetime.datetime.fromisoformat(row["timestamp"]).replace(tzinfo=None),
                       "o": float(row["open"]), "h": float(row["high"]),
                       "l": float(row["low"]), "c": float(row["close"])})
    return h4


def clean_simulate_slow(h4, extra_cost_points=0.0, entry_days=10, trail_days=5,
                         risk_ils=tb.SLOW_RISK_ILS, pyramid_units=1, pyramid_step_n=0.5,
                         be_trigger=None, be_offset=0.0):
    ew = entry_days * tb.CANDLES_PER_DAY_4H
    tw = trail_days * tb.CANDLES_PER_DAY_4H
    spread_pts = tb._spread_points()
    total_deduction = spread_pts + extra_cost_points
    pos = None
    closed = []

    def _close_pos(p, exit_px, when, open_at_end=False):
        total = 0.0
        for u in p["units"]:
            pts_u = (exit_px - u["e"]) if p["dir"] == "long" else (u["e"] - exit_px)
            total += (pts_u - total_deduction) * u["ipp"]
        days_held = max(0.0, (when - p["et"]).total_seconds() / 86400.0)
        rec = {"pnl": total, "dir": p["dir"], "et": p["et"], "ct": when,
               "units": len(p["units"]), "days": days_held}
        if open_at_end:
            rec["open_at_end"] = True
        closed.append(rec)

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i - ew:i])
        ll = min(x["l"] for x in h4[i - ew:i])
        if pos is None:
            direction = None
            if c["c"] > hh:
                direction = "long"
            elif c["c"] < ll:
                direction = "short"
            if direction:
                entry = c["c"]  # NO slippage here -- signal path is clean
                t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
                t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
                trail = t_lo if direction == "long" else t_hi
                stop_pts = abs(entry - trail)
                if stop_pts < 1e-6:
                    continue
                w = h4[max(0, i - 30):i + 1]
                n_atr = tb.calc_atr([x["h"] for x in w], [x["l"] for x in w],
                                     [x["c"] for x in w]) if pyramid_units > 1 else None
                pos = {"dir": direction, "entry": entry, "trail": trail,
                       "units": [{"e": entry, "ipp": risk_ils / stop_pts}],
                       "last_add": entry, "n_atr": n_atr, "be_hit": False, "et": c["t"]}
            continue

        t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
        t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        if pos.get("be_hit"):
            _anchor = pos["units"][-1]["e"] if pos.get("units") else pos["entry"]
            be_px = (_anchor + be_offset) if pos["dir"] == "long" else (_anchor - be_offset)
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

        if pyramid_units > 1 and pos.get("n_atr") and len(pos["units"]) < pyramid_units:
            step = pyramid_step_n * pos["n_atr"]
            while len(pos["units"]) < pyramid_units:
                if pos["dir"] == "long":
                    trigger = pos["last_add"] + step
                    if c["c"] < trigger:
                        break
                    add_px = trigger
                    dist = add_px - pos["trail"]
                else:
                    trigger = pos["last_add"] - step
                    if c["c"] > trigger:
                        break
                    add_px = trigger
                    dist = pos["trail"] - add_px
                if dist < 1e-6:
                    break
                pos["units"].append({"e": add_px, "ipp": risk_ils / dist})
                pos["last_add"] = trigger

        exit_px = None
        if pos["dir"] == "long" and c["l"] <= pos["trail"]:
            exit_px = min(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
            exit_px = max(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        if exit_px is not None:
            _close_pos(pos, exit_px, c["t"])
            pos = None
            continue

        if be_trigger is not None and not pos["be_hit"]:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= be_trigger:
                pos["be_hit"] = True

    if pos is not None and h4:
        last = h4[-1]
        _close_pos(pos, last["c"], last["t"], open_at_end=True)

    wins = [x for x in closed if x["pnl"] > 0]
    n = len(closed)
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1) if n else 0,
            "pnl": round(sum(x["pnl"] for x in closed), 2), "detail": closed}


def clean_simulate_slow_er(h4, er, threshold, extra_cost_points=0.0, entry_days=10,
                            trail_days=5, risk_ils=tb.SLOW_RISK_ILS, pyramid_units=1,
                            pyramid_step_n=0.5, be_trigger=None, be_offset=0.0):
    """Same as clean_simulate_slow but gates entry on er[i] >= threshold."""
    ew = entry_days * tb.CANDLES_PER_DAY_4H
    tw = trail_days * tb.CANDLES_PER_DAY_4H
    spread_pts = tb._spread_points()
    total_deduction = spread_pts + extra_cost_points
    pos = None
    closed = []
    blocked = 0

    def _close_pos(p, exit_px, when, open_at_end=False):
        total = 0.0
        for u in p["units"]:
            pts_u = (exit_px - u["e"]) if p["dir"] == "long" else (u["e"] - exit_px)
            total += (pts_u - total_deduction) * u["ipp"]
        days_held = max(0.0, (when - p["et"]).total_seconds() / 86400.0)
        rec = {"pnl": total, "dir": p["dir"], "et": p["et"], "ct": when,
               "units": len(p["units"]), "days": days_held}
        if open_at_end:
            rec["open_at_end"] = True
        closed.append(rec)

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i - ew:i])
        ll = min(x["l"] for x in h4[i - ew:i])
        if pos is None:
            direction = None
            if c["c"] > hh:
                direction = "long"
            elif c["c"] < ll:
                direction = "short"
            if direction:
                if er[i] is None or er[i] < threshold:
                    blocked += 1
                    continue
                entry = c["c"]
                t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
                t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
                trail = t_lo if direction == "long" else t_hi
                stop_pts = abs(entry - trail)
                if stop_pts < 1e-6:
                    continue
                w = h4[max(0, i - 30):i + 1]
                n_atr = tb.calc_atr([x["h"] for x in w], [x["l"] for x in w],
                                     [x["c"] for x in w]) if pyramid_units > 1 else None
                pos = {"dir": direction, "entry": entry, "trail": trail,
                       "units": [{"e": entry, "ipp": risk_ils / stop_pts}],
                       "last_add": entry, "n_atr": n_atr, "be_hit": False, "et": c["t"]}
            continue

        t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
        t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        if pos.get("be_hit"):
            _anchor = pos["units"][-1]["e"] if pos.get("units") else pos["entry"]
            be_px = (_anchor + be_offset) if pos["dir"] == "long" else (_anchor - be_offset)
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

        if pyramid_units > 1 and pos.get("n_atr") and len(pos["units"]) < pyramid_units:
            step = pyramid_step_n * pos["n_atr"]
            while len(pos["units"]) < pyramid_units:
                if pos["dir"] == "long":
                    trigger = pos["last_add"] + step
                    if c["c"] < trigger:
                        break
                    add_px = trigger
                    dist = add_px - pos["trail"]
                else:
                    trigger = pos["last_add"] - step
                    if c["c"] > trigger:
                        break
                    add_px = trigger
                    dist = pos["trail"] - add_px
                if dist < 1e-6:
                    break
                pos["units"].append({"e": add_px, "ipp": risk_ils / dist})
                pos["last_add"] = trigger

        exit_px = None
        if pos["dir"] == "long" and c["l"] <= pos["trail"]:
            exit_px = min(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
            exit_px = max(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        if exit_px is not None:
            _close_pos(pos, exit_px, c["t"])
            pos = None
            continue

        if be_trigger is not None and not pos["be_hit"]:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= be_trigger:
                pos["be_hit"] = True

    if pos is not None and h4:
        last = h4[-1]
        _close_pos(pos, last["c"], last["t"], open_at_end=True)

    wins = [x for x in closed if x["pnl"] > 0]
    n = len(closed)
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1) if n else 0,
            "pnl": round(sum(x["pnl"] for x in closed), 2), "blocked": blocked, "detail": closed}


def compute_er(h4, period=10):
    er = [None] * len(h4)
    for i in range(period, len(h4)):
        net = abs(h4[i]["c"] - h4[i - period]["c"])
        vol = sum(abs(h4[j]["c"] - h4[j - 1]["c"]) for j in range(i - period + 1, i + 1))
        er[i] = net / vol if vol > 1e-9 else 0.0
    return er


if __name__ == "__main__":
    h4 = load_h4()
    kw = dict(entry_days=tb.SLOW_ENTRY_DAYS, trail_days=tb.SLOW_TRAIL_DAYS,
              risk_ils=tb.SLOW_RISK_ILS, pyramid_units=tb.SLOW_PYRAMID_UNITS,
              pyramid_step_n=tb.SLOW_PYRAMID_STEP_N, be_trigger=tb.SLOW_BE_TRIGGER,
              be_offset=tb.SLOW_BE_OFFSET)

    print("=== ישן (מעורבב: slippage משנה גם תזמון) ===")
    old = {}
    for slip in (0.0, 2.0, 3.0):
        r = tb._simulate_slow(h4, slippage_points=slip, **kw)
        old[slip] = r
        print(f"  {slip}$: {r['trades']} עסקאות | {r['win_rate']} | ‏{r['pnl']:+.0f}")

    print()
    print("=== נקי (cost-only: אותו רצף עסקאות, רק עלות שונה) ===")
    new = {}
    for slip in (0.0, 2.0, 3.0):
        r = clean_simulate_slow(h4, extra_cost_points=slip, **kw)
        new[slip] = r
        print(f"  {slip}$: {r['trades']} עסקאות | {r['win_rate']}% | ‏{r['pnl']:+.0f}")

    print()
    base_dates = set(t["et"] for t in new[0.0]["detail"])
    for slip in (2.0, 3.0):
        dates = set(t["et"] for t in new[slip]["detail"])
        print(f"נקי: 0$ ו-{slip}$ חולקות בדיוק {len(base_dates & dates)}/{len(base_dates)} כניסות "
              f"(אמור להיות זהה 100%)")

    print()
    print("=== השוואה ישירה: כמה זה שינה את המסקנה ===")
    for slip in (0.0, 2.0, 3.0):
        diff = new[slip]["pnl"] - old[slip]["pnl"]
        print(f"  {slip}$: ישן ‏{old[slip]['pnl']:+.0f} | נקי ‏{new[slip]['pnl']:+.0f} | פער {diff:+.0f}")
