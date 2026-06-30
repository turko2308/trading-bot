import requests
import time
import datetime
import json
import os
import sys
import hashlib

# ============================================================
# הגדרות
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "960197631")
TWELVEDATA_KEY = os.environ.get("TWELVE_DATA_API_KEY", "2be6ffca08d942de8903d6aee41a312e")

# רק זהב (XAU/USD היחיד שעובד בחינמי של Twelve Data)
SYMBOLS = {
    "זהב": "XAU/USD"
}

# שעות מסחר (שעון ישראל): 08:00–22:00
TRADING_HOURS = {
    "זהב": {"start": 8, "end": 22}
}

# ===== מגבלות איתותים =====
# עד 10 עסקאות שנכנסת אליהן ביום. אם רק צופה ולא נכנס — האיתותים ממשיכים עד 20.
MAX_ENTERED_PER_DAY = 10
MAX_SIGNALS_PER_DAY = 20
MAX_PARALLEL_TRADES = 2
SIGNAL_COOLDOWN_MINUTES = 30
TRADE_TIMEOUT_HOURS = 6

# מגבלת הפסד יומית — מנוטרלת לבינתיים (לשלב בדיקות)
DAILY_LOSS_LIMIT = None

ACCOUNT_SIZE = 500
RISK_PER_TRADE = 0.02   # לתצוגה/השוואה בלבד — אינו משמש לחישוב הרווח/הפסד

# ============================================================
# כלכלת פוזיציה אמיתית — Plus500, זהב (XAU/USD)
# כל החישובים בשקלים מבוססים על המספרים האלה. עדכן לפי המסך שלך.
# ============================================================
POSITION_SIZE_OZ = 0.75   # הכמות שאתה פותח בפועל. מינימום בפלוס500 לזהב = 0.75 אונקיות.
USD_ILS = 2.99            # שער דולר/שקל. עדכן מדי פעם (נכון ליוני 2026: ~2.99).
SPREAD_POINTS = 0.77      # מרווח (spread) בזהב בפלוס500, בנקודות מחיר.

def points_to_ils(points):
    """המרת מרחק מחיר (נקודות $/אונקיה) לשקלים, לפי גודל הפוזיציה ושער הדולר."""
    return POSITION_SIZE_OZ * abs(points) * USD_ILS

# עלות הספרד לעסקה (משולמת בכניסה), בשקלים
SPREAD_COST_ILS = round(POSITION_SIZE_OZ * SPREAD_POINTS * USD_ILS, 2)

DATA_FILE = "/tmp/bot_data.json"
PENDING_FILE = "/tmp/pending_signals.json"

# ============================================================
# שמירת מצב
# ============================================================
def load_data():
    default = {
        "trades": [],
        "daily_stats": {},
        "signal_history": [],
        "trade_counter": 0,
        "indicator_weights": {
            "rsi": 1.0,
            "macd": 1.0,
            "bollinger": 1.0,
            "breakout": 1.0,
            "adx": 1.0
        },
        "all_time_stats": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0,
            "early_exits": 0
        }
    }
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                # ודא שכל המפתחות קיימים (תאימות לאחור)
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
                if "adx" not in data["indicator_weights"]:
                    data["indicator_weights"]["adx"] = 1.0
                return data
    except:
        pass
    return default

def save_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"שגיאה בשמירה: {e}", flush=True)

def load_pending():
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_pending(pending):
    try:
        with open(PENDING_FILE, "w") as f:
            json.dump(pending, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"שגיאה בשמירת pending: {e}", flush=True)

def make_trade_id(symbol_name, ts):
    raw = f"{symbol_name}_{ts}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]

# ============================================================
# טלגרם
# ============================================================
def send_telegram(message, keyboard=None):
    print(f"[TG] שולח: {message[:50]}...", flush=True)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        r = requests.post(url, json=payload, timeout=10)
        result = r.json()
        if not result.get("ok"):
            print(f"[TG] שגיאה: {result}", flush=True)
        return result.get("result", {}).get("message_id")
    except Exception as e:
        print(f"[TG] exception: {e}", flush=True)
        return None

def get_updates(offset=0):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        return r.json().get("result", [])
    except:
        return []

def answer_callback(callback_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_id}, timeout=5)
    except:
        pass

# ============================================================
# נתוני שוק
# ============================================================
def get_prices(symbol, interval="15min", outputsize=50):
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVEDATA_KEY
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"שגיאה ב-{symbol}: {data.get('message', 'לא ידוע')}", flush=True)
            return None
        closes = [float(v["close"]) for v in reversed(data["values"])]
        highs = [float(v["high"]) for v in reversed(data["values"])]
        lows = [float(v["low"]) for v in reversed(data["values"])]
        return {"closes": closes, "highs": highs, "lows": lows}
    except Exception as e:
        print(f"שגיאה בשליפת נתונים {symbol}: {e}", flush=True)
        return None

def get_daily_trend(symbol):
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": 20,
            "apikey": TWELVEDATA_KEY
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            return None
        closes = [float(v["close"]) for v in reversed(data["values"])]
        ma20 = sum(closes[-20:]) / 20
        current = closes[-1]
        if current > ma20 * 1.01:
            return "עלייה"
        elif current < ma20 * 0.99:
            return "ירידה"
        return "ניטרלי"
    except:
        return None

# ============================================================
# אינדיקטורים
# ============================================================
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_macd(prices):
    if len(prices) < 26:
        return None, None
    def ema(data, period):
        k = 2 / (period + 1)
        ema_val = data[0]
        for p in data[1:]:
            ema_val = p * k + ema_val * (1 - k)
        return ema_val
    ema12 = ema(prices[-26:], 12)
    ema26 = ema(prices[-26:], 26)
    macd_line = ema12 - ema26
    signal = ema(prices[-9:], 9) if len(prices) >= 35 else macd_line
    return round(macd_line, 4), round(signal, 4)

def calc_bollinger(prices, period=20):
    if len(prices) < period:
        return None, None, None
    recent = prices[-period:]
    ma = sum(recent) / period
    std = (sum((p - ma) ** 2 for p in recent) / period) ** 0.5
    return round(ma + 2*std, 2), round(ma, 2), round(ma - 2*std, 2)

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    return round(sum(trs) / period, 4)

def calc_adx(highs, lows, closes, period=14):
    """ADX — עוצמת מגמה. גבוה (>25) = מגמה חזקה, נמוך (<20) = דשדוש."""
    n = len(closes)
    if n < period * 2:
        return None
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    def wilder(values, p):
        if len(values) < p:
            return []
        out = [sum(values[:p])]
        for v in values[p:]:
            out.append(out[-1] - out[-1]/p + v)
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
        return round(sum(dx_list)/len(dx_list), 2) if dx_list else None
    adx = sum(dx_list[:period]) / period
    for v in dx_list[period:]:
        adx = (adx*(period-1) + v) / period
    return round(adx, 2)

def check_breakout(prices, highs, lows, candles=20):
    """פריצה מעל/מתחת לטווח האחרון (בלי תנאי נפח)."""
    if len(prices) < candles + 1:
        return None
    recent_highs = highs[-candles-1:-1]
    recent_lows = lows[-candles-1:-1]
    current = prices[-1]
    if current > max(recent_highs):
        return "למעלה"
    elif current < min(recent_lows):
        return "למטה"
    return None

# ============================================================
# בדיקות מגבלות
# ============================================================
def is_trading_hours(symbol_name):
    now = datetime.datetime.now()
    hours = TRADING_HOURS.get(symbol_name, {"start": 8, "end": 22})
    return hours["start"] <= now.hour < hours["end"]

def get_today_key():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def can_trade(symbol_name, data):
    today = get_today_key()
    daily = data["daily_stats"].get(today, {})

    entered = daily.get("entered", 0)
    signals_sent = daily.get("signals_sent", 0)

    # נכנסת ל-10 עסקאות → עצור. אחרת, איתותים ממשיכים עד תקרה של 20 שנשלחו.
    if entered >= MAX_ENTERED_PER_DAY:
        return False, f"הגעת ל-{MAX_ENTERED_PER_DAY} עסקאות היום"
    if signals_sent >= MAX_SIGNALS_PER_DAY:
        return False, f"נשלחו {MAX_SIGNALS_PER_DAY} איתותים היום (תקרה)"

    open_trades = [t for t in data["trades"] if t["status"] == "open"]
    if len(open_trades) >= MAX_PARALLEL_TRADES:
        return False, "2 עסקאות פתוחות כבר"

    # מגבלת הפסד יומית — מנוטרלת
    if DAILY_LOSS_LIMIT is not None:
        daily_pnl = daily.get("pnl", 0)
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, "הגעת למגבלת הפסד יומית"

    now = datetime.datetime.now()
    recent_signals = [
        s for s in data["signal_history"]
        if s["symbol"] == symbol_name and
        (now - datetime.datetime.fromisoformat(s["time"])).total_seconds() < SIGNAL_COOLDOWN_MINUTES * 60
    ]
    if recent_signals:
        return False, f"cooldown על {symbol_name}"

    return True, ""

def check_open_trades_for_symbol(symbol_name, data):
    for t in data["trades"]:
        if t["symbol"] == symbol_name and t["status"] == "open":
            return t
    return None

# ============================================================
# ניתוח ושליחת סיגנל
# ============================================================
def analyze_and_signal(symbol_name, symbol_code, data):
    if not is_trading_hours(symbol_name):
        print(f"[{symbol_name}] לא בשעות מסחר", flush=True)
        return

    can, reason = can_trade(symbol_name, data)
    if not can:
        print(f"[{symbol_name}] לא שולח: {reason}", flush=True)
        return

    prices_data = get_prices(symbol_code)
    if not prices_data:
        return

    closes = prices_data["closes"]
    highs = prices_data["highs"]
    lows = prices_data["lows"]
    current = closes[-1]

    rsi = calc_rsi(closes)
    macd_line, macd_signal = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    atr = calc_atr(highs, lows, closes)
    adx = calc_adx(highs, lows, closes)
    breakout_dir = check_breakout(closes, highs, lows)
    daily_trend = get_daily_trend(symbol_code)

    weights = data["indicator_weights"]
    signals = []
    direction = None
    score = 0

    if rsi is not None:
        if rsi <= 30:
            signals.append(f"🔵 RSI = {rsi} (מכירת יתר)")
            score += 1 * weights["rsi"]
            direction = "קנייה"
        elif rsi >= 70:
            signals.append(f"🔴 RSI = {rsi} (קנייה יתר)")
            score += 1 * weights["rsi"]
            direction = "מכירה"

    if macd_line is not None:
        if macd_line > macd_signal and (direction == "קנייה" or direction is None):
            signals.append(f"📈 MACD חיובי ({macd_line})")
            score += 1 * weights["macd"]
            if direction is None:
                direction = "קנייה"
        elif macd_line < macd_signal and (direction == "מכירה" or direction is None):
            signals.append(f"📉 MACD שלילי ({macd_line})")
            score += 1 * weights["macd"]
            if direction is None:
                direction = "מכירה"

    if bb_upper and bb_lower:
        if current <= bb_lower and (direction == "קנייה" or direction is None):
            signals.append(f"📊 מתחת לרצועה תחתונה ({bb_lower})")
            score += 1 * weights["bollinger"]
            if direction is None:
                direction = "קנייה"
        elif current >= bb_upper and (direction == "מכירה" or direction is None):
            signals.append(f"📊 מעל רצועה עליונה ({bb_upper})")
            score += 1 * weights["bollinger"]
            if direction is None:
                direction = "מכירה"

    if breakout_dir:
        if breakout_dir == "למעלה" and (direction == "קנייה" or direction is None):
            signals.append("💥 פריצה למעלה")
            score += 1 * weights["breakout"]
            direction = "קנייה"
        elif breakout_dir == "למטה" and (direction == "מכירה" or direction is None):
            signals.append("💥 פריצה למטה")
            score += 1 * weights["breakout"]
            direction = "מכירה"

    # ADX — מחזק מגמה חזקה, מחליש דשדוש (במקום הווליום)
    if adx is not None and direction:
        if adx >= 25:
            signals.append(f"💪 ADX = {adx} (מגמה חזקה)")
            score += 1 * weights["adx"]
        elif adx < 20:
            signals.append(f"〰️ ADX = {adx} (מגמה חלשה)")
            score -= 1 * weights["adx"]

    trend_bonus = ""
    if daily_trend and direction:
        if daily_trend == "עלייה" and direction == "קנייה":
            score += 0.5
            trend_bonus = "✅ עם המגמה"
        elif daily_trend == "ירידה" and direction == "מכירה":
            score += 0.5
            trend_bonus = "✅ עם המגמה"
        elif daily_trend != "ניטרלי":
            score -= 0.5
            trend_bonus = "⚠️ נגד המגמה"

    stars = min(5, max(1, round(score)))
    star_display = "⭐" * stars

    if not direction or stars < 2:
        print(f"[{datetime.datetime.now().strftime('%H:%M')}] {symbol_name}: ציון {stars} — לא מספיק", flush=True)
        return

    open_trade = check_open_trades_for_symbol(symbol_name, data)
    reversal_warning = ""
    if open_trade and open_trade["direction"] != direction:
        reversal_warning = f"\n⚠️ <b>היפוך כיוון!</b> היית ב{open_trade['direction']}\n"

    if atr:
        stop_distance = atr * 1.5
    else:
        stop_distance = current * 0.005

    entry_price = round(current, 2)
    tp2_mult = 4 if stars >= 4 else 3

    # סיכון אמיתי בשקלים
    risk_amount = round(points_to_ils(stop_distance) + SPREAD_COST_ILS, 2)
    risk_pct = round(risk_amount / ACCOUNT_SIZE * 100, 1)

    if direction == "קנייה":
        stop = round(current - stop_distance, 2)
        target1 = round(current + stop_distance * 2, 2)
        target2 = round(current + stop_distance * tp2_mult, 2)
    else:
        stop = round(current + stop_distance, 2)
        target1 = round(current - stop_distance * 2, 2)
        target2 = round(current - stop_distance * tp2_mult, 2)

    now = datetime.datetime.now()
    timeout_time = (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).strftime("%H:%M")
    trend_line = f"📈 מגמה: {daily_trend} {trend_bonus}\n" if daily_trend else ""

    # מספר עסקה רץ
    data["trade_counter"] = data.get("trade_counter", 0) + 1
    trade_num = data["trade_counter"]

    # שמור pending
    trade_id = make_trade_id(symbol_name, now.strftime('%H%M%S'))
    pending = load_pending()
    pending[trade_id] = {
        "number": trade_num,
        "symbol": symbol_name,
        "direction": direction,
        "entry": entry_price,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "time": now.isoformat()
    }
    cutoff = (now - datetime.timedelta(hours=1)).isoformat()
    pending = {k: v for k, v in pending.items() if v["time"] > cutoff}
    save_pending(pending)

    msg = (
        f"🚨 <b>איתות סחר #{trade_num} — {symbol_name}</b>\n"
        f"🕐 {now.strftime('%H:%M')} | {star_display} {stars}/5\n"
        f"{reversal_warning}"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 כיוון: <b>{'קנייה 🟢' if direction == 'קנייה' else 'מכירה 🔴'}</b>\n"
        f"💰 כניסה: <b>{entry_price}</b>\n"
        f"🛑 סטופ: <b>{stop}</b>\n"
        f"🎯 טארגט 1: <b>{target1}</b> (סוגר הכל)\n"
        f"🎯 טארגט 2: <b>{target2}</b> (מידע)\n"
        f"💸 סיכון: {risk_amount} ש\"ח ({risk_pct}% מהחשבון)\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{trend_line}"
        f"🔍 " + " | ".join(signals) + "\n"
        f"⏰ יציאה: {timeout_time}"
    )

    keyboard = [[
        {"text": "✅ נכנסתי", "callback_data": f"en_{trade_id}"},
        {"text": "❌ דילגתי", "callback_data": f"sk_{trade_id}"}
    ]]

    send_telegram(msg, keyboard)

    data["signal_history"].append({
        "symbol": symbol_name,
        "time": now.isoformat(),
        "direction": direction,
        "score": stars,
        "price": entry_price
    })
    cutoff2 = (now - datetime.timedelta(hours=2)).isoformat()
    data["signal_history"] = [s for s in data["signal_history"] if s["time"] > cutoff2]

    today = get_today_key()
    if today not in data["daily_stats"]:
        data["daily_stats"][today] = {}
    data["daily_stats"][today]["signals_sent"] = data["daily_stats"][today].get("signals_sent", 0) + 1

    save_data(data)
    print(f"[{now.strftime('%H:%M')}] ✅ איתות #{trade_num}: {symbol_name} {direction} ציון {stars}", flush=True)

# ============================================================
# טיפול בתגובות משתמש
# ============================================================
def handle_callbacks(data, last_update_id):
    updates = get_updates(last_update_id + 1)
    for update in updates:
        last_update_id = update["update_id"]

        if "callback_query" in update:
            cb = update["callback_query"]
            answer_callback(cb["id"])
            cbd = cb["data"]
            print(f"[CALLBACK] קיבלתי: {cbd}", flush=True)

            pending = load_pending()

            # ✅ נכנסתי לעסקה
            if cbd.startswith("en_"):
                trade_id = cbd[3:]
                signal = pending.get(trade_id)
                if not signal:
                    send_telegram("⚠️ הסיגנל פג תוקף")
                    continue
                # מנע כניסה כפולה
                if any(t["id"] == trade_id for t in data["trades"]):
                    send_telegram("⚠️ כבר נכנסת לעסקה הזו")
                    continue
                now = datetime.datetime.now()
                timeout = (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).isoformat()
                num = signal.get("number", "?")
                trade = {
                    "id": trade_id,
                    "number": num,
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "entry": signal["entry"],
                    "stop": signal["stop"],
                    "target1": signal["target1"],
                    "target2": signal["target2"],
                    "entry_time": now.isoformat(),
                    "timeout": timeout,
                    "status": "open",
                    "target_alerted": False,
                    "stop_alerted": False,
                    "timeout_sent": False
                }
                data["trades"].append(trade)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["entered"] = data["daily_stats"][today].get("entered", 0) + 1
                save_data(data)
                keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade_id}"}]]
                send_telegram(
                    f"✅ <b>עסקה #{num} נפתחה — {signal['symbol']}</b>\n"
                    f"כיוון: {'קנייה 🟢' if signal['direction'] == 'קנייה' else 'מכירה 🔴'}\n"
                    f"כניסה: {signal['entry']} | סטופ: {signal['stop']}\n"
                    f"🎯 טארגט 1: {signal['target1']}\n"
                    f"⏰ תזכורת: {datetime.datetime.fromisoformat(timeout).strftime('%H:%M')}",
                    keyboard
                )
                print(f"[CALLBACK] ✅ עסקה #{num} נפתחה: {trade_id}", flush=True)

            # ❌ דילגתי
            elif cbd.startswith("sk_"):
                send_telegram("❌ דילגת — ממשיך לסרוק 👀")
                print(f"[CALLBACK] ❌ דילג: {cbd[3:]}", flush=True)

            # 🔒 סגרתי עסקה (תפריט סגירה ידני)
            elif cbd.startswith("cl_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה פתוחה")
                    continue
                keyboard = [
                    [{"text": "🎯 הגעתי לטארגט", "callback_data": f"rf_{trade_id}"}],
                    [{"text": "💰 יצאתי מוקדם", "callback_data": f"re_{trade_id}"}],
                    [{"text": "❌ יצאתי בהפסד", "callback_data": f"rl_{trade_id}"}]
                ]
                send_telegram(f"📊 <b>עסקה #{trade.get('number','?')} — איך יצאת?</b>", keyboard)
                print(f"[CALLBACK] 🔒 סגירה: {trade_id}", flush=True)

            # תוצאה — רווח (טארגט 1 סוגר הכל)
            elif cbd.startswith("rf_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                # רווח אמיתי בשקלים, מבוסס טארגט 1 (1:2), פחות ספרד
                risk_distance = abs(trade["entry"] - trade["stop"])
                reward_distance = abs(trade["target1"] - trade["entry"])
                r_multiple = (reward_distance / risk_distance) if risk_distance else 0
                pnl = round(points_to_ils(reward_distance) - SPREAD_COST_ILS, 2)
                trade["status"] = "closed"
                trade["result"] = "win"
                trade["pnl"] = pnl
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) + pnl, 2)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = round(data["daily_stats"][today].get("pnl", 0) + pnl, 2)
                save_data(data)
                send_telegram(f"🎉 <b>רווח! עסקה #{trade.get('number','?')}</b>\n💰 +{pnl} ש\"ח (יחס 1:{round(r_multiple, 1)})")
                update_indicator_weights(data)

            # תוצאה — יצאתי מוקדם
            elif cbd.startswith("re_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                trade["waiting_early_exit"] = True
                save_data(data)
                send_telegram("💰 <b>כמה עשית?</b>\nשלח לי את הסכום בש\"ח")

            # תוצאה — הפסד
            elif cbd.startswith("rl_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                # הפסד אמיתי בשקלים, מבוסס מרחק הסטופ, פלוס ספרד
                risk_distance = abs(trade["entry"] - trade["stop"])
                loss = round(points_to_ils(risk_distance) + SPREAD_COST_ILS, 2)
                trade["status"] = "closed"
                trade["result"] = "loss"
                trade["pnl"] = -loss
                data["all_time_stats"]["losses"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) - loss, 2)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = round(data["daily_stats"][today].get("pnl", 0) - loss, 2)
                save_data(data)
                send_telegram(f"📉 <b>הפסד — עסקה #{trade.get('number','?')}</b>\n💸 -{loss} ש\"ח")
                update_indicator_weights(data)

        elif "message" in update:
            msg = update["message"]
            text = msg.get("text", "").strip()
            waiting_trade = next((t for t in data["trades"] if t.get("waiting_early_exit") and t["status"] == "open"), None)
            if waiting_trade and text.replace(".", "").isdigit():
                amount = float(text)
                # יעילות מול הרווח האמיתי בטארגט 1 (שקלים)
                reward_distance = abs(waiting_trade["target1"] - waiting_trade["entry"])
                full_target_profit = points_to_ils(reward_distance) - SPREAD_COST_ILS
                efficiency = round((amount / full_target_profit) * 100) if full_target_profit > 0 else 0
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = round(data["daily_stats"][today].get("pnl", 0) + amount, 2)
                waiting_trade["status"] = "closed"
                waiting_trade["result"] = "early_exit"
                waiting_trade["pnl"] = amount
                waiting_trade.pop("waiting_early_exit", None)
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["early_exits"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) + amount, 2)
                send_telegram(
                    f"✅ <b>יציאה מוקדמת — עסקה #{waiting_trade.get('number','?')}</b>\n"
                    f"💰 {amount} ש\"ח\n"
                    f"📊 יעילות: {efficiency}%"
                    + ("\n💡 השארת כסף — שקול לתת לרוץ יותר" if efficiency < 60 else "")
                )
                save_data(data)

    return last_update_id

# ============================================================
# מעקב עסקאות פתוחות + זיהוי אוטומטי (שיטה א: high/low)
# ============================================================
def monitor_open_trades(data):
    now = datetime.datetime.now()
    candle_cache = {}
    changed = False

    for trade in data["trades"]:
        if trade["status"] != "open":
            continue

        symbol_name = trade["symbol"]
        symbol_code = SYMBOLS.get(symbol_name)
        if not symbol_code:
            continue

        if symbol_code not in candle_cache:
            pd = get_prices(symbol_code, outputsize=5)
            if pd and pd["closes"]:
                candle_cache[symbol_code] = {
                    "high": pd["highs"][-1],
                    "low": pd["lows"][-1],
                    "close": pd["closes"][-1]
                }
        candle = candle_cache.get(symbol_code)
        if not candle:
            continue

        high = candle["high"]
        low = candle["low"]
        current = candle["close"]
        direction = trade["direction"]
        stop = trade["stop"]
        target1 = trade["target1"]
        num = trade.get("number", "?")

        # קביעת נגיעה לפי high/low של הנר
        target_hit = (high >= target1) if direction == "קנייה" else (low <= target1)
        stop_hit = (low <= stop) if direction == "קנייה" else (high >= stop)

        # עדיפות לסטופ (שמרני) במקרה ששניהם נגעו באותו נר
        if stop_hit and not trade.get("stop_alerted"):
            trade["stop_alerted"] = True
            changed = True
            keyboard = [[{"text": "🛑 סגור בהפסד", "callback_data": f"rl_{trade['id']}"}]]
            send_telegram(
                f"🛑 <b>עסקה #{num} — נגעת בסטופ!</b>\n"
                f"{symbol_name} | מחיר: {current}\n"
                f"סטופ: {stop}\n"
                f"אשר סגירה בהפסד 👇",
                keyboard
            )
            print(f"[AUTO] 🛑 סטופ עסקה #{num}", flush=True)

        elif target_hit and not trade.get("target_alerted"):
            trade["target_alerted"] = True
            changed = True
            keyboard = [[{"text": "✅ סגור ברווח", "callback_data": f"rf_{trade['id']}"}]]
            send_telegram(
                f"🎯 <b>עסקה #{num} — נגעת בטארגט 1!</b>\n"
                f"{symbol_name} | מחיר: {current}\n"
                f"טארגט: {target1}\n"
                f"אשר סגירה ברווח 👇",
                keyboard
            )
            print(f"[AUTO] 🎯 טארגט עסקה #{num}", flush=True)

        # תזכורת timeout
        timeout = datetime.datetime.fromisoformat(trade["timeout"])
        if now >= timeout and not trade.get("timeout_sent"):
            trade["timeout_sent"] = True
            changed = True
            keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade['id']}"}]]
            send_telegram(
                f"⏰ <b>תזכורת — עסקה #{num}</b>\n"
                f"פתוחה {TRADE_TIMEOUT_HOURS} שעות!\n"
                f"מחיר: {current} | כניסה: {trade['entry']}",
                keyboard
            )

    if changed:
        save_data(data)

# ============================================================
# למידה
# ============================================================
def update_indicator_weights(data):
    stats = data["all_time_stats"]
    total = stats["total_trades"]
    if total < 20:
        return
    win_rate = stats["wins"] / total
    if win_rate < 0.45:
        data["indicator_weights"]["breakout"] = max(0.5, data["indicator_weights"]["breakout"] - 0.1)
        data["indicator_weights"]["rsi"] = min(1.5, data["indicator_weights"]["rsi"] + 0.1)
        send_telegram(f"🧠 משקלים עודכנו | אחוז הצלחה: {round(win_rate*100)}%")
    elif win_rate > 0.65:
        data["indicator_weights"]["breakout"] = min(1.5, data["indicator_weights"]["breakout"] + 0.05)
    save_data(data)

# ============================================================
# דוח יומי
# ============================================================
def send_daily_report(data):
    today = get_today_key()
    daily = data["daily_stats"].get(today, {})
    stats = data["all_time_stats"]
    signals_today = daily.get("signals_sent", 0)
    entered_today = daily.get("entered", 0)
    pnl_today = daily.get("pnl", 0)
    win_rate = round(stats["wins"] / stats["total_trades"] * 100) if stats["total_trades"] > 0 else 0
    total_pnl = round(stats.get("total_pnl", 0), 2)
    send_telegram(
        f"📊 <b>דוח יומי — {today}</b>\n\n"
        f"🔔 איתותים שנשלחו: {signals_today}\n"
        f"✅ עסקאות שנכנסת: {entered_today}\n"
        f"💰 רווח/הפסד היום: {round(pnl_today, 2)} ש\"ח\n\n"
        f"📈 סה\"כ עסקאות: {stats['total_trades']}\n"
        f"✅ רווחים: {stats['wins']} | ❌ הפסדים: {stats['losses']}\n"
        f"📊 אחוז הצלחה: {win_rate}%\n"
        f"🏦 רווח/הפסד מצטבר: {total_pnl} ש\"ח"
    )

# ============================================================
# לולאה ראשית
# ============================================================
def main():
    print("🤖 בוט מסחר מופעל!", flush=True)
    print(f"TOKEN exists: {bool(TELEGRAM_TOKEN)}", flush=True)
    print(f"CHAT_ID: {CHAT_ID}", flush=True)

    data = load_data()
    send_telegram(
        "🤖 <b>בוט המסחר הופעל!</b>\n\n"
        "📊 סורק: זהב (XAU/USD)\n"
        "⏰ כל 10 דקות\n"
        "🕐 שעות: 08:00—22:00 (ישראל)\n\n"
        "🔍 אינדיקטורים: RSI, MACD, Bollinger, פריצה, ADX\n"
        "🎯 זיהוי אוטומטי של טארגט/סטופ (אישור ידני)"
    )

    last_update_id = 0
    last_daily_report = ""
    last_morning_ping = ""
    scan_count = 0

    # הפרדה: כפתורים נבדקים כל POLL_INTERVAL שניות, סריקת שוק כל SCAN_INTERVAL שניות
    SCAN_INTERVAL = 600   # 10 דקות בין סריקות שוק
    POLL_INTERVAL = 2     # תדירות בדיקת כפתורים (שניות)
    last_scan_time = 0    # 0 → סריקה ראשונה מיד

    while True:
        try:
            now = datetime.datetime.now()

            # --- בדיקת כפתורים (תכופה → תגובה מיידית) ---
            last_update_id = handle_callbacks(data, last_update_id)
            data = load_data()

            # --- סריקת שוק + מעקב עסקאות: כל 10 דקות ---
            if time.time() - last_scan_time >= SCAN_INTERVAL:
                last_scan_time = time.time()
                scan_count += 1
                print(f"\n--- סריקה #{scan_count} {now.strftime('%H:%M:%S')} ---", flush=True)

                for name, code in SYMBOLS.items():
                    try:
                        analyze_and_signal(name, code, data)
                        data = load_data()
                        time.sleep(3)
                    except Exception as e:
                        print(f"שגיאה ב{name}: {e}", flush=True)

                monitor_open_trades(data)
                data = load_data()
                print("סריקה הסתיימה — כפתורים ממשיכים לעבוד עד הסריקה הבאה.", flush=True)

            # --- דוח יומי (פעם ביום, בסוף שעות המסחר) ---
            today_key = now.strftime("%Y-%m-%d")
            if now.hour == 22 and now.minute < 11 and last_daily_report != today_key:
                send_daily_report(data)
                last_daily_report = today_key

            # --- פינג בוקר (פעם ביום) ---
            if now.hour == 8 and now.minute < 11 and last_morning_ping != today_key:
                send_telegram(f"✅ בוט פעיל | {now.strftime('%d/%m/%Y')}")
                last_morning_ping = today_key

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"שגיאה כללית: {e}", flush=True)
            try:
                send_telegram(f"⚠️ שגיאה: {e}")
            except:
                pass
            time.sleep(30)

if __name__ == "__main__":
    main()
