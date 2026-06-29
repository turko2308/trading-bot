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

# ⬇️ שינוי: נפט (WTI) הוחלף בכסף (XAG/USD).
# הסמל "WTI" החזיר מניה (W&T Offshore), והסמל הנכון WTI/USD לא זמין בחינמי.
# כסף הוא מתכת כמו זהב, זמין בחינמי, ונסחר ב-Plus500.
# רוצה אחר? החלף את "XAG/USD" ל-"EUR/USD" או "BTC/USD" (שניהם חינמיים + ב-Plus500).
SYMBOLS = {
    "זהב": "XAU/USD",
    "כסף": "XAG/USD"
}

TRADING_HOURS = {
    "זהב": {"start": 10, "end": 23},
    "כסף": {"start": 10, "end": 23}
}

MAX_TRADES_PER_DAY = 3
MAX_PARALLEL_TRADES = 2
DAILY_LOSS_LIMIT = 30
ACCOUNT_SIZE = 500
RISK_PER_TRADE = 0.02
TRADE_TIMEOUT_HOURS = 6
SIGNAL_COOLDOWN_MINUTES = 30

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
        "indicator_weights": {
            "rsi": 1.0,
            "macd": 1.0,
            "bollinger": 1.0,
            "breakout": 1.0
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
                return json.load(f)
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
        volumes = [float(v.get("volume", 0)) for v in reversed(data["values"])]
        highs = [float(v["high"]) for v in reversed(data["values"])]
        lows = [float(v["low"]) for v in reversed(data["values"])]
        return {"closes": closes, "volumes": volumes, "highs": highs, "lows": lows}
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

def check_breakout(prices, highs, lows, volumes, candles=20):
    if len(prices) < candles + 1:
        return None, False
    recent_highs = highs[-candles-1:-1]
    recent_lows = lows[-candles-1:-1]
    current = prices[-1]
    curr_volume = volumes[-1] if volumes[-1] > 0 else 1
    avg_volume = sum(volumes[-candles:]) / candles if candles <= len(volumes) else 1
    volume_strong = curr_volume > avg_volume * 1.2
    if current > max(recent_highs):
        return "למעלה", volume_strong
    elif current < min(recent_lows):
        return "למטה", volume_strong
    return None, False

# ============================================================
# בדיקות מגבלות
# ============================================================
def is_trading_hours(symbol_name):
    now = datetime.datetime.now()
    hours = TRADING_HOURS.get(symbol_name, {"start": 9, "end": 23})
    return hours["start"] <= now.hour < hours["end"]

def get_today_key():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def can_trade(symbol_name, data):
    today = get_today_key()
    daily = data["daily_stats"].get(today, {})

    symbol_trades = daily.get(f"trades_{symbol_name}", 0)
    if symbol_trades >= MAX_TRADES_PER_DAY:
        return False, f"מקסימום {MAX_TRADES_PER_DAY} איתותים היום על {symbol_name}"

    open_trades = [t for t in data["trades"] if t["status"] == "open"]
    if len(open_trades) >= MAX_PARALLEL_TRADES:
        return False, "2 עסקאות פתוחות כבר"

    daily_pnl = daily.get("pnl", 0)
    if daily_pnl <= -DAILY_LOSS_LIMIT:
        return False, f"הגעת למגבלת הפסד יומית"

    now = datetime.datetime.now()
    recent_signals = [
        s for s in data["signal_history"]
        if s["symbol"] == symbol_name and
        (now - datetime.datetime.fromisoformat(s["time"])).seconds < SIGNAL_COOLDOWN_MINUTES * 60
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
    volumes = prices_data["volumes"]
    current = closes[-1]

    rsi = calc_rsi(closes)
    macd_line, macd_signal = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    atr = calc_atr(highs, lows, closes)
    breakout_dir, volume_strong = check_breakout(closes, highs, lows, volumes)
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
        vol_txt = "נפח גבוה ✅" if volume_strong else "נפח נמוך ⚠️"
        if breakout_dir == "למעלה" and (direction == "קנייה" or direction is None):
            signals.append(f"💥 פריצה למעלה | {vol_txt}")
            score += (1.5 if volume_strong else 0.5) * weights["breakout"]
            direction = "קנייה"
        elif breakout_dir == "למטה" and (direction == "מכירה" or direction is None):
            signals.append(f"💥 פריצה למטה | {vol_txt}")
            score += (1.5 if volume_strong else 0.5) * weights["breakout"]
            direction = "מכירה"

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

    risk_amount = round(ACCOUNT_SIZE * RISK_PER_TRADE, 1)
    if atr:
        stop_distance = atr * 1.5
    else:
        stop_distance = current * 0.005

    if direction == "קנייה":
        stop = round(current - stop_distance, 2)
        target1 = round(current + stop_distance * 2, 2)
        target2 = round(current + stop_distance * (3 if stars == 5 else 2), 2)
    else:
        stop = round(current + stop_distance, 2)
        target1 = round(current - stop_distance * 2, 2)
        target2 = round(current - stop_distance * (3 if stars == 5 else 2), 2)

    now = datetime.datetime.now()
    timeout_time = (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).strftime("%H:%M")
    trend_line = f"📈 מגמה: {daily_trend} {trend_bonus}\n" if daily_trend else ""

    # שמור pending
    trade_id = make_trade_id(symbol_name, now.strftime('%H%M%S'))
    pending = load_pending()
    pending[trade_id] = {
        "symbol": symbol_name,
        "direction": direction,
        "entry": current,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "time": now.isoformat()
    }
    cutoff = (now - datetime.timedelta(hours=1)).isoformat()
    pending = {k: v for k, v in pending.items() if v["time"] > cutoff}
    save_pending(pending)

    msg = (
        f"🚨 <b>איתות סחר — {symbol_name}</b>\n"
        f"🕐 {now.strftime('%H:%M')} | {star_display} {stars}/5\n"
        f"{reversal_warning}"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 כיוון: <b>{'קנייה 🟢' if direction == 'קנייה' else 'מכירה 🔴'}</b>\n"
        f"💰 כניסה: <b>{current}</b>\n"
        f"🛑 סטופ: <b>{stop}</b>\n"
        f"🎯 טארגט 1: <b>{target1}</b>\n"
        f"🎯 טארגט 2: <b>{target2}</b>\n"
        f"💸 סיכון: {risk_amount} ש\"ח\n"
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
        "price": current
    })
    cutoff2 = (now - datetime.timedelta(hours=2)).isoformat()
    data["signal_history"] = [s for s in data["signal_history"] if s["time"] > cutoff2]

    today = get_today_key()
    if today not in data["daily_stats"]:
        data["daily_stats"][today] = {}
    key = f"trades_{symbol_name}"
    data["daily_stats"][today][key] = data["daily_stats"][today].get(key, 0) + 1

    save_data(data)
    print(f"[{now.strftime('%H:%M')}] ✅ איתות: {symbol_name} {direction} ציון {stars}", flush=True)

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
                now = datetime.datetime.now()
                timeout = (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).isoformat()
                trade = {
                    "id": trade_id,
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "entry": signal["entry"],
                    "stop": signal["stop"],
                    "target1": signal["target1"],
                    "target2": signal["target2"],
                    "entry_time": now.isoformat(),
                    "timeout": timeout,
                    "status": "open",
                    "trailing_stop": signal["stop"]
                }
                data["trades"].append(trade)
                save_data(data)
                keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade_id}"}]]
                send_telegram(
                    f"✅ <b>עסקה נפתחה — {signal['symbol']}</b>\n"
                    f"כיוון: {'קנייה 🟢' if signal['direction'] == 'קנייה' else 'מכירה 🔴'}\n"
                    f"כניסה: {signal['entry']} | סטופ: {signal['stop']}\n"
                    f"⏰ תזכורת: {datetime.datetime.fromisoformat(timeout).strftime('%H:%M')}",
                    keyboard
                )
                print(f"[CALLBACK] ✅ עסקה נפתחה: {trade_id}", flush=True)

            # ❌ דילגתי
            elif cbd.startswith("sk_"):
                send_telegram("❌ דילגת — ממשיך לסרוק 👀")
                print(f"[CALLBACK] ❌ דילג: {cbd[3:]}", flush=True)

            # 🔒 סגרתי עסקה
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
                send_telegram("📊 <b>איך יצאת מהעסקה?</b>", keyboard)
                print(f"[CALLBACK] 🔒 סגירה: {trade_id}", flush=True)

            # תוצאה — הגעתי לטארגט
            elif cbd.startswith("rf_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                pnl = abs(trade["target2"] - trade["entry"])
                trade["status"] = "closed"
                trade["result"] = "win"
                trade["pnl"] = pnl
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["total_trades"] += 1
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = data["daily_stats"][today].get("pnl", 0) + pnl
                save_data(data)
                send_telegram(f"🎉 <b>רווח!</b>\n💰 +{round(pnl,2)} נקודות")
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
                risk = ACCOUNT_SIZE * RISK_PER_TRADE
                trade["status"] = "closed"
                trade["result"] = "loss"
                trade["pnl"] = -risk
                data["all_time_stats"]["losses"] += 1
                data["all_time_stats"]["total_trades"] += 1
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = data["daily_stats"][today].get("pnl", 0) - risk
                save_data(data)
                send_telegram(f"📉 <b>הפסד</b>\n💸 -{round(risk,2)} ש\"ח")
                update_indicator_weights(data)

        elif "message" in update:
            msg = update["message"]
            text = msg.get("text", "").strip()
            waiting_trade = next((t for t in data["trades"] if t.get("waiting_early_exit") and t["status"] == "open"), None)
            if waiting_trade and text.replace(".", "").isdigit():
                amount = float(text)
                target_amount = abs(waiting_trade["target2"] - waiting_trade["entry"])
                efficiency = round((amount / target_amount) * 100) if target_amount else 0
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = data["daily_stats"][today].get("pnl", 0) + amount
                waiting_trade["status"] = "closed"
                waiting_trade["result"] = "early_exit"
                waiting_trade["pnl"] = amount
                waiting_trade.pop("waiting_early_exit", None)
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["early_exits"] += 1
                data["all_time_stats"]["total_trades"] += 1
                send_telegram(
                    f"✅ <b>יציאה מוקדמת</b>\n"
                    f"💰 {amount} ש\"ח\n"
                    f"📊 יעילות: {efficiency}%"
                    + ("\n💡 השארת כסף — שקול לתת לרוץ יותר" if efficiency < 60 else "")
                )
                save_data(data)

    return last_update_id

# ============================================================
# מעקב עסקאות פתוחות
# ============================================================
def monitor_open_trades(data):
    now = datetime.datetime.now()
    prices_cache = {}

    for trade in data["trades"]:
        if trade["status"] != "open":
            continue

        symbol_name = trade["symbol"]
        symbol_code = SYMBOLS.get(symbol_name)
        if not symbol_code:
            continue

        if symbol_code not in prices_cache:
            pd = get_prices(symbol_code, outputsize=5)
            if pd:
                prices_cache[symbol_code] = pd["closes"][-1]
        current_price = prices_cache.get(symbol_code)
        if not current_price:
            continue

        entry = trade["entry"]
        stop = trade["stop"]
        direction = trade["direction"]

        if direction == "קנייה":
            if current_price > entry:
                new_stop = round(current_price - (current_price - entry) * 0.5, 2)
                if new_stop > trade["trailing_stop"]:
                    trade["trailing_stop"] = new_stop
            distance_to_stop = current_price - stop
            total_range = entry - stop
            if total_range > 0 and distance_to_stop / total_range < 0.2:
                send_telegram(f"⚠️ <b>{symbol_name} מתקרב לסטופ!</b>\nמחיר: {current_price} | סטופ: {stop}")
        else:
            if current_price < entry:
                new_stop = round(current_price + (entry - current_price) * 0.5, 2)
                if new_stop < trade["trailing_stop"]:
                    trade["trailing_stop"] = new_stop
            distance_to_stop = stop - current_price
            total_range = stop - entry
            if total_range > 0 and distance_to_stop / total_range < 0.2:
                send_telegram(f"⚠️ <b>{symbol_name} מתקרב לסטופ!</b>\nמחיר: {current_price} | סטופ: {stop}")

        timeout = datetime.datetime.fromisoformat(trade["timeout"])
        if now >= timeout and not trade.get("timeout_sent"):
            trade["timeout_sent"] = True
            keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade['id']}"}]]
            send_telegram(
                f"⏰ <b>תזכורת — {symbol_name}</b>\n"
                f"פתוחה {TRADE_TIMEOUT_HOURS} שעות!\n"
                f"מחיר: {current_price} | כניסה: {entry}",
                keyboard
            )

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
    total_today = sum(v for k, v in daily.items() if k.startswith("trades_"))
    pnl_today = daily.get("pnl", 0)
    win_rate = round(stats["wins"] / stats["total_trades"] * 100) if stats["total_trades"] > 0 else 0
    send_telegram(
        f"📊 <b>דוח יומי — {today}</b>\n\n"
        f"🔔 איתותים: {total_today}\n"
        f"💰 רווח/הפסד: {round(pnl_today, 2)}\n\n"
        f"📈 סה\"כ עסקאות: {stats['total_trades']}\n"
        f"✅ רווחים: {stats['wins']} | ❌ הפסדים: {stats['losses']}\n"
        f"📊 אחוז הצלחה: {win_rate}%"
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
        "📊 סורק: זהב + כסף\n"
        "⏰ כל 15 דקות\n\n"
        "שעות פעילות (ישראל):\n"
        "🥇 זהב: 10:00—22:00\n"
        "🥈 כסף: 10:00—22:00"
    )

    last_update_id = 0
    last_daily_report = ""
    last_morning_ping = ""
    scan_count = 0

    # ⬇️ התיקון המרכזי: הפרדה בין בדיקת כפתורים לסריקת שוק.
    # הכפתורים נבדקים כל POLL_INTERVAL שניות → מגיבים מיד.
    # סריקת השוק רצה כל SCAN_INTERVAL שניות → צריכת API נשארת זהה (כל 15 דק').
    SCAN_INTERVAL = 900   # 15 דקות בין סריקות שוק
    POLL_INTERVAL = 2     # תדירות בדיקת כפתורים (שניות)
    last_scan_time = 0    # 0 → סריקה ראשונה מתבצעת מיד עם העלייה

    while True:
        try:
            now = datetime.datetime.now()

            # --- בדיקת כפתורים (רץ כל ~2 שניות, getUpdates עם long-poll של 5 שניות) ---
            last_update_id = handle_callbacks(data, last_update_id)
            data = load_data()

            # --- סריקת שוק: רק כל 15 דקות ---
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

            # --- דוח יומי (פעם אחת ביום) ---
            today_key = now.strftime("%Y-%m-%d")
            if now.hour == 22 and now.minute < 6 and last_daily_report != today_key:
                send_daily_report(data)
                last_daily_report = today_key

            # --- פינג בוקר (פעם אחת ביום — עם נעילה כדי שלא יישלח שוב ושוב) ---
            if now.hour == 8 and now.minute < 6 and last_morning_ping != today_key:
                send_telegram(f"✅ בוט פעיל | סריקה #{scan_count} | {now.strftime('%d/%m/%Y')}")
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
