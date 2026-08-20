"""
tools_overlap.py — חפיפה בין שיטה 2 לשיטה 3.
שאלה: כשהשתיים מחזיקות פוזיציה באותו זמן ובאותו כיוון, מה הירידה
המשולבת — והאם היא גרועה מסכום הירידות שנמדדו בנפרד.

מנוע שיטה 2: _simulate_slow מתוך trading_bot.py (הקוד החי, לא שכפול).
מנוע שיטה 3: tools_tfoffset, ביישור שעון ישראל כמו הבוט.
"""
import datetime, collections
from zoneinfo import ZoneInfo

import trading_bot as B
import tools_tfoffset as T

IL = ZoneInfo("Asia/Jerusalem")
UTC = datetime.timezone.utc


def agg_local(bars, hours):
    """יישור לשעון ישראל — כמו _tf_aggregate על פיד Asia/Jerusalem."""
    out, cur = [], None
    for b in bars:
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


def m3_trades(h1_il, hours, m15):
    """עסקאות שיטה 3 עם זמני כניסה ויציאה."""
    tf = agg_local(h1_il, hours)
    sigs = T.signals(tf)
    for s in sigs:
        s["t"] = s["t"].astimezone(UTC)
    idx, out, busy = 0, [], None
    for s in sigs:
        start = s["t"] + datetime.timedelta(hours=hours)
        if busy and start < busy:
            continue
        while idx < len(m15) and m15[idx]["t"] < start:
            idx += 1
        j, long = idx, s["dir"] == "long"
        res = None
        while j < len(m15):
            b = m15[j]
            if long:
                if b["l"] <= s["stop"]:
                    res = (b["t"], s["stop"], False); break
                if b["h"] >= s["target"]:
                    res = (b["t"], s["target"], True); break
            else:
                if b["h"] >= s["stop"]:
                    res = (b["t"], s["stop"], False); break
                if b["l"] <= s["target"]:
                    res = (b["t"], s["target"], True); break
            j += 1
        if res is None:
            continue
        move = (res[1] - s["entry"]) if long else (s["entry"] - res[1])
        pnl = (move - T.SPREAD) * T.LOT_OZ * T.USD_ILS
        out.append({"open": start, "close": res[0], "dir": s["dir"],
                    "pnl": pnl, "win": res[2]})
        busy = res[0]
    return out


def m2_trades(h4_il):
    """עסקאות שיטה 2 מהמנוע החי."""
    r = B._simulate_slow(h4_il, entry_days=B.SLOW_ENTRY_DAYS,
                         trail_days=B.SLOW_TRAIL_DAYS,
                         be_trigger=B.SLOW_BE_TRIGGER,
                         be_offset=B.SLOW_BE_OFFSET)
    return r


def overlap_days(a_open, a_close, b_open, b_close):
    lo = max(a_open, b_open)
    hi = min(a_close, b_close)
    return max(0.0, (hi - lo).total_seconds() / 86400.0)


if __name__ == "__main__":
    m15 = T.load_m15()
    h1 = T.to_h1(m15)
    h1_il = [{"t": b["t"].astimezone(IL), "o": b["o"], "h": b["h"],
              "l": b["l"], "c": b["c"]} for b in h1]
    h4_il = agg_local(h1_il, 4)

    res2 = m2_trades(h4_il)
    print("מבנה הפלט של _simulate_slow:", type(res2),
          list(res2.keys())[:12] if isinstance(res2, dict) else len(res2))
    if isinstance(res2, dict):
        for k, v in res2.items():
            if not isinstance(v, list):
                print(f"  {k}: {v}")
        for k, v in res2.items():
            if isinstance(v, list) and v:
                print(f"  {k}: list[{len(v)}], first =", v[0])


# ---------------------------------------------------------------- analysis
def build_positions(m15):
    h1 = T.to_h1(m15)
    h1_il = [{"t": b["t"].astimezone(IL), "o": b["o"], "h": b["h"],
              "l": b["l"], "c": b["c"]} for b in h1]
    h4 = agg_local(h1_il, 4)

    r = B._simulate_slow(h4, entry_days=B.SLOW_ENTRY_DAYS,
                         trail_days=B.SLOW_TRAIL_DAYS,
                         be_trigger=B.SLOW_BE_TRIGGER,
                         be_offset=B.SLOW_BE_OFFSET, pyramid_units=1)
    m2 = [{"open": d["et"].astimezone(UTC), "close": d["ct"].astimezone(UTC),
           "dir": "long" if d["dir"] == "long" else "short",
           "pnl": (d["pts"] - T.SPREAD) * T.LOT_OZ * T.USD_ILS, "m": 2}
          for d in r["detail"]]

    out = {2: m2}
    for hrs in (4, 6):
        out[hrs] = [dict(x, m=hrs) for x in m3_trades(h1_il, hrs, m15)]
    return out


def equity_dd(positions, m15):
    """עקומת הון יומית עם סימון-לשוק של פוזיציות פתוחות."""
    daily = {}
    for b in m15:
        daily[b["t"].date()] = b
    days = sorted(daily)
    # לכל פוזיציה: pnl ליניארי בזמן הוא קירוב גס; במקום זה נשתמש
    # ברווח ממומש בסגירה + אפס לפני. שמרני לגבי DD תוך-עסקה.
    events = collections.defaultdict(float)
    for p in positions:
        events[p["close"].date()] += p["pnl"]
    eq, peak, dd, series = 0.0, 0.0, 0.0, []
    for d in days:
        eq += events.get(d, 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        series.append((d, eq))
    return eq, dd, series
