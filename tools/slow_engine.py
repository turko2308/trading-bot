# -*- coding: utf-8 -*-
"""
slow_engine.py — המנוע הקנוני היחיד לבקטסט של שיטה 2 (🐢 דונקיאן 20/4).

⚠️ זה המנוע היחיד שיש להשתמש בו לכל בדיקה חדשה. `_simulate_slow`
שב-trading_bot.py משתמש בגודל פוזיציה מנורמל-סיכון (~40 ש"ח לעסקה)
ובלי מימון לילה — הוא אינו משקף את הכלכלה האמיתית, ומספריו אינם
בני-השוואה למספרי המנוע הזה. אין להשוות מספרים בין השניים.

מקור: slow_simulator.py, עם תיקון כלל 29 (03/09/2026):
  • מחיר הכניסה נקי מעלות ביצוע (היה: entry = close ± slippage)
  • מחיר תוספת הפירמידה נקי מעלות (היה: add_px = trigger ± slippage)
  • slippage_points מנוכה בסגירה בלבד, ליחידה, יחד עם הספרד
לפני התיקון: slippage הזיז את מחיר הכניסה → הזיז את עוגן ה-BE →
הזיז את הסטופ הנגרר → שינה מתי עסקאות נסגרות. כלומר בדיקת עמידות
עלות לא בדקה "אותן עסקאות עם עלות גבוהה" אלא רצף עסקאות אחר.
אחרי התיקון: מספר העסקאות ותאריכיהן זהים בכל רמת עלות.

מתאים ל- slow_scan_and_monitor בדיוק.

ההבדל מ- _simulate_slow (הישן): הישן מגדל פוזיציה לפי סיכון מנורמל של 40 ש"ח
לעסקה (ipp = risk_ils / stop_pts) ומתעלם ממימון לילה. הלייב סוחר 0.75 אונקיה
קבוע ליחידה, עם ספרד ומימון. לכן /slow ו-/h8 הישנים אינם ברי-השוואה למציאות.

הסימולטור הזה משכפל את הכלכלה האמיתית:
  • גודל קבוע: REPORT_LOT_OZ (0.75) ליחידה.
  • ספרד: SPREAD_POINTS מנוכים פעם אחת לכל יחידה (בצד היציאה, כמו slow_real_pnl).
  • מימון לילה: FUNDING_ILS_PER_OZ_DAY * 0.75 * n_units * days.
  • BE: אחרי SLOW_BE_TRIGGER$ לטובתנו, הסטופ קופץ לכניסת היחידה האחרונה +
    SLOW_BE_OFFSET. נדרך בסוף נר, פועל מהנר הבא (שמרני, כמו הלייב).
  • פירמידינג: יחידה 2 נוספת כשנר 4H נסגר SLOW_PYRAMID_STEP_N×ATR14 מעבר
    לכניסה. מקסימום SLOW_PYRAMID_UNITS יחידות. כולן יוצאות יחד על הסטופ הנגרר.
  • סטופ נגרר: שפל/שיא SLOW_TRAIL_DAYS ימים; מתהדק בלבד.
  • יציאה: חציית סטופ נגרר. נר פסימי — אם הנר נפתח מעבר לסטופ (גאפ) יוצאים
    בפתיחה (ריאליסטי ושמרני יותר מיציאה בסטופ).

בנוסף — ידיות איכות אופציונליות (כולן כבויות כברירת מחדל = התנהגות הלייב בדיוק).
כשמדליקים אותן, הסימולטור משמש גם כ-harness לבדיקת שיפורי איכות. כך אפשר
להריץ סריג ולראות בדיוק מה כל פילטר עושה לרווח, לאחוז הצלחה ולירידה המצרפית —
בכלכלה האמיתית. רק מה שעובר עמידות (MC + Bootstrap + החלקה) מועלה ללייב.
"""

import datetime
import random
import statistics

# ── קבועי הלייב (מזוהים מתוך trading_bot_no_keys-3.py) ─────────────
CANDLES_PER_DAY_4H = 6
SLOW_ENTRY_DAYS = 20
SLOW_TRAIL_DAYS = 4
SLOW_BE_TRIGGER = 25.0
SLOW_BE_OFFSET = 3.0
SLOW_PYRAMID_UNITS = 2
SLOW_PYRAMID_STEP_N = 0.5
SLOW_LOT_OZ = 0.75
REPORT_LOT_OZ = 0.75
USD_ILS = 3.0014
SPREAD_POINTS = 0.77
FUNDING_ILS_PER_OZ_DAY = 3.82

# ── עזרים אינדיקטורים (העתק מדויק מהבוט) ──────────────────────────
def calc_ema_series(prices, period):
    if not prices:
        return []
    k = 2 / (period + 1)
    out = [prices[0]]
    for p in prices[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return out


def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return round(sum(trs) / period, 4)


def calc_adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period * 2:
        return None
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)

    def wilder(values, p):
        if len(values) < p:
            return []
        out = [sum(values[:p])]
        for v in values[p:]:
            out.append(out[-1] - out[-1] / p + v)
        return out

    tr_s = wilder(tr_list, period)
    plus_s = wilder(plus_dm, period)
    minus_s = wilder(minus_dm, period)
    if not tr_s:
        return None
    dx_list = []
    for k in range(len(tr_s)):
        if tr_s[k] == 0:
            dx_list.append(0.0)
            continue
        plus_di = 100 * plus_s[k] / tr_s[k]
        minus_di = 100 * minus_s[k] / tr_s[k]
        denom = plus_di + minus_di
        dx_list.append(100 * abs(plus_di - minus_di) / denom if denom else 0.0)
    if len(dx_list) < period:
        return round(sum(dx_list) / len(dx_list), 2) if dx_list else None
    adx = sum(dx_list[:period]) / period
    for v in dx_list[period:]:
        adx = (adx * (period - 1) + v) / period
    return round(adx, 2)


def funding_cost_ils(oz, days):
    """עלות מימון לילה מצטברת. חיובי = עלות."""
    return FUNDING_ILS_PER_OZ_DAY * oz * max(0.0, days)


# ── צבירה ליומי (למסנן מגמה על מסגרת גבוהה) ───────────────────────
def _agg_daily(h4):
    """מצרף נרות 4H לנרות יומיים. מחזיר רשימת {t,o,h,l,c}."""
    out = []
    cur = None
    for b in h4:
        day = b["t"].replace(hour=0, minute=0, second=0, microsecond=0)
        if cur is None or cur["t"] != day:
            if cur:
                out.append(cur)
            cur = {"t": day, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
    if cur:
        out.append(cur)
    return out


# ── הסימולטור האמיתי ──────────────────────────────────────────────
def simulate_slow_real(h4,
                       entry_days=SLOW_ENTRY_DAYS,
                       trail_days=SLOW_TRAIL_DAYS,
                       lot_oz=REPORT_LOT_OZ,
                       usd_ils=USD_ILS,
                       spread_points=SPREAD_POINTS,
                       funding_per_oz_day=FUNDING_ILS_PER_OZ_DAY,
                       be_trigger=SLOW_BE_TRIGGER,
                       be_offset=SLOW_BE_OFFSET,
                       pyramid_units=SLOW_PYRAMID_UNITS,
                       pyramid_step_n=SLOW_PYRAMID_STEP_N,
                       slippage_points=0.0,
                       # ידיות איכות — כולן None/False = התנהגות הלייב בדיוק
                       long_only=False,
                       first_entry_hour=None,
                       last_entry_hour=None,
                       trend_ema_period=None,
                       trend_deadzone=None,
                       adx_min=None,
                       adx_period=14,
                       breakout_min_atr=None,
                       chase_max_atr=None,
                       be_atr_mult=None):
    """
    מחזיר dict עם trades, wins, losses, win_rate, pnl (נטו בשקלים אמיתיים),
    avg_win, avg_loss, avg_days, worst (הפסד מצרפי גרוע), avg_units, max_units,
    ורשימת counters לכל ידית איכות (כמה נחסמו).

    הלייב = קריאה ללא אף ידית איכות (כל הפרמטרים האופציונליים None/False).
    """
    ew = entry_days * CANDLES_PER_DAY_4H
    tw = trail_days * CANDLES_PER_DAY_4H

    # קדם-חישוב של מסנן המגמה (יומי) אם מופעל
    daily_ema = None
    daily_times = []
    if trend_ema_period is not None and trend_deadzone is not None:
        d_bars = _agg_daily(h4)
        d_closes = [b["c"] for b in d_bars]
        daily_ema = calc_ema_series(d_closes, trend_ema_period)
        daily_times = [b["t"] for b in d_bars]

    def daily_trend_at(t):
        """מחזיר 'long'/'short'/'none' לפי EMA יומי בזמן t. None = אין מסנן."""
        if daily_ema is None:
            return None
        # ה-EMA האחרון שנסגר לפני t
        idx = None
        for i, dt in enumerate(daily_times):
            if dt < t:
                idx = i
            else:
                break
        if idx is None or idx >= len(daily_ema):
            return None
        ema = daily_ema[idx]
        # המחיר = סגירת היום ההוא; אם אין — ה-EMA עצמו
        px = d_closes[idx] if idx < len(d_bars) else ema
        dev = (px - ema) / ema if ema else 0
        if dev > trend_deadzone:
            return "long"
        if dev < -trend_deadzone:
            return "short"
        return "none"

    # פתיחת יום קלנדרי — לחסימת רדיפה (chase)
    day_opens = {}
    if chase_max_atr is not None:
        for b in h4:
            dk = b["t"].strftime("%Y-%m-%d")
            if dk not in day_opens:
                day_opens[dk] = b.get("o", b["c"])

    pos = None
    closed = []
    blocked = {"trend": 0, "adx": 0, "chase": 0, "hours": 0,
               "short": 0, "breakout_weak": 0, "stop_too_thin": 0}

    def _close_pos(p, exit_px, when, open_at_end=False):
        days = max(0.0, (when - p["et"]).total_seconds() / 86400.0)
        gross = 0.0
        for u in p["units"]:
            e = u["e"]
            pts = (exit_px - e) if p["dir"] == "long" else (e - exit_px)
            # ספרד + עלות ביצוע, פעם אחת ליחידה (כמו slow_real_pnl).
            # כלל 29: זה המקום היחיד שבו slippage_points נכנס לחישוב.
            pts -= (spread_points + slippage_points)
            gross += pts * lot_oz * usd_ils
        n_units = len(p["units"])
        fund = funding_per_oz_day * lot_oz * n_units * days
        net = gross - fund
        pts0 = (exit_px - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_px)
        rec = {"pnl": round(net, 2), "gross": round(gross, 2),
               "funding": round(fund, 2), "days": round(days, 2),
               "dir": p["dir"], "et": p["et"], "ct": when,
               "units": n_units, "entry": p["entry"], "exit": exit_px}
        if open_at_end:
            rec["open_at_end"] = True
        closed.append(rec)

    for i in range(ew, len(h4)):
        c = h4[i]
        t = c["t"]
        prior = h4[i - ew:i]
        hh = max(x["h"] for x in prior)
        ll = min(x["l"] for x in prior)

        # ── אין פוזיציה: בדיקת כניסה ──
        if pos is None:
            direction = None
            if c["c"] > hh:
                direction = "long"
            elif c["c"] < ll:
                direction = "short"
            if direction:
                # ידית 1: long_only / העדפת לונג
                if long_only and direction == "short":
                    blocked["short"] += 1
                    direction = None
                # ידית 2: חלון שעות
                if direction and first_entry_hour is not None and t.hour < first_entry_hour:
                    blocked["hours"] += 1
                    direction = None
                if direction and last_entry_hour is not None and t.hour >= last_entry_hour:
                    blocked["hours"] += 1
                    direction = None
                # ידית 3: מסנן מגמה יומי — לכנס רק בכיוון המגמה
                if direction:
                    tr = daily_trend_at(t)
                    if tr is not None and tr != direction and tr != "none":
                        blocked["trend"] += 1
                        direction = None
                    elif tr == "none":
                        # דשדוש — אפשר לחסום או להשאיר. כברירת מחדל לא חוסמים
                        # (רק כיוון נגדי נחסם). ניתן להחמיר ע"י העלאת trend_deadzone.
                        pass
                # ידית 4: ADX מינימלי על 4H
                if direction and adx_min is not None and len(h4) > i:
                    w = h4[max(0, i - 50):i + 1]
                    adx = calc_adx([x["h"] for x in w],
                                   [x["l"] for x in w],
                                   [x["c"] for x in w], adx_period)
                    if adx is None or adx < adx_min:
                        blocked["adx"] += 1
                        direction = None
                # ידית 5: חוזק פריצה — הסגירה חייבת לעבור את רמת הדונקיאן ב-k×ATR
                if direction and breakout_min_atr is not None:
                    w = h4[max(0, i - 30):i + 1]
                    atr = calc_atr([x["h"] for x in w],
                                   [x["l"] for x in w],
                                   [x["c"] for x in w])
                    if atr:
                        excess = (c["c"] - hh) if direction == "long" else (ll - c["c"])
                        if excess < breakout_min_atr * atr:
                            blocked["breakout_weak"] += 1
                            direction = None
                # ידית 6: חסימת רדיפה — כבר זז מפתיחת היום יותר מ-k×ATR בכיוון העסקה
                if direction and chase_max_atr is not None:
                    w = h4[max(0, i - 30):i + 1]
                    atr = calc_atr([x["h"] for x in w],
                                   [x["l"] for x in w],
                                   [x["c"] for x in w])
                    if atr:
                        dk = t.strftime("%Y-%m-%d")
                        d_open = day_opens.get(dk)
                        if d_open is not None:
                            move = (c["c"] - d_open) if direction == "long" else (d_open - c["c"])
                            if move > chase_max_atr * atr:
                                blocked["chase"] += 1
                                direction = None

                if direction:
                    # כלל 29: מחיר הכניסה נשאר נקי מעלות ביצוע. העלות מנוכה
                    # בסגירה בלבד (יחד עם הספרד), כדי שהיא לא תזיז את עוגן
                    # ה-BE ואת טריגר הפירמידינג ובכך תשנה את רצף העסקאות.
                    entry = c["c"]
                    t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
                    t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
                    trail = t_lo if direction == "long" else t_hi
                    stop_pts = abs(entry - trail)
                    if stop_pts < 1e-6:
                        continue
                    # ATR14 לפירמידינג ול-BE יחסי-ATR
                    w = h4[max(0, i - 30):i + 1]
                    n_atr = calc_atr([x["h"] for x in w],
                                     [x["l"] for x in w],
                                     [x["c"] for x in w])
                    # BE יחסי-ATR (אם הופעל) דורס את הטריגר הקבוע
                    _be_trig = be_atr_mult * n_atr if (be_atr_mult is not None and n_atr) else be_trigger
                    pos = {"dir": direction, "entry": entry, "trail": trail,
                           "units": [{"e": entry}],
                           "last_add": entry, "n_atr": n_atr,
                           "be_hit": False, "be_trig": _be_trig,
                           "et": c["t"]}
            if pos is None:
                continue

        # ── יש פוזיציה: ניהול ──
        # עדכון סטופ נגרר — מתהדק בלבד
        t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
        t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        # BE — הזרוע נדרכה בנר קודם; הסטופ מתהדק בלבד. עוגן = היחידה האחרונה.
        if pos.get("be_hit"):
            anchor = pos["units"][-1]["e"] if pos.get("units") else pos["entry"]
            be_px = (anchor + be_offset) if pos["dir"] == "long" else (anchor - be_offset)
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

        # פירמידינג — רק על סגירת נר מעבר לטריגר
        if pyramid_units > 1 and pos.get("n_atr") and len(pos["units"]) < pyramid_units:
            step = pyramid_step_n * pos["n_atr"]
            while len(pos["units"]) < pyramid_units:
                if pos["dir"] == "long":
                    trigger = pos["last_add"] + step
                    if c["c"] < trigger:
                        break
                    add_px = trigger          # כלל 29: נקי מעלות
                    dist = add_px - pos["trail"]
                else:
                    trigger = pos["last_add"] - step
                    if c["c"] > trigger:
                        break
                    add_px = trigger          # כלל 29: נקי מעלות
                    dist = pos["trail"] - add_px
                if dist < 1e-6:
                    break
                pos["units"].append({"e": add_px})
                pos["last_add"] = trigger

        # יציאה — חציית סטופ נגרר. גאפ נגד → יציאה בפתיחה (שמרני/ריאליסטי).
        exit_px = None
        if pos["dir"] == "long" and c["l"] <= pos["trail"]:
            exit_px = min(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
            exit_px = max(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        if exit_px is not None:
            _close_pos(pos, exit_px, c["t"])
            pos = None
            continue

        # דריכת BE בסוף הנר — פועל מהנר הבא
        if not pos.get("be_hit") and pos.get("be_trig") is not None:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= pos["be_trig"]:
                pos["be_hit"] = True

    # פוזיציה פתוחה בסוף הנתונים — mark-to-market
    if pos is not None and h4:
        last = h4[-1]
        _close_pos(pos, last["c"], last["t"], open_at_end=True)
        pos = None

    wins = [x for x in closed if x["pnl"] > 0]
    losses = [x for x in closed if x["pnl"] <= 0]
    n = len(closed)
    pnls = [x["pnl"] for x in closed]
    worst = min(pnls) if pnls else 0.0
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": f"{round(100 * len(wins) / n)}%" if n else "—",
        "pnl": round(sum(pnls), 2),
        "avg_win": round(sum(x["pnl"] for x in wins) / len(wins), 1) if wins else 0.0,
        "avg_loss": round(sum(x["pnl"] for x in losses) / len(losses), 1) if losses else 0.0,
        "avg_days": round(sum(x["days"] for x in closed) / n, 1) if n else 0.0,
        "worst": round(worst, 1),
        "avg_units": round(sum(x.get("units", 1) for x in closed) / n, 2) if n else 0.0,
        "max_units": max((x.get("units", 1) for x in closed), default=0),
        "blocked": blocked,
        "detail": closed,
    }


# ── עמידות: Monte Carlo + Bootstrap + החלקה ──────────────────────
def monte_carlo_worst(pnls, runs=2000, seed=42):
    """גרוע מצרפי על סדר עסקאות מעורבב (resample עם החזרה)."""
    if not pnls:
        return 0.0
    rng = random.Random(seed)
    worst = 0.0
    for _ in range(runs):
        eq = 0.0
        dd = 0.0
        for _ in range(len(pnls)):
            eq += rng.choice(pnls)
            dd = min(dd, eq)
            worst = min(worst, dd)
    return round(worst, 1)


def bootstrap_p_win(pnls, runs=2000, seed=7):
    """סיכוי לרווח נטו חיובי על סדר מעורבב."""
    if not pnls:
        return 0.0
    rng = random.Random(seed)
    total = sum(pnls)
    if total > 0:
        return 100.0
    wins = 0
    for _ in range(runs):
        eq = sum(rng.choice(pnls) for _ in range(len(pnls)))
        if eq > 0:
            wins += 1
    return round(100.0 * wins / runs, 1)


def robustness(pnls):
    """מחזיר dict עם worst_dd, p_win, וכמה החלקות שורדות."""
    out = {
        "worst_dd": monte_carlo_worst(pnls),
        "p_win": bootstrap_p_win(pnls),
    }
    base = sum(pnls)
    for slip in (3.0, 6.0, 10.0):
        adj = [p - slip for p in pnls]
        out[f"slip_{int(slip)}"] = round(sum(adj), 1)
    return out


# ── מקור נתונים: הבוט מושך מ-TwelveData. כאן עזר עטיפה. ───────────
def fetch_h4_from_bot(symbol="XAU/USD", outputsize=3000):
    """
    מושך נרות 4H דרך אותו API שהבוט משתמש בו. מחזיר רשימת {t,o,h,l,c}
    בשעון ישראל, מהישן לחדש. מחזיר None בכשל (כמו _fetch_history).
    דורש TWELVE_DATA_API_KEY בסביבה.
    """
    import os
    import requests
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    try:
        r = requests.get("https://api.twelvedata.com/time_series",
                         params={"symbol": symbol, "interval": "4h",
                                 "outputsize": outputsize, "apikey": key,
                                 "timezone": "Asia/Jerusalem"},
                         timeout=20)
        data = r.json()
        if "values" not in data:
            print(f"[SIM] שגיאת API: {data.get('message', 'לא ידוע')}")
            return None
        out = []
        for v in reversed(data["values"]):
            out.append({
                "t": datetime.datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S"),
                "o": float(v["open"]), "h": float(v["high"]),
                "l": float(v["low"]), "c": float(v["close"]),
            })
        return out
    except Exception as e:
        print(f"[SIM] כשל במשיכת נתונים: {e}")
        return None


# ── בדיקה עצמית על נתונים סינתטיים (בלי API) ────────────────────
def _synthetic_h4(days=400, seed=1):
    """נרות 4H סינתטיים עם מגמה ורעש — רק כדי לוודא שהמנוע רץ."""
    rng = random.Random(seed)
    bars = []
    t = datetime.datetime(2025, 1, 1, 0, 0)
    price = 2400.0
    for _ in range(days * 6):
        drift = 0.6
        o = price
        c = o + drift + rng.gauss(0, 4.0)
        h = max(o, c) + abs(rng.gauss(0, 2.0))
        l = min(o, c) - abs(rng.gauss(0, 2.0))
        bars.append({"t": t, "o": round(o, 2), "h": round(h, 2),
                     "l": round(l, 2), "c": round(c, 2)})
        price = c
        t += datetime.timedelta(hours=4)
    return bars


if __name__ == "__main__":
    print("== סימולטור אמיתי שיטה 2 — בדיקה עצמית (נתונים סינתטיים) ==")
    h4 = _synthetic_h4(days=500)
    base = simulate_slow_real(h4)
    print(f"בסיס (הלייב): {base['trades']} עסק' | {base['win_rate']} | "
          f"{base['pnl']:+.0f} ש\"ח | גרוע {base['worst']:+.0f} | "
          f"יח' ממוצע {base['avg_units']}")
    print(f"  MC worst_dd {monte_carlo_worst([x['pnl'] for x in base['detail']]):+.0f} | "
          f"P(רווח) {bootstrap_p_win([x['pnl'] for x in base['detail']]):.0f}%")

    print("\n== דוגמה: סריג ידיות איכות (כל אחת בנפרד) ==")
    variants = [
        ("long_only", dict(long_only=True)),
        ("trend EMA50 0.3%", dict(trend_ema_period=50, trend_deadzone=0.003)),
        ("ADX≥20", dict(adx_min=20)),
        ("ADX≥25", dict(adx_min=25)),
        ("פריצה +0.2ATR", dict(breakout_min_atr=0.2)),
        ("חסימת רדיפה 1.5ATR", dict(chase_max_atr=1.5)),
        ("חלון 8-20", dict(first_entry_hour=8, last_entry_hour=20)),
        ("BE יחסי 1×ATR", dict(be_atr_mult=1.0)),
    ]
    for label, kw in variants:
        r = simulate_slow_real(h4, **kw)
        delta = r["pnl"] - base["pnl"]
        blk = r["blocked"]
        print(f"  {label:22s}: {r['trades']} עסק' | {r['win_rate']:>4s} | "
              f"{r['pnl']:+7.0f} ({delta:+6.0f}) | גרוע {r['worst']:+6.0f} | "
              f"חסומים: {sum(blk.values())}")
    print("\n(בפועל יש להריץ על נתוני TwelveData אמיתיים דרך fetch_h4_from_bot,")
    print(" ולא על נתונים סינתטיים — הם כאן רק לוודא שהמנוע רץ.)")
