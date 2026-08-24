"""
m1_htf.py — שיטה 1, אותה ליבת איתות (EMA50 דדזון + RSI/MACD/BB/ADX + פריצת 20),
מועברת ל-H1 עם יציאות פרופורציוניות ל-ATR, במקום 15 דק' עם 10$/10$ קבועים.

כלי מחקר עצמאי — לא נוגע בקוד החי. תלוי ב-trading_bot.py רק בשביל
פונקציות האינדיקטורים (calc_rsi/calc_macd/calc_bollinger/calc_atr/calc_adx/
calc_ema_series/check_breakout) — לא מריץ אף לוגיקת בוט/רשת.

⚠️ יישור UTC (לא שעון ישראל) — ר' §13.1 ב-STATUS.md. תוצאה חיובית כאן
היא סף כניסה למחקר נוסף, לא ממצא סופי (חסר: יישור IL, CPCV, DSR, מספור
ניסויים בתקציב §4ה).

הרצה: python tools/m1_htf.py
"""
import sys, os, csv, datetime, random, statistics as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trading_bot as tb

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

OZ = tb.REPORT_LOT_OZ            # 0.75 — מציאות פלוס500, לא סקאלת סיכון
USD_ILS = tb.USD_ILS             # 3.0014
SPREAD = tb.SPREAD_POINTS        # 0.77$
SPREAD_ILS = round(OZ * SPREAD * USD_ILS, 2)
TREND_DEADZONE = tb.TREND_DEADZONE if hasattr(tb, "TREND_DEADZONE") else 0.003
MAX_STRETCH = tb.MAX_STRETCH_PCT  # 1.2
STOP_FLOOR_PCT = tb.STOP_FLOOR_PCT  # 0.35
ADX_MIN = tb.ADX_MIN              # 20


def load_csv(name):
    rows = []
    with open(os.path.join(DATA_DIR, name), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = r["timestamp"]
            # מנרמל לפורמט אחיד naive-UTC (הקבצים כתובים ב-UTC בשתי הצורות)
            t = t.replace("+00:00", "").replace("T", " ")
            dt = datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
            rows.append({"t": dt, "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"])})
    rows.sort(key=lambda x: x["t"])
    return rows


def score_signal(is_long, w_c, w_h, w_l, current, min_stars=2, adx_hard_min=None):
    """זהה 1:1 לניקוד ב-_simulate של trading_bot.py (שורות ~1416-1442).
    min_stars/adx_hard_min: הידוק אופציונלי לבדיקת "האם כניסה בררנית יותר עמידה יותר"."""
    rsi = tb.calc_rsi(w_c)
    macd_line, macd_sig = tb.calc_macd(w_c)
    bb_up, bb_mid, bb_lo = tb.calc_bollinger(w_c)
    adx = tb.calc_adx(w_h, w_l, w_c)
    brk = tb.check_breakout(w_c, w_h, w_l)

    if adx is not None and adx < (adx_hard_min if adx_hard_min is not None else ADX_MIN):
        return None, None
    if is_long and rsi is not None and rsi >= 75:
        return None, None
    if (not is_long) and rsi is not None and rsi <= 25:
        return None, None

    score, supporting = 0.0, 0
    if macd_line is not None and macd_sig is not None:
        if (is_long and macd_line > macd_sig) or ((not is_long) and macd_line < macd_sig):
            score += 1; supporting += 1
    if (brk == "למעלה" and is_long) or (brk == "למטה" and not is_long):
        score += 1; supporting += 1
    if rsi is not None:
        if is_long:
            if 40 <= rsi <= 65: score += 1; supporting += 1
            elif rsi >= 75: score -= 0.5
        else:
            if 35 <= rsi <= 60: score += 1; supporting += 1
            elif rsi <= 25: score -= 0.5
    if bb_up and bb_mid and bb_lo:
        if is_long:
            if current <= bb_mid: score += 1; supporting += 1
            elif current >= bb_up: score -= 0.5
        else:
            if current >= bb_mid: score += 1; supporting += 1
            elif current <= bb_lo: score -= 0.5
    if adx is not None and adx >= 25:
        score += 1; supporting += 1

    stars = min(5, max(1, round(score)))
    if stars < min_stars or supporting < 2:
        return None, None
    return stars, adx


def simulate(entry_bars, trend_bars, atr_stop_mult=1.5, target_mult=2.0,
             timeout_hours=None, slippage_usd=0.0, active_start=0, active_end=24,
             min_stars=2, adx_hard_min=None, ema_period=None):
    """
    entry_bars: נרות הכניסה (H1). trend_bars: נרות מסגרת המגמה (H4, EMA50).
    יציאה: סטופ = atr_stop_mult × ATR14(entry_bars), יעד = target_mult × מרחק הסטופ.
    בלי יעד/סטופ בדולר קבוע — זה בדיוק השינוי מול §9 (45 קונפיגורציות שנכשלו).
    """
    ep = ema_period if ema_period is not None else tb.TREND_EMA_PERIOD
    t_closes = [b["c"] for b in trend_bars]
    t_ema = tb.calc_ema_series(t_closes, ep)
    t_times = [b["t"] for b in trend_bars]

    closes = [b["c"] for b in entry_bars]
    highs = [b["h"] for b in entry_bars]
    lows = [b["l"] for b in entry_bars]
    times = [b["t"] for b in entry_bars]

    closed = []
    j = 0
    open_trade = None

    for i in range(50, len(entry_bars)):
        t = times[i]

        # --- סגירת עסקה פתוחה מול הנר הנוכחי (סטופ קודם, כמו בחי) ---
        if open_trade:
            tr = open_trade
            is_long = tr["dir"] == "long"
            stop_hit = (lows[i] <= tr["stop"]) if is_long else (highs[i] >= tr["stop"])
            target_hit = (highs[i] >= tr["target"]) if is_long else (lows[i] <= tr["target"])
            timed_out = timeout_hours and (t - tr["time"]).total_seconds() >= timeout_hours * 3600
            if stop_hit:
                pts = -(tr["entry"] - tr["stop"] if is_long else tr["stop"] - tr["entry"])
                _close(closed, tr, pts, "stop", t)
                open_trade = None
            elif target_hit:
                pts = (tr["target"] - tr["entry"]) if is_long else (tr["entry"] - tr["target"])
                _close(closed, tr, pts, "target", t)
                open_trade = None
            elif timed_out:
                pts = (closes[i] - tr["entry"]) if is_long else (tr["entry"] - closes[i])
                _close(closed, tr, pts, "timeout", t)
                open_trade = None

        if open_trade:
            continue
        if not (active_start <= t.hour < active_end):
            continue

        while j + 1 < len(t_times) and t_times[j + 1] <= t:
            j += 1
        if j < ep + 10:
            continue
        ema = t_ema[j]
        current = closes[i]
        dev = (current - ema) / ema
        if dev > TREND_DEADZONE:
            direction = "long"
        elif dev < -TREND_DEADZONE:
            direction = "short"
        else:
            continue
        if abs(dev) * 100 > MAX_STRETCH:
            continue
        is_long = direction == "long"

        w_c = closes[i - 49:i + 1]; w_h = highs[i - 49:i + 1]; w_l = lows[i - 49:i + 1]
        stars, adx = score_signal(is_long, w_c, w_h, w_l, current,
                                   min_stars=min_stars, adx_hard_min=adx_hard_min)
        if stars is None:
            continue

        atr = tb.calc_atr(w_h, w_l, w_c)
        if not atr:
            continue
        stop_distance = max(atr * atr_stop_mult, current * STOP_FLOOR_PCT / 100)
        entry_px = current + slippage_usd if is_long else current - slippage_usd
        stop = entry_px - stop_distance if is_long else entry_px + stop_distance
        target = entry_px + stop_distance * target_mult if is_long else entry_px - stop_distance * target_mult
        open_trade = {"dir": direction, "entry": entry_px, "stop": stop, "target": target,
                      "time": t, "stars": stars}

    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    total = round(sum(c["pnl"] for c in closed), 2)
    eq = 0; pk = 0; dd = 0
    for c in closed:
        eq += c["pnl"]; pk = max(pk, eq); dd = min(dd, eq - pk)
    wr = round(100 * len(wins) / len(closed), 1) if closed else None
    return {"n": len(closed), "wins": len(wins), "losses": len(losses),
            "wr": wr, "pnl": total, "dd": round(dd, 0), "detail": closed}


def _close(closed, tr, pts, reason, exit_time):
    days = max(0.0, (exit_time - tr["time"]).total_seconds() / 86400)
    gross = pts * OZ * USD_ILS
    funding = tb.funding_cost_ils(OZ, days)
    pnl = gross - SPREAD_ILS - funding
    closed.append({"pnl": round(pnl, 2), "pts": round(pts, 2), "reason": reason,
                   "t": tr["time"], "exit_t": exit_time, "dir": tr["dir"], "days": round(days, 2)})


def build_htf(h1_bars, hours, offset_h=0):
    """מצרף H1 ל-N-שעתי עם היסט גבול (בדיקת יישור, כמו tools/tf_offset.py).
    offset_h=0 = יישור UTC סטנדרטי (00/04/08.../ל-4H)."""
    out = {}
    for b in h1_bars:
        bucket_hour = ((b["t"].hour - offset_h) // hours) * hours + offset_h
        k = b["t"].replace(hour=0, minute=0, second=0, microsecond=0)
        shift_days = 0
        while bucket_hour < 0:
            bucket_hour += hours; shift_days -= 1
        while bucket_hour >= 24:
            bucket_hour -= hours; shift_days += 1
        k = k + datetime.timedelta(days=shift_days, hours=bucket_hour)
        if k not in out:
            out[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            d = out[k]
            d["h"] = max(d["h"], b["h"]); d["l"] = min(d["l"], b["l"]); d["c"] = b["c"]
    return [out[k] for k in sorted(out)]


def build_daily(bars):
    """מצרף נרות (H1/H4) ליומי — לשמש כפילטר מגמה כשהכניסה עצמה על H4."""
    by_day = {}
    for b in bars:
        k = b["t"].date()
        if k not in by_day:
            by_day[k] = {"t": datetime.datetime(k.year, k.month, k.day),
                         "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            d = by_day[k]
            d["h"] = max(d["h"], b["h"]); d["l"] = min(d["l"], b["l"]); d["c"] = b["c"]
    return [by_day[k] for k in sorted(by_day)]


def half_split(entry_bars, trend_bars, **kw):
    mid_t = entry_bars[len(entry_bars) // 2]["t"]
    r_all = simulate(entry_bars, trend_bars, **kw)
    train = [c for c in r_all["detail"] if c["t"] < mid_t]
    test = [c for c in r_all["detail"] if c["t"] >= mid_t]
    return sum(c["pnl"] for c in train), sum(c["pnl"] for c in test)


def bootstrap_p(closed, n_iter=2000, seed=7):
    if not closed:
        return None
    pnls = [c["pnl"] for c in closed]
    rng = random.Random(seed)
    totals = []
    for _ in range(n_iter):
        sample = [rng.choice(pnls) for _ in pnls]
        totals.append(sum(sample))
    totals.sort()
    p_profit = round(100 * sum(1 for x in totals if x > 0) / n_iter, 1)
    return totals[int(0.05 * n_iter)], totals[int(0.5 * n_iter)], totals[int(0.95 * n_iter)], p_profit


if __name__ == "__main__":
    h1 = load_csv("xauusd_h1.csv")
    h4 = load_csv("xauusd_h4.csv")
    print(f"H1: {len(h1)} נרות ({h1[0]['t'].date()} → {h1[-1]['t'].date()}) | "
          f"H4: {len(h4)} נרות\n")

    print("=" * 70)
    print("שלב א' — גריד על atr_stop_mult × target_mult (בלי טיימאאוט)")
    print("=" * 70)
    grid = []
    for stop_m in (1.0, 1.5, 2.0):
        for tgt_m in (1.5, 2.0, 2.5, 3.0):
            r = simulate(h1, h4, atr_stop_mult=stop_m, target_mult=tgt_m)
            grid.append((stop_m, tgt_m, r))
            print(f"  stop={stop_m:.1f}×ATR target={tgt_m:.1f}×סטופ: "
                  f"{r['n']:4d} עסק' | {str(r['wr'])+'%':>6} | "
                  f"{r['pnl']:+9.0f} ש\"ח | MaxDD {r['dd']:+.0f}")

    best = max(grid, key=lambda x: x[2]["pnl"])
    stop_m, tgt_m, base_r = best
    print(f"\n➡️  הכי טוב (PnL גולמי בלבד): stop={stop_m}×ATR, target={tgt_m}×סטופ → "
          f"{base_r['n']} עסק' | {base_r['wr']}% | {base_r['pnl']:+.0f} ש\"ח")

    print("\n" + "=" * 70)
    print("שלב ב' — עמידות להחלקה על כל הגריד (סף מתוקן 2-3$ לפי §14.2, לא 6$)")
    print("=" * 70)
    print(f"{'stop':>5} {'tgt':>5} {'n':>5} {'wr':>6} | {'0$':>7} {'2$':>7} {'3$':>7} {'6$':>7}")
    any_survives = False
    for stop_m2, tgt_m2, r0 in grid:
        row = [simulate(h1, h4, atr_stop_mult=stop_m2, target_mult=tgt_m2,
                         slippage_usd=s)["pnl"] for s in (0.0, 2.0, 3.0, 6.0)]
        mark = "✅ שורד 3$" if row[2] > 0 else ""
        any_survives = any_survives or row[2] > 0
        print(f"{stop_m2:5.1f} {tgt_m2:5.1f} {r0['n']:5d} {str(r0['wr'])+'%':>6} | "
              f"{row[0]:+7.0f} {row[1]:+7.0f} {row[2]:+7.0f} {row[3]:+7.0f}  {mark}")
    if not any_survives:
        print("\n⚠️ אף תא בגריד לא שורד 3$ החלקה. לפי כלל המעבר — שום קונפיגורציה כאן לא עוברת.")

    print("\n" + "=" * 70)
    print("שלב ג' — חצי/חצי (אימון → מבחן)")
    print("=" * 70)
    train_pnl, test_pnl = half_split(h1, h4, atr_stop_mult=stop_m, target_mult=tgt_m)
    print(f"  אימון: {train_pnl:+.0f} ש\"ח | מבחן: {test_pnl:+.0f} ש\"ח")

    print("\n" + "=" * 70)
    print("שלב ד' — Bootstrap (2,000 דגימות, resample על העסקאות)")
    print("=" * 70)
    p5, p50, p95, p_profit = bootstrap_p(base_r["detail"])
    print(f"  5%: {p5:+.0f} | חציון: {p50:+.0f} | 95%: {p95:+.0f} | P(רווח) ≈ {p_profit}%")

    print("\n" + "=" * 70)
    print("שלב ה' — טיימאאוט (מקביל ל-6 שעות המקורי, בסקאלה חדשה)")
    print("=" * 70)
    for to in (None, 24, 48, 96):
        r = simulate(h1, h4, atr_stop_mult=stop_m, target_mult=tgt_m, timeout_hours=to)
        label = "ללא" if to is None else f"{to}ש"
        print(f"  טיימאאוט {label:>5}: {r['n']:4d} עסק' | {str(r['wr'])+'%':>6} | {r['pnl']:+9.0f} ש\"ח")

    print("\n" + "=" * 70)
    print("שלב ו' — H1 עם כניסה בררנית יותר (ADX≥25 קשיח + stars≥3)")
    print("   האם התנאי הרופף (2/5 אינדיקטורים) הוא הבעיה, לא רק הטווח?")
    print("=" * 70)
    for stop_m3, tgt_m3 in ((1.0, 3.0), (1.5, 2.5), (2.0, 2.0)):
        r0 = simulate(h1, h4, atr_stop_mult=stop_m3, target_mult=tgt_m3,
                      min_stars=3, adx_hard_min=25)
        row = [simulate(h1, h4, atr_stop_mult=stop_m3, target_mult=tgt_m3,
                        min_stars=3, adx_hard_min=25, slippage_usd=s)["pnl"]
               for s in (0.0, 2.0, 3.0)]
        print(f"  stop={stop_m3}×ATR target={tgt_m3}×סטופ: {r0['n']:4d} עסק' | "
              f"{str(r0['wr'])+'%':>6} | 0$:{row[0]:+.0f} 2$:{row[1]:+.0f} 3$:{row[2]:+.0f}")

    print("\n" + "=" * 70)
    print("שלב ז' — הליבה של שיטה 1 (לא של שיטה 3!) על H4, מגמה=EMA50 יומי")
    print("   ⭐ התא שמעולם לא נבדק: אותו מנוע איתות ספציפי (EMA-דדזון+RSI/MACD/BB/ADX,")
    print("   לא פריצת-20 כמו שיטה 3) על מסגרת זמן שכבר עוברת את מחסום העלויות.")
    print("=" * 70)
    daily = build_daily(h4)
    print(f"  (נבנו {len(daily)} נרות יומיים מ-H4)")
    grid_h4 = []
    for stop_m4 in (1.0, 1.5, 2.0):
        for tgt_m4 in (1.5, 2.0, 2.5, 3.0):
            r = simulate(h4, daily, atr_stop_mult=stop_m4, target_mult=tgt_m4)
            grid_h4.append((stop_m4, tgt_m4, r))
    print(f"{'stop':>5} {'tgt':>5} {'n':>5} {'wr':>6} | {'0$':>7} {'2$':>7} {'3$':>7} {'6$':>7}")
    any_survives_h4 = False
    for stop_m4, tgt_m4, r0 in grid_h4:
        row = [simulate(h4, daily, atr_stop_mult=stop_m4, target_mult=tgt_m4,
                        slippage_usd=s)["pnl"] for s in (0.0, 2.0, 3.0, 6.0)]
        mark = "✅ שורד 3$" if row[2] > 0 else ""
        any_survives_h4 = any_survives_h4 or row[2] > 0
        print(f"{stop_m4:5.1f} {tgt_m4:5.1f} {r0['n']:5d} {str(r0['wr'])+'%':>6} | "
              f"{row[0]:+7.0f} {row[1]:+7.0f} {row[2]:+7.0f} {row[3]:+7.0f}  {mark}")
    if any_survives_h4:
        best_h4 = max(grid_h4, key=lambda x: x[2]["pnl"])
        sm, tm, br = best_h4
        tr_pnl, te_pnl = half_split(h4, daily, atr_stop_mult=sm, target_mult=tm)
        p5b, p50b, p95b, ppb = bootstrap_p(br["detail"])
        print(f"\n  ➡️ הכי טוב: stop={sm}×ATR target={tm}×סטופ | חצי/חצי: "
              f"אימון {tr_pnl:+.0f} → מבחן {te_pnl:+.0f} | "
              f"Bootstrap 5%/חציון/95%: {p5b:+.0f}/{p50b:+.0f}/{p95b:+.0f} | P(רווח)≈{ppb}%")
    else:
        print("\n  ⚠️ אף תא לא שורד 3$ החלקה כאן גם כן.")

    print("\n" + "=" * 70)
    print("שלב ח' — מבחן יישור: התא הטוב (1.5×ATR/2.0×סטופ) שורד היסטי גבול?")
    print("   בנוי מ-H1 מחדש (לא מ-xauusd_h4.csv) בהיסטים 0-3 שעות, כמו §13.3.")
    print("=" * 70)
    survives_all_offsets = True
    for off in (0, 1, 2, 3):
        h4_off = build_htf(h1, 4, offset_h=off)
        daily_off = build_daily(h4_off)
        r = simulate(h4_off, daily_off, atr_stop_mult=1.5, target_mult=2.0)
        r3 = simulate(h4_off, daily_off, atr_stop_mult=1.5, target_mult=2.0, slippage_usd=3.0)
        ok = r3["pnl"] > 0
        survives_all_offsets = survives_all_offsets and ok
        mark = "✅" if ok else "❌"
        print(f"  היסט {off}ש: {r['n']:4d} עסק' | {str(r['wr'])+'%':>6} | "
              f"0$:{r['pnl']:+.0f} 3$:{r3['pnl']:+.0f}  {mark}")
    print(f"\n  {'✅ שורד את כל 4 ההיסטים' if survives_all_offsets else '🔴 לא שורד את כל ההיסטים — תלוי ביישור הספציפי, לא יציב'}")
