import requests
import time
import datetime
import json
import os
import sys
import hashlib

try:
    from zoneinfo import ZoneInfo
    IL_TZ = ZoneInfo("Asia/Jerusalem")
except Exception:
    IL_TZ = None

def now_il():
    """שעון ישראל תמיד — לא תלוי באזור הזמן של השרת (Render = UTC)."""
    if IL_TZ:
        return datetime.datetime.now(IL_TZ)
    return datetime.datetime.now()

# ============================================================
# הגדרות
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "960197631")
TWELVEDATA_KEY = os.environ.get("TWELVE_DATA_API_KEY", "2be6ffca08d942de8903d6aee41a312e")

# ===== אחסון קבוע: GitHub Gist =====
# GIST_ID + GIST_TOKEN מוגדרים ב-Render Environment.
# אם חסרים — הבוט עובד עם /tmp בלבד (נתונים יימחקו ב-deploy) ושולח אזהרה.
GIST_ID = os.environ.get("GIST_ID", "").strip()
GIST_TOKEN = os.environ.get("GIST_TOKEN", "").strip()
GIST_FILENAME = "bot_data.json"
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"

# רק זהב (XAU/USD היחיד שעובד בחינמי של Twelve Data)
SYMBOLS = {
    "זהב": "XAU/USD"
}

# שעות מסחר (שעון ישראל): 08:00–22:00
TRADING_HOURS = {
    "זהב": {"start": 8, "end": 22}
}
# 3.3: אין איתותים חדשים משעה זו (ערב = כניסות מפסידות; backtest 11/07: ‏+203 מול הבסיס).
# המוניטור על עסקאות פתוחות ממשיך כרגיל 24/7.
LAST_ENTRY_HOUR = 19

# ===== מגבלות איתותים =====
MAX_ENTERED_PER_DAY = 10
MAX_SIGNALS_PER_DAY = 20
MAX_PARALLEL_TRADES = 2
SIGNAL_COOLDOWN_MINUTES = 30
TRADE_TIMEOUT_HOURS = 6

# מגבלת הפסד יומית — מנוטרלת לבינתיים (לשלב בדיקות)
DAILY_LOSS_LIMIT = None

# Circuit breaker — אחרי כמה הפסדים רצופים ביום עוצרים איתותים חדשים
CONSECUTIVE_LOSS_LIMIT = 3

ACCOUNT_SIZE = 500
RISK_PER_TRADE = 0.02   # לתצוגה/השוואה בלבד

# ============================================================
# פילטר מגמה קשה (Hard trend filter)
# EMA50 על נרות שעה. קובע איזה כיוון בכלל מותר לפתוח.
# ============================================================
TREND_INTERVAL = "1h"
TREND_EMA_PERIOD = 50
TREND_DEADZONE = 0.003          # ±0.3% סביב ה-EMA = דשדוש, אין איתותים
TREND_CACHE_MINUTES = 30
_trend_cache = {}

# שער ADX מינימלי — מתחת לזה אין מגמה, לא נכנסים
ADX_MIN = 20

# ===== שני תיקונים שאומתו ב-backtest (04/07): =====
# רצפת סטופ — מינימום מרחק גם כש-ATR נמוך, שלא לשבת בתוך הרעש
STOP_FLOOR_PCT = 0.35
# תקרת מתיחה — לא נכנסים כשהמחיר רחוק מדי מה-EMA (מאוחר מדי להצטרף)
MAX_STRETCH_PCT = 1.2

# ניקיון נתונים — שלא יתנפחו לנצח
MAX_STORED_TRADES = 300
DAILY_STATS_KEEP_DAYS = 90

# ============================================================
# כלכלת פוזיציה אמיתית — Plus500, זהב (XAU/USD)
# שים לב: אם אתה פותח בפועל 1.5 אונקיות (ולא 0.75), הרווח/הפסד
# האמיתי כפול ממה שהבוט מציג. עדכן POSITION_SIZE_OZ בהתאם.
# ============================================================
POSITION_SIZE_OZ = 0.75
USD_ILS = 2.99
SPREAD_POINTS = 0.77

def points_to_ils(points):
    return POSITION_SIZE_OZ * abs(points) * USD_ILS

SPREAD_COST_ILS = round(POSITION_SIZE_OZ * SPREAD_POINTS * USD_ILS, 2)

DATA_FILE = "/tmp/bot_data.json"

# ============================================================
# אחסון: Gist (קבוע) + /tmp (גיבוי מקומי מהיר)
#
# עיקרון: קוראים מה-Gist פעם אחת בהפעלה. משם — הנתונים חיים
# בזיכרון. כל שמירה כותבת ל-/tmp (מיידי) וגם דוחפת ל-Gist.
# אם דחיפה ל-Gist נכשלת — מסמנים dirty ומנסים שוב בסריקה הבאה.
# ============================================================
_gist_dirty = False
_storage_source = "default"   # gist / tmp / default / gist_fail

def gist_enabled():
    return bool(GIST_ID and GIST_TOKEN)

def _gist_headers():
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

def gist_load():
    """מחזיר dict אם הצליח (גם ריק), None אם נכשל."""
    try:
        r = requests.get(GIST_API_URL, headers=_gist_headers(), timeout=15)
        if r.status_code == 404:
            print("[GIST] שגיאה 404 — GIST_ID שגוי?", flush=True)
            return None
        if r.status_code == 401:
            print("[GIST] שגיאה 401 — GIST_TOKEN שגוי או בלי הרשאת gist", flush=True)
            return None
        r.raise_for_status()
        f = r.json().get("files", {}).get(GIST_FILENAME)
        if not f:
            print(f"[GIST] הקובץ {GIST_FILENAME} לא נמצא ב-Gist — מתחיל ריק", flush=True)
            return {}
        # קבצים מעל ~1MB חוזרים חתוכים — מושכים מה-raw_url
        if f.get("truncated") and f.get("raw_url"):
            rr = requests.get(f["raw_url"], timeout=15)
            rr.raise_for_status()
            content = rr.text
        else:
            content = f.get("content", "")
        content = content.strip()
        if not content:
            return {}
        return json.loads(content)
    except Exception as e:
        print(f"[GIST] קריאה נכשלה: {e}", flush=True)
        return None

def gist_save(data):
    """דוחף את הנתונים ל-Gist. מחזיר True/False."""
    global _gist_dirty
    try:
        payload = {
            "files": {
                GIST_FILENAME: {
                    "content": json.dumps(data, ensure_ascii=False, indent=2)
                }
            }
        }
        r = requests.patch(GIST_API_URL, headers=_gist_headers(), json=payload, timeout=15)
        if r.status_code >= 400:
            _gist_dirty = True
            print(f"[GIST] שמירה נכשלה {r.status_code}: {r.text[:200]}", flush=True)
            return False
        _gist_dirty = False
        return True
    except Exception as e:
        _gist_dirty = True
        print(f"[GIST] שמירה נכשלה (ינוסה שוב): {e}", flush=True)
        return False

def gist_diagnose():
    """
    אבחון עצמי בהפעלה: למי שייך הטוקן ואילו הרשאות יש לו.
    זה מגלה מיד אם הטוקן שגוי, בלי הרשאת gist, או מחשבון אחר.
    """
    if not gist_enabled():
        print("[GIST-CHECK] GIST_ID/GIST_TOKEN לא מוגדרים", flush=True)
        return
    try:
        r = requests.get("https://api.github.com/user", headers=_gist_headers(), timeout=15)
        if r.status_code == 401:
            print("[GIST-CHECK] ❌ הטוקן לא תקין (401) — הועתק שגוי, פג תוקף או נמחק", flush=True)
            return
        login = r.json().get("login", "?")
        scopes = r.headers.get("X-OAuth-Scopes", "")
        print(f"[GIST-CHECK] הטוקן שייך לחשבון: {login} | הרשאות: [{scopes}]", flush=True)
        if "gist" not in scopes:
            print("[GIST-CHECK] ❌ לטוקן אין הרשאת gist! צור טוקן classic חדש וסמן את התיבה gist", flush=True)
        else:
            print("[GIST-CHECK] ✅ הרשאת gist קיימת", flush=True)
    except Exception as e:
        print(f"[GIST-CHECK] בדיקה נכשלה: {e}", flush=True)

def default_data():
    return {
        "trades": [],
        "daily_stats": {},
        "signal_history": [],
        "pending": {},
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

def _merge_defaults(data):
    d = default_data()
    for k, v in d.items():
        if k not in data:
            data[k] = v
    for k in d["indicator_weights"]:
        if k not in data["indicator_weights"]:
            data["indicator_weights"][k] = 1.0
    return data

def load_data():
    """
    נקרא פעם אחת בהפעלה.
    סדר עדיפויות: Gist → /tmp → ברירת מחדל.
    """
    global _storage_source
    if gist_enabled():
        g = gist_load()
        if g is not None:
            _storage_source = "gist"
            print("[STORAGE] נטען מ-Gist", flush=True)
            return _merge_defaults(g)
        _storage_source = "gist_fail"
        print("[STORAGE] Gist נכשל — עובר ל-/tmp", flush=True)
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            if _storage_source != "gist_fail":
                _storage_source = "tmp"
            print("[STORAGE] נטען מ-/tmp", flush=True)
            return _merge_defaults(data)
    except Exception as e:
        print(f"[STORAGE] קריאת /tmp נכשלה: {e}", flush=True)
    if _storage_source not in ("gist_fail",):
        _storage_source = "default"
    print("[STORAGE] מתחיל מנתונים ריקים", flush=True)
    return default_data()

def prune_data(data):
    """מונע התנפחות: שומר 300 עסקאות אחרונות ו-90 ימי סטטיסטיקה."""
    trades = data.get("trades", [])
    if len(trades) > MAX_STORED_TRADES:
        open_trades = [t for t in trades if t.get("status") == "open"]
        closed = [t for t in trades if t.get("status") != "open"]
        keep = MAX_STORED_TRADES - len(open_trades)
        data["trades"] = closed[-keep:] + open_trades if keep > 0 else open_trades
    daily = data.get("daily_stats", {})
    if len(daily) > DAILY_STATS_KEEP_DAYS:
        for k in sorted(daily.keys())[:-DAILY_STATS_KEEP_DAYS]:
            del daily[k]

def save_data(data):
    """כותב ל-/tmp (גיבוי מקומי) ודוחף ל-Gist (אחסון קבוע)."""
    prune_data(data)
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"שגיאה בשמירה מקומית: {e}", flush=True)
    if gist_enabled():
        gist_save(data)

def make_trade_id(symbol_name, ts):
    raw = f"{symbol_name}_{ts}"
    return hashlib.md5(raw.encode()).hexdigest()[:8]

def fmt_tn(num):
    """תצוגת מספר עסקה — תומך גם בישן (13) וגם בחדש ('03/07 #2')."""
    return f"#{num}" if isinstance(num, int) else str(num)

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
            "apikey": TWELVEDATA_KEY,
            "timezone": "Asia/Jerusalem"
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"שגיאה ב-{symbol}: {data.get('message', 'לא ידוע')}", flush=True)
            return None
        closes = [float(v["close"]) for v in reversed(data["values"])]
        highs = [float(v["high"]) for v in reversed(data["values"])]
        lows = [float(v["low"]) for v in reversed(data["values"])]
        # זמן הנר האחרון — לזיהוי נתונים קפואים (שוק סגור/חג)
        last_time = None
        try:
            ts = data["values"][0]["datetime"]
            fmt = "%Y-%m-%d %H:%M:%S" if len(ts) > 10 else "%Y-%m-%d"
            last_time = datetime.datetime.strptime(ts, fmt)
            if IL_TZ:
                last_time = last_time.replace(tzinfo=IL_TZ)
        except Exception:
            pass
        return {"closes": closes, "highs": highs, "lows": lows, "last_time": last_time}
    except Exception as e:
        print(f"שגיאה בשליפת נתונים {symbol}: {e}", flush=True)
        return None

def get_trend_filter(symbol_code):
    """
    פילטר מגמה קשה: EMA50 על נרות שעה.
    מחזיר {"allowed": "long"/"short"/"none", "ema", "deviation_pct", "price"}.
    ממוטמן ל-30 דקות. None = כשל בנתונים → לא שולחים כלום (fail-safe).
    """
    now = now_il()
    cached = _trend_cache.get(symbol_code)
    if cached and (now - cached["time"]).total_seconds() < TREND_CACHE_MINUTES * 60:
        return cached["result"]
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol_code,
            "interval": TREND_INTERVAL,
            "outputsize": 200,
            "apikey": TWELVEDATA_KEY
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"[TREND] שגיאה: {data.get('message', 'לא ידוע')}", flush=True)
            return None
        closes = [float(v["close"]) for v in reversed(data["values"])]
        if len(closes) < TREND_EMA_PERIOD + 10:
            print(f"[TREND] לא מספיק נרות ({len(closes)})", flush=True)
            return None
        ema = calc_ema_series(closes, TREND_EMA_PERIOD)[-1]
        current = closes[-1]
        deviation = (current - ema) / ema
        if deviation > TREND_DEADZONE:
            allowed = "long"
        elif deviation < -TREND_DEADZONE:
            allowed = "short"
        else:
            allowed = "none"
        result = {
            "allowed": allowed,
            "ema": round(ema, 2),
            "deviation_pct": round(deviation * 100, 2),
            "price": round(current, 2)
        }
        _trend_cache[symbol_code] = {"time": now, "result": result}
        print(f"[TREND] {symbol_code}: {allowed} | מחיר {round(current,2)} | EMA50 {round(ema,2)} | {round(deviation*100,2):+.2f}%", flush=True)
        return result
    except Exception as e:
        print(f"[TREND] exception: {e}", flush=True)
        return None

# ============================================================
# אינדיקטורים
# ============================================================
def calc_ema_series(prices, period):
    """סדרת EMA מלאה (משמש גם ל-MACD וגם לפילטר המגמה)."""
    if not prices:
        return []
    k = 2 / (period + 1)
    out = [prices[0]]
    for p in prices[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return out

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
    """
    MACD תקין: קו MACD = EMA12 - EMA26, קו איתות = EMA9 של קו ה-MACD.
    (בגרסה הישנה קו האיתות חושב בטעות על המחירים הגולמיים.)
    """
    if len(prices) < 35:
        return None, None
    ema12 = calc_ema_series(prices, 12)
    ema26 = calc_ema_series(prices, 26)
    macd_series = [a - b for a, b in zip(ema12, ema26)]
    signal_series = calc_ema_series(macd_series, 9)
    return round(macd_series[-1], 4), round(signal_series[-1], 4)

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
    now = now_il()
    # שבת (5) וראשון (6) — שוק הזהב סגור (נפתח שני 01:00 שעון ישראל)
    if now.weekday() in (5, 6):
        return False
    hours = TRADING_HOURS.get(symbol_name, {"start": 8, "end": 22})
    return hours["start"] <= now.hour < hours["end"]

def get_today_key():
    return now_il().strftime("%Y-%m-%d")

def consecutive_loss_block(data):
    """Circuit breaker: 3 הפסדים רצופים (לפי זמן סגירה, היום) → עצירה עד מחר."""
    today = get_today_key()
    closed_today = [
        t for t in data["trades"]
        if t.get("status") == "closed" and t.get("result")
        and t.get("close_time", t.get("entry_time", "")).startswith(today)
    ]
    if len(closed_today) < CONSECUTIVE_LOSS_LIMIT:
        return False
    closed_today.sort(key=lambda t: t.get("close_time", t.get("entry_time", "")))
    return all(t.get("result") == "loss" for t in closed_today[-CONSECUTIVE_LOSS_LIMIT:])

def can_trade(symbol_name, data):
    today = get_today_key()
    daily = data["daily_stats"].get(today, {})

    entered = daily.get("entered", 0)
    signals_sent = daily.get("signals_sent", 0)

    if entered >= MAX_ENTERED_PER_DAY:
        return False, f"הגעת ל-{MAX_ENTERED_PER_DAY} עסקאות היום"
    if signals_sent >= MAX_SIGNALS_PER_DAY:
        return False, f"נשלחו {MAX_SIGNALS_PER_DAY} איתותים היום (תקרה)"

    open_trades = [t for t in data["trades"] if t["status"] == "open"]
    if len(open_trades) >= MAX_PARALLEL_TRADES:
        return False, "2 עסקאות פתוחות כבר"

    if consecutive_loss_block(data):
        return False, f"{CONSECUTIVE_LOSS_LIMIT} הפסדים ברצף — עצירה עד מחר"

    if DAILY_LOSS_LIMIT is not None:
        daily_pnl = daily.get("pnl", 0)
        if daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, "הגעת למגבלת הפסד יומית"

    now = now_il()
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
# ניתוח ושליחת סיגנל — trend-following עם פילטר מגמה קשה
# ============================================================
def analyze_and_signal(symbol_name, symbol_code, data):
    if not is_trading_hours(symbol_name):
        print(f"[{symbol_name}] לא בשעות מסחר", flush=True)
        return

    # 3.3: חיתוך ערב — אין איתותים חדשים אחרי LAST_ENTRY_HOUR (מוניטור ממשיך)
    if now_il().hour >= LAST_ENTRY_HOUR:
        print(f"[{symbol_name}] אחרי {LAST_ENTRY_HOUR}:00 — אין איתותים חדשים (חיתוך ערב 3.3)", flush=True)
        return

    can, reason = can_trade(symbol_name, data)
    if not can:
        print(f"[{symbol_name}] לא שולח: {reason}", flush=True)
        return

    prices_data = get_prices(symbol_code)
    if not prices_data:
        return

    # הגנת טריות: נר אחרון ישן מ-45 דקות = שוק סגור/קפוא (חג, תקלה) → לא סוחרים
    last_t = prices_data.get("last_time")
    if last_t:
        age_min = (now_il() - last_t).total_seconds() / 60
        if age_min > 45:
            print(f"[{symbol_name}] נתונים קפואים ({int(age_min)} דק' מהנר האחרון) — שוק סגור? מדלג", flush=True)
            return

    closes = prices_data["closes"]
    highs = prices_data["highs"]
    lows = prices_data["lows"]
    current = closes[-1]

    # --- אינדיקטורים ---
    rsi = calc_rsi(closes)
    macd_line, macd_signal = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    atr = calc_atr(highs, lows, closes)
    adx = calc_adx(highs, lows, closes)
    breakout_dir = check_breakout(closes, highs, lows)

    # --- פילטר מגמה קשה: EMA50 על 1h קובע איזה כיוון בכלל מותר ---
    trend = get_trend_filter(symbol_code)
    if not trend:
        print(f"[{symbol_name}] מדלג: אין נתוני מגמה (fail-safe)", flush=True)
        return
    if trend["allowed"] == "none":
        print(f"[{symbol_name}] מדלג: דשדוש ({trend['deviation_pct']:+.2f}% מ-EMA50)", flush=True)
        return
    # תקרת מתיחה: המחיר רחוק מדי מה-EMA = מאוחר מדי להצטרף למגמה
    if MAX_STRETCH_PCT is not None and abs(trend["deviation_pct"]) > MAX_STRETCH_PCT:
        print(f"[{symbol_name}] מדלג: מתוח מדי ({trend['deviation_pct']:+.2f}% מ-EMA50, תקרה {MAX_STRETCH_PCT}%)", flush=True)
        return

    direction = "קנייה" if trend["allowed"] == "long" else "מכירה"
    is_long = (direction == "קנייה")

    # --- שער ADX: בלי מגמה חזקה מספיק, לא נכנסים ---
    if adx is not None and adx < ADX_MIN:
        print(f"[{symbol_name}] מדלג: ADX {adx} < {ADX_MIN} (מגמה חלשה)", flush=True)
        return

    # 3.3: חסימת RSI קיצוני — מומנטום מוצה, מאוחר מדי להצטרף (backtest 11/07: ‏+179 מול הבסיס)
    # זהה לוריאנט 7 במנוע ה-backtest: לונג נחסם ב-RSI>=75, שורט נחסם ב-RSI<=25
    if rsi is not None:
        if is_long and rsi >= 75:
            print(f"[{symbol_name}] מדלג: RSI {rsi} >= 75 — קיצון, חסימה קשה (3.3)", flush=True)
            return
        if (not is_long) and rsi <= 25:
            print(f"[{symbol_name}] מדלג: RSI {rsi} <= 25 — קיצון, חסימה קשה (3.3)", flush=True)
            return

    weights = data["indicator_weights"]
    signals = []
    score = 0.0

    # --- MACD: מומנטום בכיוון המגמה ---
    if macd_line is not None and macd_signal is not None:
        if is_long and macd_line > macd_signal:
            signals.append(f"📈 MACD תומך ({macd_line})")
            score += 1 * weights["macd"]
        elif (not is_long) and macd_line < macd_signal:
            signals.append(f"📉 MACD תומך ({macd_line})")
            score += 1 * weights["macd"]

    # --- פריצה בכיוון המגמה ---
    if breakout_dir == "למעלה" and is_long:
        signals.append("💥 פריצה למעלה")
        score += 1 * weights["breakout"]
    elif breakout_dir == "למטה" and not is_long:
        signals.append("💥 פריצה למטה")
        score += 1 * weights["breakout"]

    # --- RSI: כניסה על תיקון בתוך המגמה, לא על קיצון ---
    if rsi is not None:
        if is_long:
            if 40 <= rsi <= 65:
                signals.append(f"🟢 RSI {rsi} (תיקון בריא)")
                score += 1 * weights["rsi"]
            elif rsi >= 75:
                signals.append(f"⚠️ RSI {rsi} (מתוח מדי)")
                score -= 0.5
        else:
            if 35 <= rsi <= 60:
                signals.append(f"🔴 RSI {rsi} (תיקון בריא)")
                score += 1 * weights["rsi"]
            elif rsi <= 25:
                signals.append(f"⚠️ RSI {rsi} (מתוח מדי)")
                score -= 0.5

    # --- Bollinger: מיקום מול הרצועות ---
    if bb_upper and bb_mid and bb_lower:
        if is_long:
            if current <= bb_mid:
                signals.append("📊 מתחת לאמצע הרצועה (תיקון)")
                score += 1 * weights["bollinger"]
            elif current >= bb_upper:
                signals.append("⚠️ נגע ברצועה עליונה (מתוח)")
                score -= 0.5
        else:
            if current >= bb_mid:
                signals.append("📊 מעל אמצע הרצועה (תיקון)")
                score += 1 * weights["bollinger"]
            elif current <= bb_lower:
                signals.append("⚠️ נגע ברצועה תחתונה (מתוח)")
                score -= 0.5

    # --- ADX: עוצמת מגמה מחזקת ---
    if adx is not None and adx >= 25:
        signals.append(f"💪 ADX {adx} (מגמה חזקה)")
        score += 1 * weights["adx"]

    stars = min(5, max(1, round(score)))
    star_display = "⭐" * stars

    # דורש לפחות 2 אינדיקטורים תומכים (לא אזהרות) — קונפלואנס אמיתי
    supporting = [s for s in signals if not s.startswith("⚠️")]
    if stars < 2 or len(supporting) < 2:
        print(f"[{symbol_name}] ציון {stars}, {len(supporting)} תומכים — לא מספיק ({direction})", flush=True)
        return

    open_trade = check_open_trades_for_symbol(symbol_name, data)
    reversal_warning = ""
    if open_trade and open_trade["direction"] != direction:
        reversal_warning = f"\n⚠️ <b>היפוך כיוון!</b> יש עסקה פתוחה ב{open_trade['direction']}\n"

    if atr:
        stop_distance = atr * 1.5
    else:
        stop_distance = current * 0.005
    # רצפת סטופ: מינימום 0.35% מהמחיר, שהסטופ לא יישב בתוך הרעש
    if STOP_FLOOR_PCT is not None:
        stop_distance = max(stop_distance, current * STOP_FLOOR_PCT / 100)

    entry_price = round(current, 2)
    tp2_mult = 4 if stars >= 4 else 3

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

    now = now_il()
    timeout_time = (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).strftime("%H:%M")

    # מספור יומי: מתאפס כל יום, התווית כוללת תאריך (למשל '03/07 #2')
    data["trade_counter"] = data.get("trade_counter", 0) + 1  # מונה כללי פנימי
    today = get_today_key()
    if today not in data["daily_stats"]:
        data["daily_stats"][today] = {}
    data["daily_stats"][today]["trade_seq"] = data["daily_stats"][today].get("trade_seq", 0) + 1
    trade_num = f"{now.strftime('%d/%m')} #{data['daily_stats'][today]['trade_seq']}"

    # שמור pending בתוך הנתונים (שורד deploy)
    trade_id = make_trade_id(symbol_name, now.strftime('%H%M%S'))
    pending = data.setdefault("pending", {})
    pending[trade_id] = {
        "number": trade_num,
        "symbol": symbol_name,
        "direction": direction,
        "entry": entry_price,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "stars": stars,
        "time": now.isoformat()
    }
    cutoff = (now - datetime.timedelta(hours=1)).isoformat()
    data["pending"] = {k: v for k, v in pending.items() if v["time"] > cutoff}

    trend_line = (
        f"📈 מגמה (EMA50 1h): "
        f"{'עלייה 🟢' if is_long else 'ירידה 🔴'} "
        f"({trend['deviation_pct']:+.2f}%)\n"
    )

    msg = (
        f"🚨 <b>איתות סחר {trade_num} — {symbol_name}</b>\n"
        f"🕐 {now.strftime('%H:%M')} | {star_display} {stars}/5\n"
        f"{reversal_warning}"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 כיוון: <b>{'קנייה 🟢' if is_long else 'מכירה 🔴'}</b>\n"
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
    print(f"[{now.strftime('%H:%M')}] ✅ איתות {trade_num}: {symbol_name} {direction} {stars}⭐", flush=True)

# ============================================================
# בדיקת עבר (Backtest) — פקודת /backtest בטלגרם
# מריץ את הלוגיקה החיה על נתוני 30 הימים האחרונים ומדווח מה היה קורה.
# לא נוגע בנתונים האמיתיים — קריאה וחישוב בלבד.
# ============================================================
def _fetch_history(symbol, interval, outputsize):
    """מושך נרות היסטוריים בשעון ישראל, ממוינים מהישן לחדש."""
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVEDATA_KEY,
            "timezone": "Asia/Jerusalem"
        }
        r = requests.get(url, params=params, timeout=30)
        d = r.json()
        if "values" not in d:
            print(f"[BACKTEST] שגיאה בנתונים: {d.get('message','לא ידוע')}", flush=True)
            return None
        out = []
        for v in d["values"]:
            ts = v["datetime"]
            fmt = "%Y-%m-%d %H:%M:%S" if len(ts) > 10 else "%Y-%m-%d"
            out.append({
                "t": datetime.datetime.strptime(ts, fmt),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"])
            })
        out.sort(key=lambda x: x["t"])
        return out
    except Exception as e:
        print(f"[BACKTEST] exception: {e}", flush=True)
        return None

def _simulate(m15, h1, stop_floor_pct=None, max_stretch_pct=None,
              deadzone=None, rsi_extreme_block=False,
              limit_entry_pct=None, last_entry_hour=None,
              target_mult=2.0, breakeven_frac=None):
    """
    מדמה את הלוגיקה החיה על נתוני העבר.
    stop_floor_pct: רצפת סטופ באחוזים (למשל 0.35).
    max_stretch_pct: תקרת מתיחה מה-EMA (למשל 1.2).
    deadzone: דדזון מגמה בשבר עשרוני (ברירת מחדל TREND_DEADZONE=0.003). וריאנט 1.
    rsi_extreme_block: True = ‏RSI<=25 חוסם שורט, RSI>=75 חוסם לונג (חסימה קשה במקום קנס). וריאנט 7.
    limit_entry_pct: כניסה בהמתנה — לימיט במרחק X% מהמחיר לכיוון הסטופ (למשל 0.15).
                     הלימיט תקף LIMIT_EXPIRY_CANDLES נרות; לא מולא — אין עסקה. וריאנט 5.
    last_entry_hour: אין כניסות חדשות משעה זו (למשל 19). המעקב על פתוחות נמשך. וריאנט 6.
    target_mult: טארגט כמכפלת מרחק הסטופ (ברירת מחדל 2.0 כמו בחי). חקר אחוז זכייה.
    breakeven_frac: אחרי שהמחיר עבר חלק זה מהדרך לטארגט (למשל 0.5) — הסטופ זז לכניסה. וריאנט 4.
    """
    LIMIT_EXPIRY_CANDLES = 4  # לימיט חי שעה (4 נרות 15 דק')
    if deadzone is None:
        deadzone = TREND_DEADZONE
    h_closes = [c["c"] for c in h1]
    h_ema = calc_ema_series(h_closes, TREND_EMA_PERIOD)
    h_times = [c["t"] for c in h1]

    closes = [c["c"] for c in m15]
    highs = [c["h"] for c in m15]
    lows = [c["l"] for c in m15]
    times = [c["t"] for c in m15]

    open_trades = []
    pending_limits = []  # וריאנט 5: הזמנות לימיט שממתינות למילוי
    expired_limits = 0   # וריאנט 5: איתותים שהלימיט שלהם פקע בלי מילוי
    closed = []          # {"result","pnl","stars","day"}
    last_signal_time = None
    daily_signals = {}
    j = 0  # מצביע על נרות השעה

    for i in range(50, len(m15)):
        t = times[i]
        day = t.strftime("%Y-%m-%d")

        # --- וריאנט 5: בדיקת מילוי/פקיעה של לימיטים ממתינים ---
        if pending_limits:
            still_pending = []
            for p in pending_limits:
                if i - p["signal_i"] > LIMIT_EXPIRY_CANDLES:
                    expired_limits += 1
                    continue  # פקע בלי מילוי — אין עסקה
                p_long = p["dir"] == "long"
                filled = (lows[i] <= p["limit"]) if p_long else (highs[i] >= p["limit"])
                if not filled:
                    still_pending.append(p)
                    continue
                entry = p["limit"]
                stop_distance = p["stop_distance"]
                stop = entry - stop_distance if p_long else entry + stop_distance
                target = entry + stop_distance * target_mult if p_long else entry - stop_distance * target_mult
                # שמרני: אם נר המילוי נגע גם בסטופ — נספר כהפסד מיידי
                stop_same = (lows[i] <= stop) if p_long else (highs[i] >= stop)
                if stop_same:
                    closed.append({"result": "loss",
                                   "pnl": -(points_to_ils(stop_distance) + SPREAD_COST_ILS),
                                   "stars": p["stars"], "day": day})
                else:
                    open_trades.append({"dir": p["dir"], "entry": entry, "stop": stop,
                                        "target": target, "time": t, "stars": p["stars"]})
            pending_limits = still_pending

        # --- סגירת עסקאות פתוחות מול הנר הנוכחי (סטופ קודם) ---
        still_open = []
        for tr in open_trades:
            is_long = tr["dir"] == "long"
            stop_hit = (lows[i] <= tr["stop"]) if is_long else (highs[i] >= tr["stop"])
            target_hit = (highs[i] >= tr["target"]) if is_long else (lows[i] <= tr["target"])
            timed_out = (t - tr["time"]).total_seconds() >= TRADE_TIMEOUT_HOURS * 3600
            if stop_hit:
                if tr.get("be"):
                    # וריאנט 4: הסטופ כבר הוזז לכניסה — יציאה באפס פחות ספרד
                    closed.append({"result": "be", "pnl": -SPREAD_COST_ILS,
                                   "stars": tr["stars"], "day": day})
                else:
                    d_ = abs(tr["entry"] - tr["stop"])
                    closed.append({"result": "loss", "pnl": -(points_to_ils(d_) + SPREAD_COST_ILS),
                                   "stars": tr["stars"], "day": day})
            elif target_hit:
                d_ = abs(tr["target"] - tr["entry"])
                closed.append({"result": "win", "pnl": points_to_ils(d_) - SPREAD_COST_ILS,
                               "stars": tr["stars"], "day": day})
            elif timed_out:
                diff = (closes[i] - tr["entry"]) if is_long else (tr["entry"] - closes[i])
                pnl = points_to_ils(diff) - SPREAD_COST_ILS if diff > 0 else -(points_to_ils(abs(diff)) + SPREAD_COST_ILS)
                closed.append({"result": "timeout", "pnl": pnl, "stars": tr["stars"], "day": day})
            else:
                # וריאנט 4: break-even — שמרני: הטריגר מהנר הנוכחי נכנס לתוקף מהנר הבא
                if breakeven_frac is not None and not tr.get("be"):
                    trigger = tr["entry"] + (tr["target"] - tr["entry"]) * breakeven_frac
                    reached = (highs[i] >= trigger) if is_long else (lows[i] <= trigger)
                    if reached:
                        tr["stop"] = tr["entry"]
                        tr["be"] = True
                still_open.append(tr)
        open_trades = still_open

        # --- תנאי כניסה (זהים לחיים) ---
        if not (8 <= t.hour < 22):
            continue
        if last_entry_hour is not None and t.hour >= last_entry_hour:
            continue  # וריאנט 6: אין כניסות חדשות בערב
        if daily_signals.get(day, 0) >= MAX_ENTERED_PER_DAY:
            continue
        if len(open_trades) >= MAX_PARALLEL_TRADES:
            continue
        if last_signal_time and (t - last_signal_time).total_seconds() < SIGNAL_COOLDOWN_MINUTES * 60:
            continue
        # circuit breaker: 3 הפסדים רצופים היום
        today_closed = [c for c in closed if c["day"] == day and c["result"] in ("win", "loss")]
        if len(today_closed) >= CONSECUTIVE_LOSS_LIMIT and \
           all(c["result"] == "loss" for c in today_closed[-CONSECUTIVE_LOSS_LIMIT:]):
            continue

        # --- מגמה מנר השעה האחרון שהושלם ---
        while j + 1 < len(h_times) and h_times[j + 1] <= t:
            j += 1
        if j < TREND_EMA_PERIOD + 10:
            continue
        ema = h_ema[j]
        current = closes[i]
        dev = (current - ema) / ema
        if dev > deadzone:
            direction = "long"
        elif dev < -deadzone:
            direction = "short"
        else:
            continue
        if max_stretch_pct is not None and abs(dev) * 100 > max_stretch_pct:
            continue
        is_long = direction == "long"

        # --- אינדיקטורים על חלון 50 נרות (כמו בחי) ---
        w_c = closes[i - 49:i + 1]
        w_h = highs[i - 49:i + 1]
        w_l = lows[i - 49:i + 1]

        adx = calc_adx(w_h, w_l, w_c)
        if adx is not None and adx < ADX_MIN:
            continue

        rsi = calc_rsi(w_c)
        # וריאנט 7: RSI קיצוני חוסם כניסה לגמרי (במקום קנס כוכב)
        if rsi_extreme_block and rsi is not None:
            if is_long and rsi >= 75:
                continue
            if (not is_long) and rsi <= 25:
                continue
        macd_line, macd_sig = calc_macd(w_c)
        bb_up, bb_mid, bb_lo = calc_bollinger(w_c)
        atr = calc_atr(w_h, w_l, w_c)
        brk = check_breakout(w_c, w_h, w_l)

        score = 0.0
        supporting = 0
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
        if stars < 2 or supporting < 2:
            continue

        # --- פתיחת עסקה מדומה ---
        stop_distance = atr * 1.5 if atr else current * 0.005
        if stop_floor_pct is not None:
            stop_distance = max(stop_distance, current * stop_floor_pct / 100)

        if limit_entry_pct is not None:
            # וריאנט 5: במקום כניסת שוק — לימיט לכיוון הסטופ. לא מולא = אין עסקה
            limit = current - current * limit_entry_pct / 100 if is_long \
                else current + current * limit_entry_pct / 100
            pending_limits.append({"dir": direction, "limit": limit,
                                   "stop_distance": stop_distance,
                                   "stars": stars, "signal_i": i})
        else:
            stop = current - stop_distance if is_long else current + stop_distance
            target = current + stop_distance * target_mult if is_long else current - stop_distance * target_mult
            open_trades.append({"dir": direction, "entry": current, "stop": stop,
                                "target": target, "time": t, "stars": stars})
        daily_signals[day] = daily_signals.get(day, 0) + 1
        last_signal_time = t

    wins = [c for c in closed if c["result"] == "win"]
    losses = [c for c in closed if c["result"] == "loss"]
    touts = [c for c in closed if c["result"] == "timeout"]
    bes = [c for c in closed if c["result"] == "be"]
    total_pnl = round(sum(c["pnl"] for c in closed), 2)
    decided = len(wins) + len(losses)
    win_rate = f"{round(len(wins) / decided * 100)}%" if decided else "—"
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "timeouts": len(touts), "win_rate": win_rate, "pnl": total_pnl,
        "unfilled": expired_limits, "be": len(bes)
    }

def run_backtest():
    """מריץ בדיקת עבר ומחזיר דוח טקסט לטלגרם."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)   # ~30 ימי מסחר
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return "⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."

    date_from = m15[0]["t"].strftime("%d/%m")
    date_to = m15[-1]["t"].strftime("%d/%m")

    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR)

    variants = [
        ("⚙️ בסיס — הלוגיקה החיה (3.3)", {}),
        ("📉 גרסה 3.2 הישנה (השוואה)",
         {"rsi_extreme_block": False, "last_entry_hour": None}),
        ("🎯 טארגט 1.5× (במקום 2×)",      {"target_mult": 1.5}),
        ("🎯 טארגט 1.0× (יחס 1:1)",       {"target_mult": 1.0}),
        ("4️⃣ Break-even אחרי 50% מהדרך",  {"breakeven_frac": 0.5}),
        ("🎯+4️⃣ טארגט 1.5× + Break-even 50%",
         {"target_mult": 1.5, "breakeven_frac": 0.5}),
    ]

    def block(name, r, base_pnl=None):
        diff = ""
        if base_pnl is not None:
            diff = f" ({r['pnl'] - base_pnl:+.0f} מול הבסיס)"
        extra = ""
        if r.get("unfilled"):
            extra += f" | 🚫 לא מולאו: {r['unfilled']}"
        if r.get("be"):
            extra += f" | 🤝 יצאו באפס: {r['be']}"
        return (
            f"<b>{name}</b>\n"
            f"🔔 {r['trades']} עסק' | ✅ {r['wins']} | ❌ {r['losses']}"
            + (f" | ⏰ {r['timeouts']}" if r['timeouts'] else "") + extra + "\n"
            f"📊 {r['win_rate']} | 💰 {r['pnl']:+.2f} ש\"ח{diff}\n"
        )

    parts = [f"📊 <b>בדיקת עבר — {date_from} עד {date_to}</b>\n"
             f"(חקר אחוז זכייה: וריאנט אחד משתנה בכל שורה מול לוגיקת 3.3)\n"]
    base_pnl = None
    for name, overrides in variants:
        kw = dict(live); kw.update(overrides)
        r = _simulate(m15, h1, **kw)
        parts.append(block(name, r, base_pnl))
        if base_pnl is None:
            base_pnl = r["pnl"]
    parts.append("💡 אחוז = זכיות מתוך זכיות+הפסדים | ⏰ = תום 6 שעות | 🤝 = סטופ שהוזז לכניסה")
    return "\n".join(parts)

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

            pending = data.get("pending", {})

            # ✅ נכנסתי לעסקה
            if cbd.startswith("en_"):
                trade_id = cbd[3:]
                signal = pending.get(trade_id)
                if not signal:
                    send_telegram("⚠️ הסיגנל פג תוקף")
                    continue
                if any(t["id"] == trade_id for t in data["trades"]):
                    send_telegram("⚠️ כבר נכנסת לעסקה הזו")
                    continue
                now = now_il()
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
                    "stars": signal.get("stars"),
                    "entry_time": now.isoformat(),
                    "timeout": timeout,
                    "status": "open",
                    "target_alerted": False,
                    "stop_alerted": False,
                    "timeout_sent": False
                }
                data["trades"].append(trade)
                pending.pop(trade_id, None)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["entered"] = data["daily_stats"][today].get("entered", 0) + 1
                save_data(data)
                keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade_id}"}]]
                send_telegram(
                    f"✅ <b>עסקה {fmt_tn(num)} נפתחה — {signal['symbol']}</b>\n"
                    f"כיוון: {'קנייה 🟢' if signal['direction'] == 'קנייה' else 'מכירה 🔴'}\n"
                    f"כניסה: {signal['entry']} | סטופ: {signal['stop']}\n"
                    f"🎯 טארגט 1: {signal['target1']}\n"
                    f"⏰ תזכורת: {datetime.datetime.fromisoformat(timeout).strftime('%H:%M')}",
                    keyboard
                )
                print(f"[CALLBACK] ✅ עסקה {fmt_tn(num)} נפתחה: {trade_id}", flush=True)

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
                send_telegram(f"📊 <b>עסקה {fmt_tn(trade.get('number','?'))} — איך יצאת?</b>", keyboard)
                print(f"[CALLBACK] 🔒 סגירה: {trade_id}", flush=True)

            # תוצאה — רווח (טארגט 1 סוגר הכל)
            elif cbd.startswith("rf_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                risk_distance = abs(trade["entry"] - trade["stop"])
                reward_distance = abs(trade["target1"] - trade["entry"])
                r_multiple = (reward_distance / risk_distance) if risk_distance else 0
                pnl = round(points_to_ils(reward_distance) - SPREAD_COST_ILS, 2)
                trade["status"] = "closed"
                trade["result"] = "win"
                trade["pnl"] = pnl
                trade["close_time"] = now_il().isoformat()
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) + pnl, 2)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = round(data["daily_stats"][today].get("pnl", 0) + pnl, 2)
                save_data(data)
                send_telegram(f"🎉 <b>רווח! עסקה {fmt_tn(trade.get('number','?'))}</b>\n💰 +{pnl} ש\"ח (יחס 1:{round(r_multiple, 1)})")
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
                risk_distance = abs(trade["entry"] - trade["stop"])
                loss = round(points_to_ils(risk_distance) + SPREAD_COST_ILS, 2)
                trade["status"] = "closed"
                trade["result"] = "loss"
                trade["pnl"] = -loss
                trade["close_time"] = now_il().isoformat()
                data["all_time_stats"]["losses"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) - loss, 2)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["pnl"] = round(data["daily_stats"][today].get("pnl", 0) - loss, 2)
                save_data(data)
                send_telegram(f"📉 <b>הפסד — עסקה {fmt_tn(trade.get('number','?'))}</b>\n💸 -{loss} ש\"ח")
                update_indicator_weights(data)

        elif "message" in update:
            msg = update["message"]
            text = msg.get("text", "").strip()

            # פקודת בדיקת עבר
            if text.lower() in ("/backtest", "backtest", "בדיקה"):
                send_telegram("⏳ מריץ בדיקת עבר על ~30 ימים... (עד דקה)")
                try:
                    send_telegram(run_backtest())
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            waiting_trade = next((t for t in data["trades"] if t.get("waiting_early_exit") and t["status"] == "open"), None)
            if waiting_trade and text.replace(".", "").replace("-", "").isdigit():
                amount = float(text)
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
                waiting_trade["close_time"] = now_il().isoformat()
                waiting_trade.pop("waiting_early_exit", None)
                data["all_time_stats"]["wins"] += 1
                data["all_time_stats"]["early_exits"] += 1
                data["all_time_stats"]["total_trades"] += 1
                data["all_time_stats"]["total_pnl"] = round(data["all_time_stats"].get("total_pnl", 0) + amount, 2)
                send_telegram(
                    f"✅ <b>יציאה מוקדמת — עסקה {fmt_tn(waiting_trade.get('number','?'))}</b>\n"
                    f"💰 {amount} ש\"ח\n"
                    f"📊 יעילות: {efficiency}%"
                    + ("\n💡 השארת כסף — שקול לתת לרוץ יותר" if efficiency < 60 else "")
                )
                save_data(data)

    return last_update_id

# ============================================================
# מעקב עסקאות פתוחות + זיהוי אוטומטי (high/low)
# ============================================================
def monitor_open_trades(data):
    now = now_il()
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

        target_hit = (high >= target1) if direction == "קנייה" else (low <= target1)
        stop_hit = (low <= stop) if direction == "קנייה" else (high >= stop)

        # עדיפות לסטופ (שמרני) במקרה ששניהם נגעו באותו נר
        if stop_hit and not trade.get("stop_alerted"):
            trade["stop_alerted"] = True
            changed = True
            keyboard = [[{"text": "🛑 סגור בהפסד", "callback_data": f"rl_{trade['id']}"}]]
            send_telegram(
                f"🛑 <b>עסקה {fmt_tn(num)} — נגעת בסטופ!</b>\n"
                f"{symbol_name} | מחיר: {current}\n"
                f"סטופ: {stop}\n"
                f"אשר סגירה בהפסד 👇",
                keyboard
            )
            print(f"[AUTO] 🛑 סטופ עסקה {fmt_tn(num)}", flush=True)

        elif target_hit and not trade.get("target_alerted"):
            trade["target_alerted"] = True
            changed = True
            keyboard = [[{"text": "✅ סגור ברווח", "callback_data": f"rf_{trade['id']}"}]]
            send_telegram(
                f"🎯 <b>עסקה {fmt_tn(num)} — נגעת בטארגט 1!</b>\n"
                f"{symbol_name} | מחיר: {current}\n"
                f"טארגט: {target1}\n"
                f"אשר סגירה ברווח 👇",
                keyboard
            )
            print(f"[AUTO] 🎯 טארגט עסקה {fmt_tn(num)}", flush=True)

        # תזכורת timeout
        timeout = datetime.datetime.fromisoformat(trade["timeout"])
        try:
            expired = now >= timeout
        except TypeError:
            expired = True  # ערבוב aware/naive מנתונים ישנים — שלח תזכורת ליתר ביטחון
        if expired and not trade.get("timeout_sent"):
            trade["timeout_sent"] = True
            changed = True
            keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade['id']}"}]]
            send_telegram(
                f"⏰ <b>תזכורת — עסקה {fmt_tn(num)}</b>\n"
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
    storage_note = "" if _storage_source == "gist" else "\n⚠️ אחסון זמני בלבד — הנתונים לא ב-Gist!"
    send_telegram(
        f"📊 <b>דוח יומי — {today}</b>\n\n"
        f"🔔 איתותים שנשלחו: {signals_today}\n"
        f"✅ עסקאות שנכנסת: {entered_today}\n"
        f"💰 רווח/הפסד היום: {round(pnl_today, 2)} ש\"ח\n\n"
        f"📈 סה\"כ עסקאות: {stats['total_trades']}\n"
        f"✅ רווחים: {stats['wins']} | ❌ הפסדים: {stats['losses']}\n"
        f"📊 אחוז הצלחה: {win_rate}%\n"
        f"🏦 רווח/הפסד מצטבר: {total_pnl} ש\"ח"
        f"{storage_note}"
    )

# ============================================================
# לולאה ראשית
# ============================================================
def main():
    print("🤖 בוט מסחר מופעל! [גרסה 3.3 — חסימת RSI קיצוני + חיתוך ערב 19:00]", flush=True)
    print(f"TOKEN exists: {bool(TELEGRAM_TOKEN)}", flush=True)
    print(f"CHAT_ID: {CHAT_ID}", flush=True)
    print(f"GIST configured: {gist_enabled()}", flush=True)
    gist_diagnose()

    data = load_data()

    if _storage_source == "gist":
        storage_line = "💾 אחסון קבוע: GitHub Gist ✅"
    elif _storage_source == "gist_fail":
        storage_line = "🚨 Gist מוגדר אבל נכשל! בדוק GIST_ID/GIST_TOKEN ב-Render"
    elif gist_enabled():
        storage_line = "💾 אחסון קבוע: GitHub Gist ✅"
    else:
        storage_line = "⚠️ אחסון זמני בלבד (/tmp) — הגדר GIST_ID + GIST_TOKEN ב-Render"

    send_telegram(
        "🤖 <b>בוט המסחר הופעל!</b> (גרסה 3.3)\n\n"
        "📊 סורק: זהב (XAU/USD)\n"
        "⏰ כל 10 דקות | 🕐 08:00—22:00 (ישראל)\n\n"
        "🧭 <b>מסחר עם המגמה בלבד</b>\n"
        "פילטר EMA50 (1h) קובע כיוון — נגד המגמה נחסם\n"
        "🛑 עצירה אוטומטית אחרי 3 הפסדים ברצף\n"
        "💡 שלח /backtest לבדיקת הלוגיקה על 30 ימים אחורה\n"
        f"{storage_line}"
    )

    last_update_id = 0
    last_daily_report = ""
    last_morning_ping = ""
    scan_count = 0

    SCAN_INTERVAL = 600   # 10 דקות בין סריקות שוק
    POLL_INTERVAL = 2     # תדירות בדיקת כפתורים (שניות)
    last_scan_time = 0

    while True:
        try:
            now = now_il()

            # --- בדיקת כפתורים (תכופה → תגובה מיידית) ---
            last_update_id = handle_callbacks(data, last_update_id)

            # --- סריקת שוק + מעקב עסקאות: כל 10 דקות ---
            if time.time() - last_scan_time >= SCAN_INTERVAL:
                last_scan_time = time.time()
                scan_count += 1
                print(f"\n--- סריקה #{scan_count} {now.strftime('%H:%M:%S')} ---", flush=True)

                for name, code in SYMBOLS.items():
                    try:
                        analyze_and_signal(name, code, data)
                        time.sleep(3)
                    except Exception as e:
                        print(f"שגיאה ב{name}: {e}", flush=True)

                monitor_open_trades(data)

                # ניסיון חוזר לדחיפה ל-Gist אם שמירה קודמת נכשלה
                if _gist_dirty and gist_enabled():
                    print("[GIST] מנסה שוב לדחוף נתונים...", flush=True)
                    gist_save(data)

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
