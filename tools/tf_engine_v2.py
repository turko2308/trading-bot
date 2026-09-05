"""
tools/tf_engine_v2.py — Method 3 backtest engine (verified, in repo)
======================================================================
Built 03/09/2026 because the previously-referenced tools/tf_engine.py
does not exist in the repo (404) -- its cited numbers were already
flagged in trading_bot.py comments as unreproduced (rule 21/25 freeze).

Mirrors live logic exactly (tf_scan + tf_monitor in trading_bot.py):
  - Aggregate H1 -> 4H/6H, timestamp-floor aligned (_tf_aggregate)
  - Direction: close > 20-bar prior high AND close > EMA50 (long);
               close < 20-bar prior low  AND close < EMA50 (short)
  - Entry = signal candle close. Stop/Target = entry -/+ 2xATR14
  - Exit scan starts at candle CLOSE time (t0 + hours), pessimistic
    candle (stop wins ties) -- matches the v3.9.5 bug fix
  - 4H signals tagged with backup: a same-direction 6H signal within
    TF_BACKUP_LOOKBACK_H=6h before or TF_BACKUP_WINDOW_H=4h after
  - No slippage/BE entanglement risk here: Method 3 has no trailing
    stop or breakeven mechanism, so cost is applied as pure deduction
    at exit (like SPREAD_POINTS already is) -- this cannot shift
    entry/exit timing, unlike Method 2's bug.
"""
import csv, datetime
import sys, os
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import trading_bot as tb

TF_CONFIGS = tb.TF_CONFIGS
TF_BREAKOUT_BARS = tb.TF_BREAKOUT_BARS
TF_EMA_PERIOD = tb.TF_EMA_PERIOD
TF_ATR_PERIOD = tb.TF_ATR_PERIOD
TF_STOP_ATR = tb.TF_STOP_ATR
TF_TARGET_ATR = tb.TF_TARGET_ATR
TF_BACKUP_WINDOW_H = tb.TF_BACKUP_WINDOW_H
TF_BACKUP_LOOKBACK_H = tb.TF_BACKUP_LOOKBACK_H
SPREAD_POINTS = tb.SPREAD_POINTS
REPORT_LOT_OZ = tb.REPORT_LOT_OZ
USD_ILS = tb.USD_ILS


def load_h1(path=None):
    if path is None:
        path = os.path.join(REPO_ROOT, "data", "xauusd_h1.csv")
    bars = []
    with open(path) as f:
        for row in csv.DictReader(f):
            bars.append({
                "t": datetime.datetime.fromisoformat(row["timestamp"].strip()),
                "o": float(row["open"]), "h": float(row["high"]),
                "l": float(row["low"]), "c": float(row["close"]),
            })
    bars.sort(key=lambda b: b["t"])
    return bars


def tf_aggregate(h1_bars, hours):
    out = []
    cur = None
    for b in h1_bars:
        slot = b["t"].replace(minute=0, second=0, microsecond=0)
        slot = slot.replace(hour=(slot.hour // hours) * hours)
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


def tf_atr_series(bars, n=TF_ATR_PERIOD):
    atr = [None] * len(bars)
    for i in range(n, len(bars)):
        tr = []
        for j in range(i - n + 1, i + 1):
            pc = bars[j - 1]["c"]
            tr.append(max(bars[j]["h"] - bars[j]["l"],
                          abs(bars[j]["h"] - pc), abs(pc - bars[j]["l"])))
        atr[i] = sum(tr) / n
    return atr


def generate_signals(bars, ema, atr, tf_name):
    """Mirrors tf_scan's per-candle logic exactly, causal (uses only
    bars up to and including i for direction/ATR/EMA)."""
    signals = []
    min_i = TF_EMA_PERIOD + TF_BREAKOUT_BARS + 5
    for i in range(min_i, len(bars)):
        if ema[i] is None or atr[i] is None:
            continue
        c = bars[i]["c"]
        prior = bars[i - TF_BREAKOUT_BARS:i]
        hh = max(b["h"] for b in prior)
        ll = min(b["l"] for b in prior)
        direction = None
        if c > hh and c > ema[i]:
            direction = "long"
        elif c < ll and c < ema[i]:
            direction = "short"
        if not direction:
            continue
        is_long = direction == "long"
        stop = c - TF_STOP_ATR * atr[i] if is_long else c + TF_STOP_ATR * atr[i]
        target = c + TF_TARGET_ATR * atr[i] if is_long else c - TF_TARGET_ATR * atr[i]
        signals.append({
            "tf": tf_name, "i": i, "t0": bars[i]["t"], "direction": direction,
            "entry": c, "stop": stop, "target": target, "atr": atr[i],
        })
    return signals


def tag_backup(sig_4h, sig_6h):
    """4H signal gets backup=True if a same-direction 6H signal exists
    within [-LOOKBACK_H, +WINDOW_H] of the 4H signal time. Matches live
    two-pass logic (checked-behind at scan time, then patched forward
    when the 6H signal later arrives) collapsed into one offline pass
    since we have full hindsight here -- net effect on which signals
    end up backed is the same as the live two-message flow."""
    for s in sig_4h:
        window_start = s["t0"] - datetime.timedelta(hours=TF_BACKUP_LOOKBACK_H)
        window_end = s["t0"] + datetime.timedelta(hours=TF_BACKUP_WINDOW_H)
        s["backup"] = any(
            b["direction"] == s["direction"] and window_start <= b["t0"] <= window_end
            for b in sig_6h
        )
    return sig_4h


def simulate_exits(signals, h1, tf_hours, extra_cost_points=0.0):
    closed = []
    for sig in signals:
        t_close = sig["t0"] + datetime.timedelta(hours=tf_hours)
        is_long = sig["direction"] == "long"
        hit = None
        for b in h1:
            if b["t"] < t_close:
                continue
            s_hit = (b["l"] <= sig["stop"]) if is_long else (b["h"] >= sig["stop"])
            t_hit = (b["h"] >= sig["target"]) if is_long else (b["l"] <= sig["target"])
            if s_hit:
                hit = ("loss", sig["stop"], b["t"])
                break
            if t_hit:
                hit = ("win", sig["target"], b["t"])
                break
        if hit is None:
            continue  # still open at end of data
        result, exit_px, exit_t = hit
        pts = (exit_px - sig["entry"]) if is_long else (sig["entry"] - exit_px)
        pnl = (pts - SPREAD_POINTS - extra_cost_points) * REPORT_LOT_OZ * USD_ILS
        closed.append({**sig, "result": result, "exit": exit_px, "exit_t": exit_t,
                        "pts": pts, "pnl": pnl})
    return closed


def summarize(closed, label):
    n = len(closed)
    if n == 0:
        print(f"{label}: 0 עסקאות")
        return
    wins = [c for c in closed if c["result"] == "win"]
    total = sum(c["pnl"] for c in closed)
    print(f"{label}: {n} עסקאות | {len(wins)/n*100:.1f}% | ‏{total:+.0f} ש\"ח ({REPORT_LOT_OZ}oz)")


if __name__ == "__main__":
    h1 = load_h1()
    print(f"h1: {len(h1)} נרות ({h1[0]['t']} → {h1[-1]['t']})")
    print()

    all_by_tf = {}
    for cfg in TF_CONFIGS:
        name, hrs = cfg["name"], cfg["hours"]
        bars = tf_aggregate(h1, hrs)
        closes = [b["c"] for b in bars]
        ema = tb.calc_ema_series(closes, TF_EMA_PERIOD)
        atr = tf_atr_series(bars, TF_ATR_PERIOD)
        sigs = generate_signals(bars, ema, atr, name)
        all_by_tf[name] = sigs
        print(f"{name}: {len(bars)} נרות מצורפים, {len(sigs)} איתותים גולמיים")

    tag_backup(all_by_tf["4H"], all_by_tf["6H"])
    n_backed = sum(1 for s in all_by_tf["4H"] if s["backup"])
    print(f"4H עם גיבוי 6H: {n_backed}/{len(all_by_tf['4H'])}")
    print()

    for slip in (0.0, 2.0, 3.0):
        print(f"--- עלות נוספת {slip}$ (מעבר לספרד {SPREAD_POINTS}$) ---")
        closed_4h_all = simulate_exits(all_by_tf["4H"], h1, 4, slip)
        closed_4h_backed = simulate_exits([s for s in all_by_tf["4H"] if s["backup"]], h1, 4, slip)
        closed_4h_unbacked = simulate_exits([s for s in all_by_tf["4H"] if not s["backup"]], h1, 4, slip)
        closed_6h = simulate_exits(all_by_tf["6H"], h1, 6, slip)
        summarize(closed_4h_all, "4H הכל")
        summarize(closed_4h_backed, "4H עם גיבוי")
        summarize(closed_4h_unbacked, "4H בלי גיבוי")
        summarize(closed_6h, "6H")
        print()
