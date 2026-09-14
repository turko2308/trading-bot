"""
tools/cores.py — Core A (מגמה טהורה, דו-כיווני) מול Core B (מין-ריברז'ן), סוויפ טווחי-זמן
================================================================================================
גרסה מתוקנת 14/09/2026 — הגרסה הקודמת (מ-14/09 מוקדם יותר) פישלה: (1) הייתה long-only
בלבד, בעוד הבוט החי (tf_scan, trading_bot.py) דו-כיווני; (2) יישרה נרות לפי UTC במקום
Asia/Jerusalem כמו הבוט החי. שתי הטעויות תוקנו כאן, מול trading_bot.py בפועל (tf_scan,
_tf_aggregate, _tf_atr, שורות 2172-2400).

Core A דו-כיווני: פריצת 20-נר + כיוון EMA50 (קנייה: close>hh ו-close>ema;
מכירה: close<ll ו-close<ema). סטופ/יעד 2×ATR14. נרות מיושרים ל-Asia/Jerusalem,
תואם בדיוק ל-tf_scan החי.

אומת מול bot_data.json האמיתי (tf_signals) — ראה validate_against_live() בתחתית.

הרצה: python tools/cores.py   (מצפה ל-data/xauusd_h1.csv (יחסית לריפו))
"""
import csv, datetime
from zoneinfo import ZoneInfo
import statistics as st

IL = ZoneInfo("Asia/Jerusalem")
UTC = ZoneInfo("UTC")

def load_h1(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts_raw = r["timestamp"].split("+")[0].strip()
            ts = datetime.datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ts_il = ts.astimezone(IL).replace(tzinfo=None)
            rows.append({"t": ts_il, "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"])})
    return sorted(rows, key=lambda x: x["t"])

def aggregate(bars, hours):
    """מקבץ נרות H1 (כבר ב-IL time) לנרות N שעות, מיושר לגבול השעה — תואם _tf_aggregate."""
    out = []
    cur = None
    for b in bars:
        slot = b["t"].replace(minute=0, second=0, microsecond=0)
        slot = slot.replace(hour=(slot.hour // hours) * hours)
        if cur is None or cur["t"] != slot:
            if cur: out.append(cur)
            cur = {"t": slot, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            cur["h"] = max(cur["h"], b["h"]); cur["l"] = min(cur["l"], b["l"]); cur["c"] = b["c"]
    if cur: out.append(cur)
    return out

def ema_series(closes, span):
    out = [None]*len(closes); k = 2/(span+1); e = closes[0]; out[0] = e
    for i in range(1, len(closes)):
        e = closes[i]*k + e*(1-k); out[i] = e
    return out

def atr_series(bars, n=14):
    trs = []
    for i in range(len(bars)):
        if i == 0: trs.append(bars[i]["h"]-bars[i]["l"]); continue
        pc = bars[i-1]["c"]
        trs.append(max(bars[i]["h"]-bars[i]["l"], abs(bars[i]["h"]-pc), abs(bars[i]["l"]-pc)))
    out = [None]*len(bars)
    for i in range(n-1, len(bars)):
        out[i] = sum(trs[i-n+1:i+1])/n
    return out

def core_a(bars, target_mult=2.0, stop_mult=2.0, donch_n=20, max_hold=120, slippage=0.0):
    """דו-כיווני — תואם tf_scan: קנייה כש-close>hh(20 קודמים) וגם close>EMA50;
    מכירה כש-close<ll(20 קודמים) וגם close<EMA50."""
    closes = [b["c"] for b in bars]
    ema = ema_series(closes, 50)
    atr = atr_series(bars, 14)
    trades = []; pos = None
    for i in range(60, len(bars)):
        c = bars[i]
        if pos is None:
            prior = bars[i-donch_n:i]
            hh = max(x["h"] for x in prior); ll = min(x["l"] for x in prior)
            direction = None
            if c["c"] > hh and c["c"] > ema[i]:
                direction = "long"
            elif c["c"] < ll and c["c"] < ema[i]:
                direction = "short"
            if direction and atr[i]:
                entry = c["c"] + (slippage if direction=="long" else -slippage)
                stop = entry - stop_mult*atr[i] if direction=="long" else entry + stop_mult*atr[i]
                target = entry + target_mult*atr[i] if direction=="long" else entry - target_mult*atr[i]
                pos = {"i": i, "dir": direction, "entry": entry, "stop": stop, "target": target}
            continue
        exit_px = None
        if pos["dir"] == "long":
            if bars[i]["l"] <= pos["stop"]: exit_px = pos["stop"]
            elif bars[i]["h"] >= pos["target"]: exit_px = pos["target"]
        else:
            if bars[i]["h"] >= pos["stop"]: exit_px = pos["stop"]
            elif bars[i]["l"] <= pos["target"]: exit_px = pos["target"]
        if exit_px is None and i - pos["i"] >= max_hold:
            exit_px = c["c"]
        if exit_px is not None:
            pnl = (exit_px - pos["entry"]) if pos["dir"]=="long" else (pos["entry"] - exit_px)
            trades.append({"pnl": pnl, "t": bars[i]["t"], "dir": pos["dir"], "entry": pos["entry"]})
            pos = None
    return trades

def validate_against_live(bars_4h, bars_6h, live_json_path):
    """משווה כניסות שהמנוע כאן מייצר מול tf_signals האמיתיים ב-bot_data.json."""
    import json
    live = json.load(open(live_json_path))
    live_entries = {(s["tf"], round(s["entry"],2)) for s in live.get("tf_signals", [])}
    t4 = core_a(bars_4h); t6 = core_a(bars_6h)
    my_entries = {("4H", round(x["entry"],2)) for x in t4} | {("6H", round(x["entry"],2)) for x in t6}
    matched = live_entries & my_entries
    print(f"אימות: {len(matched)}/{len(live_entries)} כניסות אמיתיות מ-tf_signals נמצאות גם אצלי")
    missing = live_entries - my_entries
    if missing:
        print("לא נמצאו (עד 10):", list(missing)[:10])

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/xauusd_h1.csv"
    h1 = load_h1(path)
    print(f"נטען: {len(h1)} נרות H1 (IL time), {h1[0]['t']} עד {h1[-1]['t']}\n")
    h4 = aggregate(h1, 4); h6 = aggregate(h1, 6)

    t4 = core_a(h4); t6 = core_a(h6)
    print(f"Core A 4H: N={len(t4)}  total={sum(x['pnl'] for x in t4):.1f}$")
    print(f"Core A 6H: N={len(t6)}  total={sum(x['pnl'] for x in t6):.1f}$")

    import os
    if os.path.exists("bot_data.json"):
        print()
        validate_against_live(h4, h6, "bot_data.json")
