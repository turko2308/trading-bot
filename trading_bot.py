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

_runtime = {"last_scan": None}  # 3.4: מצב ריצה ל-/status (לא נשמר ב-Gist)

def now_il():
    """שעון ישראל תמיד — לא תלוי באזור הזמן של השרת (Render = UTC)."""
    if IL_TZ:
        return datetime.datetime.now(IL_TZ)
    return datetime.datetime.now()

# ============================================================
# הגדרות
# ============================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TWELVEDATA_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")

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
# 3.6.2: חלון הפעילות הורחב ל-06:00-22:00 לבקשת המשתמש.
# הערה מהנתונים (Dukascopy, 2.5 שנים): חלון 6-22 יצא ‏1,392- מתחת
# לחלון 8-22 על אותם נתונים. ההרחבה נועדה לכסות את שעות המסחר
# הידני של הבוקר (06:00-07:00), לא כשיפור צפוי לתוצאה.
TRADING_HOURS = {
    "זהב": {"start": 6, "end": 22}
}
# 3.3: אין איתותים חדשים משעה זו (ערב = כניסות מפסידות; backtest 11/07: ‏+203 מול הבסיס).
# המוניטור על עסקאות פתוחות ממשיך כרגיל 24/7.
LAST_ENTRY_HOUR = 19
# 3.4: תזכורת חוזרת על סגירה שלא אושרה — כל 30 דק', עד 5 פעמים
REMINDER_MINUTES = 30
MAX_REMINDERS = 5

# ===== מגבלות איתותים =====
MAX_ENTERED_PER_DAY = 10
MAX_SIGNALS_PER_DAY = 20
MAX_PARALLEL_TRADES = 2
SIGNAL_COOLDOWN_MINUTES = 30
TRADE_TIMEOUT_HOURS = 6

# 3.6.4: יעד/סטופ קבועים בדולרים, לפי סגנון המסחר של המשתמש
# (כניסה ויציאה מהירה, 2-3 עסקאות, סוגר באותו יום).
# None = חזרה לחישוב הישן לפי ATR (טארגט = פי 2 מהסטופ).
# ⚠️ מהנתונים (2.5 שנים, 1.5 אונקיות): 10$/10$ = 51% הצלחה,
# ‏2.54- ש"ח לעסקה. זה התא הטוב ביותר בטווח שנבדק — לא תא רווחי.
# (‏8$: 45%, ‏2.90-  |  12$: 56%, ‏2.92-  |  16$: 66%, ‏2.08-)
FIXED_TARGET_USD = 10.0
FIXED_STOP_USD = 10.0

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
POSITION_SIZE_OZ = 1.5   # 3.6.5: תוקן — איזק סוחר 1.5 אונקיות
# 3.9.3: השער נמדד מ-**רווח/הפסד שנסגר**, לא משווי פוזיציה מוצג.
# מסך Closed Position, פוזיציה 1879518625:
#   1.5oz · 4200.13 → 4390.74 · שווי 6,300.20$ → 6,586.11$ (הפרש 285.91$)
#   P&L ‏858.14 ש"ח  →  858.14 / 285.91 = 3.0014
# ‏3.9.2 השתמש ב-2.9617, שנגזר מ**שווי פוזיציה** מוצג. אלה שתי המרות
# שונות בפלוס500. מה שקובע לדיווח רווח/הפסד הוא זו של ה-P&L.
USD_ILS = 3.0014
SPREAD_POINTS = 0.77

# ── 3.9.1: מימון לילה — **נמדד**, לא משוער ──────────────────────
# מקור: עסקה אמיתית שנסגרה בפלוס500 — 1.5 אונקיות, 08/05/26 16:32 →
# 10/08/26 22:55 (5.27 ימים), Overnight Funding ‏30.16- ש"ח.
#     30.16 / 1.5 / 5.27 = 3.82 ש"ח ליום לאונקיה
# ההנחה הקודמת בתיעוד הייתה 6.00 ש"ח ליום ל-0.75 אונקיה — גבוהה פי 2.1.
# ‏3.9.3: השער שנגזר מאותה עסקה (3.0014) אומץ כ-USD_ILS. ר' למעלה.
# ‏אימות: 30.16 / 1.5 / 5.27 = 3.82 — מאושר שוב מול המסך ב-15/08.
FUNDING_ILS_PER_OZ_DAY = 3.82
FUNDING_ILS_PER_DAY_MIN_LOT = round(FUNDING_ILS_PER_OZ_DAY * 0.75, 2)   # 2.86

def funding_cost_ils(oz, days):
    """עלות מימון לילה מצטברת. חיובי = עלות."""
    return FUNDING_ILS_PER_OZ_DAY * oz * max(0.0, days)

def points_to_ils(points):
    return POSITION_SIZE_OZ * abs(points) * USD_ILS

SPREAD_COST_ILS = round(POSITION_SIZE_OZ * SPREAD_POINTS * USD_ILS, 2)

# ── 3.9.1: סימון אחיד להודעות טלגרם ────────────────────────────
# ריבוע צבעוני לפי שיטה + גבול ברור לתחילת וסוף איתות, כדי שאפשר יהיה
# להבחין בין שיטות ובין איתות להודעת שירות במבט אחד בפיד.
METHOD_MARK = {1: "🟥", 2: "🟩", 3: "🟧"}
METHOD_NAME = {1: "שיטה 1", 2: "שיטה 2", 3: "שיטה 3"}

def sig_open(system):
    m = METHOD_MARK.get(system, "⬜")
    return f"{m * 6}\n<b>▼ תחילת איתות · {METHOD_NAME.get(system, '')}</b>"

def sig_close(system):
    m = METHOD_MARK.get(system, "⬜")
    return f"<b>▲ סוף איתות · {METHOD_NAME.get(system, '')}</b>\n{m * 6}"

TF_SIGNAL_OPEN = sig_open(3)
TF_SIGNAL_CLOSE = sig_close(3)

# ── 3.9.3: חותמת גרסה על כל רשומה שנשמרת ──────────────────────
# הצורך: מ-3.9.2 השדה `pnl` מודד דבר אחר לגמרי (0.75oz נטו במקום
# סקאלת סיכון 40 ש"ח). רשומה ישנה וחדשה נראות זהות ואי אפשר להבדיל
# ביניהן בדיעבד. הכלל "אל תערבב נתונים משתי סקאלות" תוחזק עד היום
# לפי תאריך בלבד — עכשיו הוא נאכף בנתונים עצמם.
BOT_VERSION = "3.9.3"
PNL_SCALE = "0.75oz-net"        # מה שהשדה pnl מודד בגרסה הזו

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
    shadows = data.get("shadow_trades", [])
    if len(shadows) > MAX_STORED_TRADES:
        open_sh = [t for t in shadows if t.get("status") == "open"]
        closed_sh = [t for t in shadows if t.get("status") != "open"]
        keep = MAX_STORED_TRADES - len(open_sh)
        data["shadow_trades"] = (closed_sh[-keep:] + open_sh) if keep > 0 else open_sh
    daily = data.get("daily_stats", {})
    if len(daily) > DAILY_STATS_KEEP_DAYS:
        for k in sorted(daily.keys())[:-DAILY_STATS_KEEP_DAYS]:
            del daily[k]

def save_data(data):
    """כותב ל-/tmp (גיבוי מקומי) ודוחף ל-Gist (אחסון קבוע)."""
    prune_data(data)
    # 3.9.3: חותמת גרסה — מי כתב את הרשומות ובאיזו סקאלה.
    data["_bot_version"] = BOT_VERSION
    data["_pnl_scale"] = PNL_SCALE
    data["_saved_at"] = now_il().isoformat()
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
        # 3.6.3 (תיקון באג): הנר האחרון שהספק מחזיר הוא הנר שמתהווה כרגע —
        # RSI/MACD/ADX/בולינגר/פריצה חושבו עליו עד כה, כלומר על נתון חלקי
        # שמשתנה כל דקה. זה גם מה שהבדיל את הבוט החי ממנוע ה-backtest,
        # שתמיד עבד על נרות סגורים. מעכשיו: אינדיקטורים על נרות סגורים,
        # והמחיר החי משמש רק כמחיר כניסה.
        live_price = closes[-1]
        forming = False
        if last_time is not None and len(closes) > 2:
            try:
                mins = int("".join(ch for ch in interval if ch.isdigit()) or 0)
                if "h" in interval and "min" not in interval:
                    mins *= 60
                if mins and (now_il() - last_time).total_seconds() < mins * 60:
                    forming = True
            except Exception:
                forming = False
        if forming:
            closes, highs, lows = closes[:-1], highs[:-1], lows[:-1]
        return {"closes": closes, "highs": highs, "lows": lows,
                "last_time": last_time, "live_price": live_price,
                "forming": forming}
    except Exception as e:
        print(f"שגיאה בשליפת נתונים {symbol}: {e}", flush=True)
        return None

# 3.5.1: פתיחת היום — לתיעוד move_from_open ברשומות איתות (השערה 1).
# נר יומי אחד, ממוטמן ליום שלם → עלות: קריאת API אחת ביום.
_day_open_cache = {}

def get_day_open(symbol_code):
    """פתיחת הנר היומי הנוכחי. None בכשל — תיעוד בלבד, לא חוסם כלום."""
    today = get_today_key()
    cached = _day_open_cache.get(symbol_code)
    if cached and cached["date"] == today:
        return cached["value"]
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol_code,
            "interval": "1day",
            "outputsize": 1,
            "apikey": TWELVEDATA_KEY,
            "timezone": "Asia/Jerusalem"
        }
        r = requests.get(url, params=params, timeout=15)
        d = r.json()
        if "values" not in d or not d["values"]:
            return None
        value = float(d["values"][0]["open"])
        _day_open_cache[symbol_code] = {"date": today, "value": value}
        return value
    except Exception as e:
        print(f"[DAY_OPEN] exception: {e}", flush=True)
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
            "apikey": TWELVEDATA_KEY,
            "timezone": "Asia/Jerusalem"   # 3.6.3: היה חסר — הפיד היה ב-UTC
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
    """3.4: שני מסלולים — 'עיני המערכת' (סימולציה, רק שערי איכות) מול 'התיק שלך' (כל המגבלות).
    שערי איכות: מגמה, מתיחה, ADX, חסימת RSI, ניקוד. שערי תפעול: חיתוך ערב, ברייקר, מגבלות יומיות,
    סלוטים, cooldown. איתות שעובר איכות תמיד נרשם בסימולציה; רק אם עבר גם תפעול — נשלח אליך."""
    if not is_trading_hours(symbol_name):
        print(f"[{symbol_name}] לא בשעות מסחר", flush=True)
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
    # 3.6.3: closes/highs/lows = נרות סגורים בלבד (לאינדיקטורים).
    # current = המחיר החי, לשימוש כמחיר כניסה/סטופ/טארגט בלבד.
    current = prices_data.get("live_price", closes[-1])
    if prices_data.get("forming"):
        print(f"[{symbol_name}] אינדיקטורים על {len(closes)} נרות סגורים | מחיר חי {current}", flush=True)

    # --- אינדיקטורים ---
    rsi = calc_rsi(closes)
    macd_line, macd_signal = calc_macd(closes)
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes)
    atr = calc_atr(highs, lows, closes)
    adx = calc_adx(highs, lows, closes)
    breakout_dir = check_breakout(closes + [current], highs + [current], lows + [current])

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
    # 3.5.1 (תיקון תיוג): "תומך" = הקו מעל/מתחת לקו האיתות (מומנטום משתפר).
    # כשסימן הקו עצמו מנוגד לכיוון (שלילי בקנייה / חיובי במכירה) — מציינים
    # זאת בתווית ומתעדים ב-macd_sign_agree. הניקוד לא השתנה — הפיכת הסכמת
    # הסימן לדרישה קשה היא השערה שדורשת backtest, לא תיקון באג.
    macd_sign_agree = None
    if macd_line is not None and macd_signal is not None:
        if is_long and macd_line > macd_signal:
            macd_sign_agree = macd_line > 0
            note = "" if macd_sign_agree else "; הקו עוד שלילי"
            signals.append(f"📈 MACD תומך ({macd_line}{note})")
            score += 1 * weights["macd"]
        elif (not is_long) and macd_line < macd_signal:
            macd_sign_agree = macd_line < 0
            note = "" if macd_sign_agree else "; הקו עוד חיובי"
            signals.append(f"📉 MACD תומך ({macd_line}{note})")
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

    if FIXED_STOP_USD is not None:
        # 3.6.4: סטופ קבוע בדולרים — רצפת ה-0.35% לא חלה כאן
        stop_distance = FIXED_STOP_USD
    else:
        if atr:
            stop_distance = atr * 1.5
        else:
            stop_distance = current * 0.005
        # רצפת סטופ: מינימום 0.35% מהמחיר, שהסטופ לא יישב בתוך הרעש
        if STOP_FLOOR_PCT is not None:
            stop_distance = max(stop_distance, current * STOP_FLOOR_PCT / 100)
    # מרחק היעד: קבוע בדולרים, או פי 2 מהסטופ (הישן)
    target_distance = FIXED_TARGET_USD if FIXED_TARGET_USD is not None else stop_distance * 2

    entry_price = round(current, 2)

    risk_amount = round(points_to_ils(stop_distance) + SPREAD_COST_ILS, 2)
    risk_pct = round(risk_amount / ACCOUNT_SIZE * 100, 1)

    # target2 = יעד מידע בלבד (לא נסגר עליו) — פי 1.5/2 מהיעד הראשון
    t2_dist = target_distance * (2 if stars >= 4 else 1.5)
    if direction == "קנייה":
        stop = round(current - stop_distance, 2)
        target1 = round(current + target_distance, 2)
        target2 = round(current + t2_dist, 2)
    else:
        stop = round(current + stop_distance, 2)
        target1 = round(current - target_distance, 2)
        target2 = round(current - t2_dist, 2)

    now = now_il()

    # 3.5.1: הקשר אנליטי לתיעוד (הבאג מהרשימה: adx / ema_dev / move_from_open).
    # תיעוד בלבד — שום שער חדש. day_open=None בכשל, לא עוצר כלום.
    day_open = get_day_open(symbol_code)
    move_from_open = round(current - day_open, 2) if day_open else None
    move_atr = round(move_from_open / atr, 2) if (move_from_open is not None and atr) else None
    # 3.5.2: שיפוע ADX — ההפרש מול ה-ADX לפני 5 נרות (השערה 2). תיעוד בלבד.
    adx_slope5 = None
    if adx is not None and len(closes) > 33:
        _adx_prev = calc_adx(highs[:-5], lows[:-5], closes[:-5])
        if _adx_prev is not None:
            adx_slope5 = round(adx - _adx_prev, 2)
    signal_context = {
        "adx": adx,
        "adx_slope5": adx_slope5,           # חיובי = ADX עולה (מגמה נבנית)
        "rsi": rsi,
        "atr": atr,
        "ema_dev": trend["deviation_pct"],
        "day_open": day_open,
        "move_from_open": move_from_open,   # חיובי = מעל הפתיחה
        "move_atr": move_atr,               # המרחק ביחידות ATR (השערה 1)
        "macd": macd_line,
        "macd_sign_agree": macd_sign_agree
    }

    # ================= 3.4: מסלול הסימולציה ("עיני המערכת") =================
    # כל איתות שעבר את שערי האיכות נרשם כאן — גם אם התפעול יחסום אותו בהמשך.
    # cooldown נפרד של 30 דק' לסימולציה, כדי לא לרשום את אותו מצב שוק 4 פעמים.
    shadow = None
    shadow_list = data.setdefault("shadow_trades", [])
    last_sh = data.setdefault("shadow_last_signal", {}).get(symbol_name)
    sh_cooldown_ok = True
    if last_sh:
        try:
            sh_cooldown_ok = (now - datetime.datetime.fromisoformat(last_sh)).total_seconds() >= SIGNAL_COOLDOWN_MINUTES * 60
        except Exception:
            sh_cooldown_ok = True
    if sh_cooldown_ok:
        today = get_today_key()
        if today not in data["daily_stats"]:
            data["daily_stats"][today] = {}
        data["daily_stats"][today]["shadow_seq"] = data["daily_stats"][today].get("shadow_seq", 0) + 1
        shadow = {
            "id": make_trade_id("SH" + symbol_name, now.strftime('%H%M%S')),
            "number": f"S{data['daily_stats'][today]['shadow_seq']}",
            "symbol": symbol_name,
            "direction": direction,
            "entry": entry_price,
            "stop": stop,
            "target1": target1,
            "stars": stars,
            "time": now.isoformat(),
            "timeout": (now + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).isoformat(),
            "status": "open",
            "blocked_reason": None,  # ימולא אם התפעול חוסם
            **signal_context         # 3.5.1: adx / ema_dev / move_from_open ועוד
        }
        shadow_list.append(shadow)
        data["shadow_last_signal"][symbol_name] = now.isoformat()

    # ================= 3.4: שערי תפעול (רק על "התיק שלך") =================
    op_block = None
    if now.hour >= LAST_ENTRY_HOUR:
        op_block = f"חיתוך ערב ({LAST_ENTRY_HOUR}:00)"
    else:
        can, reason = can_trade(symbol_name, data)
        if not can:
            op_block = reason

    if op_block:
        if shadow:
            shadow["blocked_reason"] = op_block
        today = get_today_key()
        if today not in data["daily_stats"]:
            data["daily_stats"][today] = {}
        blocked = data["daily_stats"][today].setdefault("blocked", {})
        blocked[op_block] = blocked.get(op_block, 0) + 1
        save_data(data)
        print(f"[{symbol_name}] איתות איכותי נחסם תפעולית: {op_block}"
              + (f" (נרשם בסימולציה {shadow['number']})" if shadow else ""), flush=True)
        return

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
        "time": now.isoformat(),
        **signal_context   # 3.5.1: הקשר אנליטי — עובר לעסקה האמיתית בכניסה
    }
    cutoff = (now - datetime.timedelta(hours=1)).isoformat()
    data["pending"] = {k: v for k, v in pending.items() if v["time"] > cutoff}

    trend_line = (
        f"📈 מגמה (EMA50 1h): "
        f"{'עלייה 🟢' if is_long else 'ירידה 🔴'} "
        f"({trend['deviation_pct']:+.2f}%)\n"
    )

    msg = (
        f"{sig_open(1)}\n"
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

    if OLD_SIGNALS_WATCH_ONLY:
        # 3.5.0: הישנה נפסלה למסחר (מבחן עמידות 16/07) — נשארת כעיניים בלבד.
        # אין כפתורים ואין pending: אין דרך "להיכנס" עליה בטעות.
        pending.pop(trade_id, None)
        # 3.9.3: מצב שקט — הכל נרשם, כלום לא נשלח.
        if OLD_SIGNALS_SILENT:
            print(f"[S1-SILENT] {trade_num} {direction} @{entry_price} "
                  f"({stars}/5) — נרשם, לא נשלח", flush=True)
        else:
            msg = msg.replace("🚨 <b>איתות סחר", "🚨 <b>[מעקב בלבד] איתות ישן")
            msg += ("\n📊 <i>המערכת הישנה — לא למסחר. הסימולציה עוקבת.</i>"
                    f"\n{sig_close(1)}")
            send_telegram(msg)
    else:
        keyboard = [[
            {"text": "✅ נכנסתי", "callback_data": f"en_{trade_id}"},
            {"text": "❌ דילגתי", "callback_data": f"sk_{trade_id}"}
        ]]
        send_telegram(msg + f"\n{sig_close(1)}", keyboard)

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
    if shadow:
        shadow["linked_real"] = True  # האיתות הזה גם נשלח בפועל

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
                "o": float(v["open"]),
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
              target_mult=2.0, breakeven_frac=None, slippage_points=0.0,
              min_daily_range=None, max_move_from_open_atr=None,
              adx_rising_bars=None, first_entry_hour=None, long_only=False,
              active_start=8, active_end=22,
              fixed_target_pts=None, fixed_stop_pts=None):
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
    slippage_points: מבחן עמידות — כל כניסת שוק מוזזת X נקודות לרעת הכיוון
                     (לונג נכנס גבוה יותר, שורט נמוך יותר). הסטופ/טארגט נגזרים
                     מהכניסה המוזזת — מדמה את פער הדגימה חי-מול-מנוע שנצפה ב-/cross.
    min_daily_range: שער תנועה — אין כניסות חדשות ביום שבו ממוצע הטווח
                     (high-low) של שני ימי המסחר הקודמים נמוך מסף זה בנקודות.
                     ההיגיון: הימים הרווחיים היו ימי תנועה; הטבחים — דשדוש.
                     משתמש בימים קודמים בלבד — אין הצצה לעתיד.
    max_move_from_open_atr: השערה 1 — חסימת רדיפה. אם המחיר כבר זז מפתיחת
                     היום *בכיוון העסקה* יותר מ-K כפול ATR — אין כניסה
                     (רדיפת מהלך שכבר קרה: 21/07 לונגים אחרי +70$,
                     24/07 שורטים אחרי ירידה). מהלך נגד הכיוון לא חוסם —
                     זו דווקא כניסת תיקון. פתיחת יום = פתיחת הנר הראשון
                     של היום הקלנדרי בנתונים — ידועה בזמן האיתות, אין הצצה.
    adx_rising_bars: השערה 2 — ADX עולה. כניסה רק אם ה-ADX עכשיו גבוה
                     מה-ADX לפני N נרות: מגמה שעוד נבנית, לא מהלך מוצה.
                     ADX גבוה שיורד = המהלך כנראה מאחורינו — נחסם, גם אם
                     הוא עדיין מעל הרף 20. אין הצצה — שני החישובים על עבר.
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

    # שער תנועה: טווח (high-low) לכל יום בנתונים — לשימוש כ"ימים קודמים" בלבד
    day_ranges = {}
    if min_daily_range is not None:
        for c in m15:
            dk = c["t"].strftime("%Y-%m-%d")
            if dk not in day_ranges:
                day_ranges[dk] = [c["h"], c["l"]]
            else:
                day_ranges[dk][0] = max(day_ranges[dk][0], c["h"])
                day_ranges[dk][1] = min(day_ranges[dk][1], c["l"])
        day_ranges = {k: v[0] - v[1] for k, v in day_ranges.items()}
    range_day_keys = sorted(day_ranges.keys())
    gated_days = set()

    # השערה 1: פתיחת כל יום קלנדרי = פתיחת הנר הראשון שלו בנתונים
    day_opens = {}
    if max_move_from_open_atr is not None:
        for c in m15:
            dk = c["t"].strftime("%Y-%m-%d")
            if dk not in day_opens:
                day_opens[dk] = c.get("o", c["c"])
    blocked_chase = 0
    blocked_fading = 0  # השערה 2: נחסמו כי ה-ADX יורד
    blocked_hours = 0    # /h9: נרות מחוץ לחלון הפעילות
    blocked_morning = 0  # /h3: איתותים שנחסמו כי לפני שעת הפתיחה
    blocked_short = 0    # /h3: שורטים שנחסמו במצב לונג-בלבד

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
                                   "stars": p["stars"], "day": day,
                                   "et": t, "ct": t, "dir": p["dir"], "entry": entry})
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
                                   "stars": tr["stars"], "day": day,
                                   "et": tr["time"], "ct": t, "dir": tr["dir"], "entry": tr["entry"]})
                else:
                    d_ = abs(tr["entry"] - tr["stop"])
                    closed.append({"result": "loss", "pnl": -(points_to_ils(d_) + SPREAD_COST_ILS),
                                   "stars": tr["stars"], "day": day,
                                   "et": tr["time"], "ct": t, "dir": tr["dir"], "entry": tr["entry"]})
            elif target_hit:
                d_ = abs(tr["target"] - tr["entry"])
                closed.append({"result": "win", "pnl": points_to_ils(d_) - SPREAD_COST_ILS,
                               "stars": tr["stars"], "day": day,
                               "et": tr["time"], "ct": t, "dir": tr["dir"], "entry": tr["entry"]})
            elif timed_out:
                diff = (closes[i] - tr["entry"]) if is_long else (tr["entry"] - closes[i])
                pnl = points_to_ils(diff) - SPREAD_COST_ILS if diff > 0 else -(points_to_ils(abs(diff)) + SPREAD_COST_ILS)
                closed.append({"result": "timeout", "pnl": pnl, "stars": tr["stars"], "day": day,
                               "et": tr["time"], "ct": t, "dir": tr["dir"], "entry": tr["entry"]})
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
        # /h9: חלון הפעילות. ברירת המחדל 8-22 = הבוט החי. active_start=0
        # ו-active_end=24 פותחים 24 שעות (הזהב נסחר גם בלילה).
        if not (active_start <= t.hour < active_end):
            blocked_hours += 1
            continue
        if min_daily_range is not None:
            prev_days = [k for k in range_day_keys if k < day][-2:]
            if len(prev_days) == 2:
                avg_range = (day_ranges[prev_days[0]] + day_ranges[prev_days[1]]) / 2.0
                if avg_range < min_daily_range:
                    gated_days.add(day)
                    continue  # שער תנועה: היומיים הקודמים רדומים — לא סוחרים היום
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

        # השערה 2: ADX חייב לעלות — מגמה שנבנית, לא מהלך מוצה.
        # משווים לחלון זהה שמסתיים N נרות אחורה. אם אין נתון קודם — לא חוסמים.
        if adx_rising_bars is not None and adx is not None:
            p = i - adx_rising_bars
            if p >= 49:
                adx_prev = calc_adx(highs[p - 49:p + 1], lows[p - 49:p + 1], closes[p - 49:p + 1])
                if adx_prev is not None and adx <= adx_prev:
                    blocked_fading += 1
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

        # השערה 1: חסימת רדיפה — המחיר כבר זז מפתיחת היום בכיוון העסקה
        # יותר מ-K כפול ATR → המהלך כבר קרה, לא רודפים אחריו.
        if max_move_from_open_atr is not None and atr:
            d_open = day_opens.get(day)
            if d_open is not None:
                move_dir = (current - d_open) if is_long else (d_open - current)
                if move_dir > max_move_from_open_atr * atr:
                    blocked_chase += 1
                    continue

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

        # /h3 חתך א': חסימת בוקר — אין כניסות חדשות לפני שעה H.
        # רקע: בחי 08:00-10:00 נתנו 18 עסקאות, 0 ניצחונות, ‏432.80-.
        # ממוקם אחרי בדיקת האיכות → המונה סופר איתותים אמיתיים שנחסמו.
        if first_entry_hour is not None and t.hour < first_entry_hour:
            blocked_morning += 1
            continue

        # /h3 חתך ב': לונג בלבד — שוק שורי מבני; בחי שורטים = 8% הצלחה, ‏420-.
        if long_only and not is_long:
            blocked_short += 1
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
            entry_px = current + slippage_points if is_long else current - slippage_points
            # יעד/סטופ קבועים בדולרים (בדיקת "5$ מספיק לי")
            sd = fixed_stop_pts if fixed_stop_pts is not None else stop_distance
            td = fixed_target_pts if fixed_target_pts is not None else sd * target_mult
            stop = entry_px - sd if is_long else entry_px + sd
            target = entry_px + td if is_long else entry_px - td
            open_trades.append({"dir": direction, "entry": entry_px, "stop": stop,
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
        "unfilled": expired_limits, "be": len(bes),
        "detail": closed, "gated_days": len(gated_days),
        "blocked_chase": blocked_chase,
        "blocked_fading": blocked_fading,
        "blocked_hours": blocked_hours,
        "blocked_morning": blocked_morning,
        "blocked_short": blocked_short
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

    # 3.6.5: הבסיס במנוע חייב להיות זהה ללוגיקה החיה, אחרת הדוח מדווח
    # על מערכת אחרת מזו שרצה. כשמוגדרים יעד/סטופ קבועים — הם נכנסים לכאן.
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)

    variants = [
        ("⚙️ בסיס — הלוגיקה החיה (3.3)", {}),
        ("📉 גרסה 3.2 הישנה (השוואה)",
         {"rsi_extreme_block": False, "last_entry_hour": None}),
        ("🎯 יעד 5$ (במקום 10$)",         {"fixed_target_pts": 5.0}),
        ("🎯 יעד 15$",                    {"fixed_target_pts": 15.0}),
        ("🛑 סטופ 8$ (הדוק יותר)",         {"fixed_stop_pts": 8.0}),
        ("🛑 סטופ 14$ (רחב יותר)",         {"fixed_stop_pts": 14.0}),
        ("4️⃣ Break-even אחרי 50% מהדרך",  {"breakeven_frac": 0.5}),
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
    # 3.4.4: שער תנועה — אין כניסות ביום שאחרי יומיים רדומים.
    # ספי המשתמש (5-20) + שניים גבוהים לראות איפה השער מתחיל לנשוך.
    parts.append("🚪 <b>שער תנועה</b> (בסיס + אין מסחר אחרי יומיים רדומים):")
    gate_results = []
    for thr in (5.0, 10.0, 15.0, 20.0, 30.0, 40.0):
        kw = dict(live); kw["min_daily_range"] = thr
        rg = _simulate(m15, h1, **kw)
        gate_results.append((thr, rg))
        parts.append(f"  סף {thr:.0f} נק': {rg['trades']} עסק' | {rg['win_rate']} | "
                     f"{rg['pnl']:+.0f} ש\"ח ({rg['pnl'] - base_pnl:+.0f}) | 🚪 {rg['gated_days']} ימים בחוץ")
    best_thr, best_r = max(gate_results, key=lambda x: x[1]["pnl"])
    if best_r["pnl"] > base_pnl:
        parts.append(f"  🧪 עמידות לסף הטוב ({best_thr:.0f} נק'):")
        for slip in (3.0, 6.0, 10.0):
            kw = dict(live); kw["min_daily_range"] = best_thr; kw["slippage_points"] = slip
            rs2 = _simulate(m15, h1, **kw)
            parts.append(f"    ‏{slip:.0f} נק' נגד: {rs2['win_rate']} | {rs2['pnl']:+.0f} ש\"ח")
    parts.append("")

    # 3.4.3: מבחן עמידות — הבסיס עם כניסות מוזזות לרעתנו.
    # רקע: /cross הראה שפער דגימה של ~10 נק' ב-13/07 הפך +165 ל-134-.
    # אם הרווח קורס על הזזה קטנה — אין יתרון אמיתי, יש רעש ביצוע.
    parts.append("🧪 <b>מבחן עמידות</b> (הבסיס, כניסה מוזזת נגדנו):")
    for slip in (3.0, 6.0, 10.0):
        kw = dict(live); kw["slippage_points"] = slip
        rs = _simulate(m15, h1, **kw)
        parts.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח ({rs['pnl'] - base_pnl:+.0f} מול הבסיס)")
    parts.append("")
    parts.append("💡 אחוז = זכיות מתוך זכיות+הפסדים | ⏰ = תום 6 שעות | 🤝 = סטופ שהוזז לכניסה")
    return "\n".join(parts)

def run_h1_backtest():
    """השערה 1 (פקודת /h1): חסימת רדיפה — מרחק מפתיחת יום ב-ATR.
    בודקת את ההשערה לבד מול הבסיס החי, על גריד ספים, + מבחן עמידות לסף
    הטוב ביותר. מחזירה רשימת הודעות (מפוצל — מגבלת 4096 של טלגרם)."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)   # ~30 ימי מסחר
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return ["⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."]

    date_from = m15[0]["t"].strftime("%d/%m")
    date_to = m15[-1]["t"].strftime("%d/%m")
    # 3.6.5: יעד/סטופ קבועים נכנסים לבסיס כדי שהמנוע יריץ את הלוגיקה החיה
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)

    base = _simulate(m15, h1, **live)
    parts = [f"🧪 <b>השערה 1: חסימת רדיפה — {date_from} עד {date_to}</b>\n"
             f"אין כניסה אם המחיר כבר זז מפתיחת היום בכיוון העסקה יותר מ-K×ATR.\n\n"
             f"<b>⚙️ בסיס (הלוגיקה החיה):</b>\n"
             f"🔔 {base['trades']} עסק' | ✅ {base['wins']} | ❌ {base['losses']}"
             + (f" | ⏰ {base['timeouts']}" if base['timeouts'] else "") + "\n"
             f"📊 {base['win_rate']} | 💰 {base['pnl']:+.2f} ש\"ח\n"]

    grid_results = []
    lines = ["<b>🚪 גריד ספים (K × ATR):</b>"]
    for k in (3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0):
        kw = dict(live); kw["max_move_from_open_atr"] = k
        r = _simulate(m15, h1, **kw)
        grid_results.append((k, r))
        lines.append(f"  K={k:.0f}: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f}) | 🚫 נחסמו: {r['blocked_chase']}")
    parts.append("\n".join(lines))

    best_k, best_r = max(grid_results, key=lambda x: x[1]["pnl"])
    verdict = []
    if best_r["pnl"] > base["pnl"]:
        verdict.append(f"\n🧪 <b>מבחן עמידות לסף הטוב (K={best_k:.0f}):</b>")
        robust = True
        for slip in (3.0, 6.0, 10.0):
            kw = dict(live); kw["max_move_from_open_atr"] = best_k
            kw["slippage_points"] = slip
            rs = _simulate(m15, h1, **kw)
            verdict.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח "
                           f"({rs['pnl'] - base['pnl']:+.0f} מול בסיס ללא הזזה)")
            if rs["pnl"] <= base["pnl"]:
                robust = False
        verdict.append("\n✅ שורד עמידות — מועמד ליישום." if robust
                       else "\n⚠️ קורס תחת הזזה — יתרון לא אמיתי.")
    else:
        verdict.append("\n❌ אף סף לא שיפר את הבסיס בתקופה זו — ההשערה לא מאושרת על המדגם.")
    verdict.append("💡 מספר עסקאות נמוך = מדגם קטן; לשקול גם מול חתכי ה-shadow החיים.")
    parts.append("\n".join(verdict))
    return parts

def run_h2_backtest():
    """השערה 2 (פקודת /h2): ADX עולה — כניסה רק כשהמגמה עוד נבנית.
    ADX גבוה שיורד = מהלך מוצה, נחסם גם אם מעל הרף. גריד על עומק ההשוואה
    (N נרות אחורה) + מבחן עמידות ל-N הטוב. מחזירה רשימת הודעות."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)   # ~30 ימי מסחר
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return ["⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."]

    date_from = m15[0]["t"].strftime("%d/%m")
    date_to = m15[-1]["t"].strftime("%d/%m")
    # 3.6.5: יעד/סטופ קבועים נכנסים לבסיס כדי שהמנוע יריץ את הלוגיקה החיה
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)

    base = _simulate(m15, h1, **live)
    parts = [f"🧪 <b>השערה 2: ADX עולה — {date_from} עד {date_to}</b>\n"
             f"כניסה רק אם ה-ADX גבוה מערכו לפני N נרות (מגמה נבנית, לא מוצה).\n\n"
             f"<b>⚙️ בסיס (הלוגיקה החיה):</b>\n"
             f"🔔 {base['trades']} עסק' | ✅ {base['wins']} | ❌ {base['losses']}"
             + (f" | ⏰ {base['timeouts']}" if base['timeouts'] else "") + "\n"
             f"📊 {base['win_rate']} | 💰 {base['pnl']:+.2f} ש\"ח\n"]

    grid_results = []
    lines = ["<b>🚪 גריד עומק השוואה (N נרות 15 דק' אחורה):</b>"]
    for n in (2, 3, 5, 8, 12):
        kw = dict(live); kw["adx_rising_bars"] = n
        r = _simulate(m15, h1, **kw)
        grid_results.append((n, r))
        lines.append(f"  N={n}: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f}) | 🚫 נחסמו: {r['blocked_fading']}")
    parts.append("\n".join(lines))

    best_n, best_r = max(grid_results, key=lambda x: x[1]["pnl"])
    verdict = []
    if best_r["pnl"] > base["pnl"]:
        verdict.append(f"\n🧪 <b>מבחן עמידות לעומק הטוב (N={best_n}):</b>")
        robust = True
        for slip in (3.0, 6.0, 10.0):
            kw = dict(live); kw["adx_rising_bars"] = best_n
            kw["slippage_points"] = slip
            rs = _simulate(m15, h1, **kw)
            verdict.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח "
                           f"({rs['pnl'] - base['pnl']:+.0f} מול בסיס ללא הזזה)")
            if rs["pnl"] <= base["pnl"]:
                robust = False
        verdict.append("\n✅ שורד עמידות — מועמד ליישום." if robust
                       else "\n⚠️ קורס תחת הזזה — יתרון לא אמיתי.")
    else:
        verdict.append("\n❌ אף עומק לא שיפר את הבסיס בתקופה זו — ההשערה לא מאושרת על המדגם.")
    verdict.append("💡 מספר עסקאות נמוך = מדגם קטן; לשקול גם מול חתכי ה-shadow החיים.")
    parts.append("\n".join(verdict))
    return parts

def run_h3_backtest():
    """‏/h3: שלושת החתכים שהנתונים החיים הצביעו עליהם (לא תיאוריה):
    א) חסימת בוקר — 08:00-10:00 בחי: 18 עסקאות, 0 ניצחונות, ‏432.80-.
    ב) לונג בלבד — שורטים בחי: 8% הצלחה, ‏420-.
    ג) גאומטריית יציאה — 23/36 נחנקו על רצפת הסטופ; /mfe הראה שהעסקאות
       הלכו לטובה ואז התהפכו → סטופ רחב יותר / טארגט קרוב יותר.
    אזהרה: החתכים נולדו מ-36 העסקאות החיות — הבדיקה כאן היא על מדגם
    המנוע הנפרד, בדיוק כדי לא לאשר התאמת-יתר לאותו מדגם. כל שורה משנה
    משתנה אחד מול הבסיס. מחזירה רשימת הודעות."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)   # ~30 ימי מסחר
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return ["⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."]

    date_from = m15[0]["t"].strftime("%d/%m")
    date_to = m15[-1]["t"].strftime("%d/%m")
    # 3.6.5: יעד/סטופ קבועים נכנסים לבסיס כדי שהמנוע יריץ את הלוגיקה החיה
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)

    base = _simulate(m15, h1, **live)
    parts = [f"🧪 <b>/h3: חתכי הנתונים החיים — {date_from} עד {date_to}</b>\n"
             f"שלושת הממצאים מ-36 העסקאות, נבחנים על מדגם המנוע הנפרד.\n\n"
             f"<b>⚙️ בסיס (הלוגיקה החיה):</b>\n"
             f"🔔 {base['trades']} עסק' | ✅ {base['wins']} | ❌ {base['losses']}"
             + (f" | ⏰ {base['timeouts']}" if base['timeouts'] else "") + "\n"
             f"📊 {base['win_rate']} | 💰 {base['pnl']:+.2f} ש\"ח\n"]

    all_variants = []  # (תווית, kwargs, תוצאה) — לבחירת הטוב למבחן עמידות

    # --- א' — חסימת בוקר ---
    lines = ["<b>🌅 א' — חסימת בוקר (אין כניסות לפני שעה H):</b>"]
    for h in (9, 10, 11):
        kw = {"first_entry_hour": h}
        full = dict(live); full.update(kw)
        r = _simulate(m15, h1, **full)
        all_variants.append((f"בוקר H={h}", kw, r))
        lines.append(f"  H={h}:00: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f}) | 🚫 נחסמו: {r['blocked_morning']}")
    parts.append("\n".join(lines))

    # --- ב' — לונג בלבד ---
    kw = {"long_only": True}
    full = dict(live); full.update(kw)
    r = _simulate(m15, h1, **full)
    all_variants.append(("לונג בלבד", kw, r))
    parts.append(f"<b>🟢 ב' — לונג בלבד:</b>\n"
                 f"  {r['trades']} עסק' | {r['win_rate']} | "
                 f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f}) | 🚫 שורטים שנחסמו: {r['blocked_short']}")

    # --- ג' — גאומטריית יציאה (משתנה אחד בכל שורה) ---
    lines = ["<b>📐 ג' — גאומטריית יציאה:</b>",
             "  רצפת סטופ (טארגט 2× כמו בחי):"]
    for floor in (0.5, 0.7, 1.0):
        kw = {"stop_floor_pct": floor}
        full = dict(live); full.update(kw)
        r = _simulate(m15, h1, **full)
        all_variants.append((f"סטופ {floor}%", kw, r))
        lines.append(f"    רצפה {floor}%: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f})")
    lines.append("  טארגט (רצפת סטופ 0.35% כמו בחי):")
    for tm in (1.5, 1.0):
        kw = {"target_mult": tm}
        full = dict(live); full.update(kw)
        r = _simulate(m15, h1, **full)
        all_variants.append((f"טארגט {tm}×", kw, r))
        lines.append(f"    ‏{tm}×: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f})")
    parts.append("\n".join(lines))

    # --- מבחן עמידות לחתך הטוב ביותר ---
    best_label, best_kw, best_r = max(all_variants, key=lambda x: x[2]["pnl"])
    verdict = []
    if best_r["pnl"] > base["pnl"]:
        verdict.append(f"\n🧪 <b>מבחן עמידות לחתך הטוב ({best_label}):</b>")
        robust = True
        for slip in (3.0, 6.0, 10.0):
            full = dict(live); full.update(best_kw)
            full["slippage_points"] = slip
            rs = _simulate(m15, h1, **full)
            verdict.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח "
                           f"({rs['pnl'] - base['pnl']:+.0f} מול בסיס ללא הזזה)")
            if rs["pnl"] <= base["pnl"]:
                robust = False
        verdict.append("\n✅ שורד עמידות — מועמד ליישום." if robust
                       else "\n⚠️ קורס תחת הזזה — יתרון לא אמיתי.")
    else:
        verdict.append("\n❌ אף חתך לא שיפר את הבסיס — הממצאים מ-36 העסקאות לא שרדו מדגם נפרד (התאמת יתר).")
    verdict.append("💡 חתך שעובר כאן וגם בחי — ראיה כפולה. חתך שעובר רק בחי — חשד להתאמת יתר.")
    parts.append("\n".join(verdict))
    return parts

# ============================================================
# טיפול בתגובות משתמש
# ============================================================


# ============================================================
# 3.4.5: שיטה 2 — עוקב מגמה איטי על נרות 4 שעות. פקודת /slow.
# חוקים (אושרו 18/07): כניסה בפריצת שיא/שפל N ימי מסחר; יציאה
# בסטופ נגרר בלבד (שפל/שיא M ימים, רק מתהדק); עסקה אחת בכל רגע;
# סיכון קבוע בש"ח לעסקה — גודל הפוזיציה נגזר מרוחב הסטופ.
# סימולציה וקריאה בלבד — הלוגיקה החיה לא נגעה.
# ============================================================
SLOW_RISK_ILS = 40.0            # סיכון לעסקה — **לבדיקות היסטוריות בלבד**
# ══════════════════════════════════════════════════════════════
# 3.9.2 — הדיווח עבר מסקאלת סיכון לגודל אמיתי
# ══════════════════════════════════════════════════════════════
# עד 3.9.1 כל דיווח בשקלים חושב לפי SLOW_RISK_ILS=40: הגודל התאים
# את עצמו לרוחב הסטופ כך שהסיכון תמיד 40 ש"ח. זו סקאלה תיאורטית —
# בפלוס500 אי אפשר לפתוח 0.28 אונקיה. המינימום הוא 0.75, נקודה.
#
# מה זה עשה בפועל: עסקה איטי #1 (13/08) דווחה כ-**41.20+ ש"ח**.
# בגודל האמיתי, עם ספרד ומימון של 8 ימים, היא הייתה **11.28- ש"ח**.
# הבוט הראה רווח על עסקה מפסידה.
#
# מ-3.9.2: כל שקל שהבוט מציג הוא 0.75 אונקיה בפועל, בניכוי ספרד
# ומימון. אין יותר שתי סקאלות.
REPORT_LOT_OZ = 0.75            # הגודל שבו מדווח הכל. = SLOW_LOT_OZ

def ils_per_point(oz=None):
    """שקלים לכל דולר תזוזה, בגודל אמיתי."""
    return (REPORT_LOT_OZ if oz is None else oz) * USD_ILS

# 3.9.3: ‏real_pnl() הוסרה — היא הוגדרה ב-3.9.2 ומעולם לא נקראה
# (מסלול הסגירה שכפל אותה inline), ושמה התנגש עם משתנה מקומי ב-/status.
# מחליפה אותה slow_real_pnl() ליד _finalize_trade, שגם סופרת יחידות,
# ימי החזקה ומימון, ויודעת מתי לא לגבות ספרד.

# ===== 3.5.0: מצב כפול — שיטה 2 למסחר, הישנה למעקב-בלבד =====
SLOW_LIVE = True                # True = איתותי שיטה 2 (🐢) פעילים עם כפתורים
OLD_SIGNALS_WATCH_ONLY = True   # True = איתותי השיטה הישנה (🚨) נשלחים בלי כפתורים, "מעקב בלבד"; הצללים ממשיכים
# ── 3.9.3: שיטה 1 רצה בשקט ─────────────────────────────────────
# ‏True = איתותי שיטה 1 **לא נשלחים לטלגרם בכלל**. כל השאר ממשיך בדיוק
# כמו קודם: הצללים נפתחים ונסגרים, shadow_trades/daily_stats מתעדכנים,
# signal_history נרשם, והדוח היומי ממשיך להציג את שיטה 1.
# הנימוק: לפי הגיסט — 18-19 איתותים ביום, 77 צללים ב-9 ימים, 42.9%
# הצלחה, ‏759- ש"ח. שיטה 1 רדומה ואין השערות פתוחות (STATUS §9.7);
# ההודעות הן רעש שמסתיר את שיטות 2 ו-3.
# ‏False מחזיר את ההודעות בלי שום שינוי אחר.
OLD_SIGNALS_SILENT = True
SLOW_ENTRY_DAYS = 20            # כניסה: פריצת שיא/שפל 20 ימי מסחר (הקומבינציה שניצחה ב-/slow: +474, 46%, שרדה 10 נק')
SLOW_TRAIL_DAYS = 4             # 3.9.1: 3 → 4. אושר בעבר ולא היה פרוס.
                                # הנימוק הוא עמידות, לא רווח נקי: ב-0$ ההפרש
                                # קטן (1796 מול 1578), אבל בהחלקה 10$ זה
                                # 1230 מול 748, ו-MaxDD ‏109- מול ‏139-.
SLOW_PENDING_HOURS = 4          # תוקף איתות עד הנר הבא
# 3.8.0 — ברייקאיבן לשיטה 2 (נבדק 09/08 על xauusd_h4.csv, 2.5 שנים)
#   אחרי שהמחיר זז SLOW_BE_TRIGGER$ לטובתנו, הסטופ מוקפץ לכניסה + OFFSET.
#   הסטופ מתהדק בלבד — הברייקאיבן לא מרפה סטופ נגרר שכבר עבר אותו.
#   התוצאה: הרווח כמעט זהה, הירידה נחתכת ב-~43%.
#   בסיס: +632, MaxDD -161 | BE 25+3: +778, MaxDD -72
#   MC (2,000): גרוע -316 → -171 | Bootstrap: P(רווח) 97.5% → 99.7%
#   בהחלקה 10$ (פער הביצוע האמיתי): גרוע -361 → -225
#   OFFSET=3 מכסה את הספרד — יציאה בברייקאיבן היא אפס, לא מינוס.
SLOW_BE_TRIGGER = 25.0          # None = מכבה את הברייקאיבן לגמרי
SLOW_BE_OFFSET = 3.0            # כמה מעל הכניסה מציבים את הסטופ
# 3.9.0 — פירמידינג לשיטה 2 (נבדק 10/08 על xauusd_h4.csv, 2.5 שנים,
#   בגודל יחידה קבוע 0.75oz — מציאות פלוס500, לא גודל-לפי-סיכון):
#   כניסה ראשונה 0.75oz. כשנר 4h נסגר 0.5×ATR14 מעבר לכניסה —
#   התראה לפתוח פוזיציה שנייה של 0.75oz. שתיהן על אותו סטופ נגרר.
#   תוצאות (יחידה בודדת ← 2 יחידות): ‏3,450+ ← ‏7,157+ | הצלחה 74% ← 45%
#   MaxDD כמעט זהה: ‏657- ← ‏657- (התוספת נכנסת רק אחרי שהעסקה ברווח)
#   עמידות: סליפג' 3$ ‏5,609+ | סליפג' 10$ ‏4,482+ (בסיס: ‏3,047+/‏2,395+)
#   MC (2,000 ערבובים) גרוע: ‏1,016- ← ‏1,158-
#   שקילות חשיפה: 2×0.75 מדורג = אותה חשיפה מקסימלית כמו 1.5 בכניסה
#   אחת, אבל חצי MaxDD (‏657- מול ‏1,313-).
SLOW_PYRAMID_UNITS = 2          # 1 = מכבה את הפירמידינג (התנהגות 3.8.0)
SLOW_PYRAMID_STEP_N = 0.5       # הטריגר להוספה: 0.5×ATR14 מהכניסה
SLOW_LOT_OZ = 0.75              # גודל יחידה בפועל בפלוס500 (המינימום)
CANDLES_PER_DAY_4H = 6          # זהב נסחר ~24 שעות → 6 נרות 4ש' ליום מסחר

def _spread_points():
    """הספרד בנקודות — נגזר מהקבועים הקיימים כדי לא להגדיר פעמיים."""
    per_pt = points_to_ils(1)
    return SPREAD_COST_ILS / per_pt if per_pt else 0.5

def _simulate_slow(h4, entry_days=10, trail_days=5, risk_ils=SLOW_RISK_ILS,
                   slippage_points=0.0, fixed_target_points=None,
                   pyramid_units=1, pyramid_step_n=0.5,
                   be_trigger=None, be_offset=0.0):
    """עוקב מגמה איטי על נרות 4 שעות.

    entry_days: פריצת שיא/שפל של N ימי מסחר (N*6 נרות) פותחת עסקה.
    trail_days: סטופ נגרר = שפל/שיא M ימים; מתהדק בלבד, לא נסוג.
    risk_ils: הסיכון ההתחלתי ליחידה; גודל היחידה = risk_ils / רוחב הסטופ שלה.
    slippage_points: הזזת כניסה לרעת הכיוון (מבחן עמידות). חלה גם על תוספות.
    fixed_target_points: אם ניתן — סוגר ברווח קבוע של X נק' (וריאנט השוואה,
                         לא חלק מהשיטה; בשביל "טארגט 5 נקודות").
    pyramid_units: השערה 8 — פירמידינג בסגנון טרטלס. 1 = התנהגות הבסיס
                   בדיוק (יחידה אחת). 2-4 = מוסיפים יחידה בכל פעם שנר
                   נסגר pyramid_step_n×N מעבר לתוספת האחרונה (N = ATR14
                   על 4h בכניסה). תוספת על סגירת נר בלבד — שמרני, בלי
                   ספקולציית תוך-נר. כל היחידות יוצאות יחד על הסטופ
                   הנגרר. שים לב: כל יחידה מסכנת risk_ils נוסף —
                   pyramid_units=4 מכפיל את הסיכון פי ~4.
    be_trigger: ברייקאיבן — אחרי תנועה של X$ לטובתנו, הסטופ עולה
                לכניסה + be_offset. None = כבוי (התנהגות הבסיס).
                שמרני: הזרוע נדרכת בנר אחד ופועלת מהנר הבא, כי אי אפשר
                לדעת את סדר האירועים בתוך נר.
    """
    ew = entry_days * CANDLES_PER_DAY_4H
    tw = trail_days * CANDLES_PER_DAY_4H
    spread_pts = _spread_points()
    pos = None
    closed = []

    def _close_pos(p, exit_px, when, open_at_end=False):
        total = 0.0
        for u in p["units"]:
            pts_u = (exit_px - u["e"]) if p["dir"] == "long" else (u["e"] - exit_px)
            total += (pts_u - spread_pts) * u["ipp"]
        pts0 = (exit_px - p["entry"]) if p["dir"] == "long" else (p["entry"] - exit_px)
        days_held = max(0.0, (when - p["et"]).total_seconds() / 86400.0)
        rec = {"pnl": total, "pts": pts0, "days": days_held,
               "dir": p["dir"], "et": p["et"], "ct": when,
               "units": len(p["units"])}
        if open_at_end:
            rec["open_at_end"] = True
        closed.append(rec)

    for i in range(ew, len(h4)):
        c = h4[i]
        hh = max(x["h"] for x in h4[i - ew:i])       # שיא N ימים (עד הנר הקודם)
        ll = min(x["l"] for x in h4[i - ew:i])
        if pos is None:
            direction = None
            if c["c"] > hh:
                direction = "long"
            elif c["c"] < ll:
                direction = "short"
            if direction:
                entry = c["c"] + slippage_points if direction == "long" else c["c"] - slippage_points
                t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
                t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
                trail = t_lo if direction == "long" else t_hi
                stop_pts = abs(entry - trail)
                if stop_pts < 1e-6:
                    continue
                # N לפירמידינג: ATR14 על 4h בזמן הכניסה (משתמש בעבר בלבד)
                w = h4[max(0, i - 30):i + 1]
                n_atr = calc_atr([x["h"] for x in w], [x["l"] for x in w],
                                 [x["c"] for x in w]) if pyramid_units > 1 else None
                pos = {"dir": direction, "entry": entry, "trail": trail,
                       "stop_pts": stop_pts,
                       "units": [{"e": entry, "ipp": risk_ils / stop_pts}],
                       "last_add": entry, "n_atr": n_atr,
                       "be_hit": False,
                       "et": c["t"]}
            continue
        # עדכון סטופ נגרר — מתהדק בלבד
        t_lo = min(x["l"] for x in h4[max(0, i - tw):i])
        t_hi = max(x["h"] for x in h4[max(0, i - tw):i])
        if pos["dir"] == "long":
            pos["trail"] = max(pos["trail"], t_lo)
        else:
            pos["trail"] = min(pos["trail"], t_hi)

        # ברייקאיבן — הזרוע נדרכה בנר קודם; הסטופ מתהדק בלבד
        if pos.get("be_hit"):
            be_px = (pos["entry"] + be_offset) if pos["dir"] == "long" else (pos["entry"] - be_offset)
            if pos["dir"] == "long":
                pos["trail"] = max(pos["trail"], be_px)
            else:
                pos["trail"] = min(pos["trail"], be_px)

        # השערה 8: תוספות פירמידינג — רק על סגירת נר מעבר לטריגר
        if pyramid_units > 1 and pos.get("n_atr") and len(pos["units"]) < pyramid_units:
            step = pyramid_step_n * pos["n_atr"]
            while len(pos["units"]) < pyramid_units:
                if pos["dir"] == "long":
                    trigger = pos["last_add"] + step
                    if c["c"] < trigger:
                        break
                    add_px = trigger + slippage_points
                    dist = add_px - pos["trail"]
                else:
                    trigger = pos["last_add"] - step
                    if c["c"] > trigger:
                        break
                    add_px = trigger - slippage_points
                    dist = pos["trail"] - add_px
                if dist < 1e-6:
                    break  # הסטופ צמוד מדי ליחידה חדשה — לא מוסיפים
                pos["units"].append({"e": add_px, "ipp": risk_ils / dist})
                pos["last_add"] = trigger

        exit_px = None
        # וריאנט טארגט קבוע (להשוואה בלבד)
        if fixed_target_points is not None:
            if pos["dir"] == "long" and c["h"] >= pos["entry"] + fixed_target_points:
                exit_px = pos["entry"] + fixed_target_points
            elif pos["dir"] == "short" and c["l"] <= pos["entry"] - fixed_target_points:
                exit_px = pos["entry"] - fixed_target_points
        if exit_px is None:
            if pos["dir"] == "long" and c["l"] <= pos["trail"]:
                exit_px = min(pos["trail"], c["o"]) if "o" in c else pos["trail"]
            elif pos["dir"] == "short" and c["h"] >= pos["trail"]:
                exit_px = max(pos["trail"], c["o"]) if "o" in c else pos["trail"]
        if exit_px is not None:
            _close_pos(pos, exit_px, c["t"])
            pos = None
            continue

        # דריכת הברייקאיבן בסוף הנר — פועל מהנר הבא
        if be_trigger is not None and not pos["be_hit"]:
            mfe = (c["h"] - pos["entry"]) if pos["dir"] == "long" else (pos["entry"] - c["l"])
            if mfe >= be_trigger:
                pos["be_hit"] = True
    # פוזיציה שנותרה פתוחה בסוף הנתונים — נסגרת לפי המחיר האחרון (mark-to-market),
    # אחרת מגמה ארוכה שלא נשברה לא נספרת בכלל בדוח
    if pos is not None and h4:
        last = h4[-1]
        _close_pos(pos, last["c"], last["t"], open_at_end=True)
        pos = None
    wins = [x for x in closed if x["pnl"] > 0]
    losses = [x for x in closed if x["pnl"] <= 0]
    n = len(closed)
    return {
        "trades": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": f"{round(100 * len(wins) / n)}%" if n else "—",
        "pnl": round(sum(x["pnl"] for x in closed), 2),
        "avg_win": round(sum(x["pnl"] for x in wins) / len(wins), 1) if wins else 0.0,
        "avg_loss": round(sum(x["pnl"] for x in losses) / len(losses), 1) if losses else 0.0,
        "avg_days": round(sum(x["days"] for x in closed) / n, 1) if n else 0.0,
        "worst": round(min((x["pnl"] for x in closed), default=0.0), 1),
        "avg_units": round(sum(x.get("units", 1) for x in closed) / n, 2) if n else 0.0,
        "max_units": max((x.get("units", 1) for x in closed), default=0),
        "detail": closed,
    }

# ============================================================
# שיטה 3 📊 — ליבת מגמה על מסגרות זמן גבוהות (4H + 6H)
# ============================================================
# הרקע (בדיקה על 2.5 שנות נתוני Dukascopy, 1.5 אונקיות):
#   הספרד קבוע 0.77$ — הוא 15% מתנועה של 5$ אבל 1.5% מתנועה של 50$.
#   זו הסיבה היחידה שהמסגרות הגבוהות עוברות בזמן ש-15 דק' נכשלת.
#
#   4H: 247 עסק' | 55% | ‏6,167+ | שורד 3$ החלקה | 4 מ-5 תקופות חיוביות
#   6H: 164 עסק' | 62% | ‏11,720+ | שורד 3$ החלקה | 5 מ-5 תקופות חיוביות
#       Monte Carlo (2000 ערבובים): ירידה חציונית ‏2,179-, גרועה ‏5,722-
#       Bootstrap (2000 דגימות): 99.7% הסתברות לרווח
#       נשאר רווחי גם בספרד פי 10 (8$ → ‏6,402+)
#
# הערות המבקר החיצוני שמיושמות כאן:
#   • אפס אופטימיזציה — הפרמטרים קפואים כפי שנבדקו. כל שינוי מאפס את האישורים.
#   • Forward test — מעקב בלבד, בלי כסף, עד שיצטברו מספיק איתותים חיים.
#   • ניהול סיכון הוא הבעיה, לא האלגוריתם — לכן כל איתות מציג את גודל
#     הפוזיציה הנדרש ואת החשבון המינימלי.
#   • המדגם דק (164 עסקאות) — מוצג בהודעה כדי שלא יישכח.
# ============================================================

# worst_dd = הירידה המצטברת הגרועה ביותר מ-Monte Carlo (2000 ערבובים),
# מדודה ב-1.5 אונקיות. זה המספר שקובע גודל פוזיציה — לא הסיכון בעסקה
# בודדת, כי מה שמוחק חשבון הוא רצף הפסדים ולא עסקה אחת.
# כל מסגרת מציגה שתי תוכניות יציאה על אותה כניסה ואותו סטופ:
#   far  = יעד 2×ATR — הכי רווחי (‏71 ש"ח/עסקה ב-6H), אבל ימים והרבה הפסדים
#   near = יעד 10$ קבוע — הושבת ב-3.9.1, ר' למטה.
# 3.9.1: מספרי ה-far להלן **לא שוחזרו** ע"י tools/tf_engine.py. השחזור
# על אותם נתונים נותן 4H ‏4,485+ ו-6H ‏7,194+ (מנוע עסקה-אחת), ובטווח
# היישורים 4,485–7,345 ו-6,014–9,862. המספר של 4H נמצא בתוך הטווח;
# זה של 6H (11,720) מעליו. הם נשארים כאן כתיעוד היסטורי בלבד ומסומנים
# ככאלה בהודעה — לא ככיול. אין לכייל את המנוע אליהם.
TF_CONFIGS = [
    {"name": "4H", "hours": 4, "worst_dd": 8106, "med_dd": 3485,
     "far": {"trades": 247, "wr": "55%", "pnl": "+6,167", "per": "+24.97", "periods": "4/5"}},
    {"name": "6H", "hours": 6, "worst_dd": 6009, "med_dd": 2172,
     "far": {"trades": 164, "wr": "62%", "pnl": "+11,720", "per": "+71.47", "periods": "5/5"}},
]
# ── 3.9.1: היעד הקרוב הושבת ────────────────────────────────────
# יעד קבוע של 10$ מול סטופ שגדל עם ה-ATR (היום ~59$) = סיכון 59 כדי
# להרוויח 10. הבדיקה מחדש (tools/tf_engine.py):
#     יעד 10$ →  4H: ‏3-   | 6H: ‏776-
#     יעד 20$ →  4H: 1,772 | 6H: 604
#     יעד 50$ →  4H: 2,699 | 6H: 2,110
# והוא **נשבר עם הזמן** ככל שה-ATR גדל: 2024 ‏79-‏ / 2025 ‏217-‏ / 2026 ‏481-‏,
# למרות שאחוז ההצלחה עלה ל-89%. עד 3.9.0 הוא הוצג באיתות כאילו הוא
# אופציה תקפה. הוא לא. None = לא מוצג. אין לשחזר ערך קבוע בדולרים —
# אם יוחזר יעד קרוב, הוא חייב להיות ביחס ל-ATR.
TF_NEAR_TARGET_USD = None
TF_DD_BASE_OZ = 1.5        # גודל הפוזיציה שבו נמדדו הירידות
TF_DD_BUDGET = 0.30        # אחוז מהחשבון שמותר לירידה הגרועה לצרוך
TF_BREAKOUT_BARS = 20      # פריצת שיא/שפל של 20 נרות
TF_EMA_PERIOD = 50         # מסנן מגמה
TF_ATR_PERIOD = 14
TF_STOP_ATR = 2.0          # סטופ = 2×ATR
TF_TARGET_ATR = 2.0        # יעד = 2×ATR

# 3.9.3: ‏TF_RISK_ILS ו-PLUS500_MIN_OZ הוסרו. הם נוספו ב-3.9.1 עבור
# "גודל לפי סיכון לשיטה 3", אבל 3.9.2 החליט על גודל קבוע (REPORT_LOT_OZ)
# והם נשארו קוד מת. ‏STATUS עדיין מונה את תיקון #5 של 3.9.1 — הוא בוטל.

# 3.9.1: חלון הגיבוי בין המסגרות (הממצא החזק בשיטה 3).
# איתות 4H בלי איתות 6H מקביל הוא קבוצה מפסידה שיטתית. נרות 4H נסגרים
# ב-00/04/08/12/16/20 ונרות 6H ב-00/06/12/18 → הפער המרבי 4 שעות, ולכן
# גיבוי יכול להגיע עד 4 שעות **אחרי** האיתות. אין כאן סינון אוטומטי —
# רק סימון באיתות, כי שיטה 3 היא מעקב בלבד.
TF_BACKUP_WINDOW_H = 4     # עד כמה שעות אחרי האיתות מקבלים גיבוי
TF_BACKUP_LOOKBACK_H = 6   # וכמה אחורה נחשב "גיבוי קיים" (נר 6H אחד)


def _tf_aggregate(h1_bars, hours):
    """מצרף נרות שעה לנרות של N שעות, מיושר לגבול השעה."""
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


def _tf_atr(bars, n=TF_ATR_PERIOD):
    if len(bars) < n + 1:
        return None
    tr = []
    for j in range(len(bars) - n, len(bars)):
        pc = bars[j - 1]["c"]
        tr.append(max(bars[j]["h"] - bars[j]["l"],
                      abs(bars[j]["h"] - pc), abs(pc - bars[j]["l"])))
    return sum(tr) / n


def tf_monitor(data, h1):
    """3.9.3: סוגר איתותי שיטה 3 — הפונקציה הזו פשוט לא הייתה קיימת.

    עד 3.9.2 כל רשומה ב-tf_signals נכתבה עם status="open" ושום קוד
    לא סגר אותה. הגיסט מ-15/08 מראה 10 מתוך 10 תקועות כ-open, בלי
    תוצאה אחת. המשמעות: ה-forward test — שהוא לפי STATUS §9.5
    "העדיפות הסטטיסטית העליונה" — צבר כניסות ואפס תצפיות.

    הלוגיקה זהה לזו של tools/tf_engine.py במצב "נר פסימי": אם נר
    בודד נגע גם בסטופ וגם ביעד, הסטופ מנצח. כך שמספרי ה-forward test
    יהיו בני-השוואה למספרי הבדיקה ההיסטורית ולא נדיבים מהם.

    מעקב בלבד — הרווח מדווח ב-REPORT_LOT_OZ כדי שיהיה באותה סקאלה
    כמו שיטה 2, אבל אין פוזיציה אמיתית ואין מימון (עסקאות שיטה 3
    נסגרות בימים בודדים והן לא נפתחו בפלוס500).
    """
    log = data.get("tf_signals", [])
    open_sigs = [x for x in log if x.get("status") == "open"]
    if not open_sigs or not h1:
        return False

    changed = False
    for sig in open_sigs:
        try:
            t0 = datetime.datetime.fromisoformat(sig["candle"])
        except (KeyError, ValueError, TypeError):
            sig["status"] = "void"
            sig["result"] = "bad_record"
            changed = True
            continue

        entry = sig["entry"]
        stop = sig["stop"]
        target = sig["target"]
        is_long = sig.get("direction") == "קנייה"

        # רק נרות שנסגרו אחרי נר האיתות
        bars = [b for b in h1 if b["t"] > t0]
        if not bars:
            continue
        # 3.9.3: ההיסטוריה מוגבלת ל-800 נרות שעתיים (~33 יום). רשומה
        # ישנה מזה לא ניתנת להכרעה — מסמנים במקום להשאיר "open" לנצח.
        if t0 < h1[0]["t"]:
            sig["status"] = "unresolved"
            sig["result"] = "out_of_history"
            changed = True
            continue

        hit = None
        exit_px = None
        exit_t = None
        for b in bars:
            s_hit = (b["l"] <= stop) if is_long else (b["h"] >= stop)
            t_hit = (b["h"] >= target) if is_long else (b["l"] <= target)
            if s_hit:                      # נר פסימי: הסטופ קודם
                hit, exit_px, exit_t = "loss", stop, b["t"]
                break
            if t_hit:
                hit, exit_px, exit_t = "win", target, b["t"]
                break
        if not hit:
            continue

        pts = (exit_px - entry) if is_long else (entry - exit_px)
        pnl = round((pts - SPREAD_POINTS) * REPORT_LOT_OZ * USD_ILS, 2)
        sig["status"] = "closed"
        sig["result"] = hit
        sig["exit"] = round(exit_px, 2)
        sig["pnl"] = pnl
        sig["points"] = round(pts, 2)
        sig["close_time"] = exit_t.isoformat()
        sig["bars_held"] = sum(1 for b in bars if b["t"] <= exit_t)
        changed = True

        icon = "🎯" if hit == "win" else "🛑"
        send_telegram(
            f"{METHOD_MARK[3]} <b>[מעקב] שיטה 3 — {sig.get('tf')} נסגרה</b> {icon}\n"
            f"{sig.get('direction')} {entry} → {sig['exit']} ({pts:+.1f}$)\n"
            f"💰 {pnl:+.2f} ש\"ח ({REPORT_LOT_OZ}oz תיאורטי)\n"
            f"⏳ {sig['bars_held']} שעות"
            + (" | ✅ היה גיבוי 6H" if sig.get("backup") is True else "")
            + (" | ⚠️ בלי גיבוי 6H" if sig.get("backup") is False else "")
        )
        print(f"[TF-{sig.get('tf')}] נסגרה: {hit} {pnl:+.2f}", flush=True)

    # סיכום מצטבר — הדבר שה-forward test קיים בשבילו
    closed = [x for x in log if x.get("status") == "closed"]
    if changed and len(closed) >= 5:
        w = sum(1 for x in closed if x["result"] == "win")
        tot = round(sum(x.get("pnl", 0) for x in closed), 2)
        print(f"[TF] מצטבר: {len(closed)} עסק' | {100*w/len(closed):.0f}% | "
              f"{tot:+.2f} ש\"ח", flush=True)
    return changed


def tf_scan(data):
    """סורק 4H ו-6H. מעקב בלבד — בלי כפתורים, בלי כסף."""
    symbol = list(SYMBOLS.values())[0]
    h1 = _fetch_history(symbol, "1h", 800)
    if not h1 or len(h1) < 400:
        return

    # 3.9.3: קודם סוגרים מה שפתוח, אחר כך מחפשים חדש.
    tf_closed_any = tf_monitor(data, h1)

    now = now_il().replace(tzinfo=None)
    state = data.setdefault("tf_state", {})
    log = data.setdefault("tf_signals", [])
    changed = False

    for cfg in TF_CONFIGS:
        hrs, name = cfg["hours"], cfg["name"]
        bars = _tf_aggregate(h1, hrs)
        # רק נרות שנסגרו — הנר האחרון עדיין נבנה
        closed = [b for b in bars if b["t"] + datetime.timedelta(hours=hrs) <= now]
        if len(closed) < TF_EMA_PERIOD + TF_BREAKOUT_BARS + 5:
            continue
        last = closed[-1]
        key = last["t"].isoformat()
        if state.get(name) == key:
            continue                      # הנר הזה כבר טופל
        state[name] = key
        changed = True

        closes = [b["c"] for b in closed]
        ema = calc_ema_series(closes, TF_EMA_PERIOD)[-1]
        prior = closed[-(TF_BREAKOUT_BARS + 1):-1]
        hh = max(b["h"] for b in prior)
        ll = min(b["l"] for b in prior)
        c = last["c"]

        direction = None
        if c > hh and c > ema:
            direction = "קנייה"
        elif c < ll and c < ema:
            direction = "מכירה"
        if not direction:
            continue

        atr = _tf_atr(closed)
        if not atr:
            continue
        is_long = direction == "קנייה"
        stop = c - TF_STOP_ATR * atr if is_long else c + TF_STOP_ATR * atr
        target = c + TF_TARGET_ATR * atr if is_long else c - TF_TARGET_ATR * atr
        risk_usd = abs(c - stop)

        # 3.9.1: גודל לפי סיכון לעסקה — לא לפי הירידה המצרפית.
        # הנוסחה הישנה (TF_DD_BASE_OZ * ACCOUNT_SIZE * TF_DD_BUDGET / worst_dd)
        # החזירה ~0.03 אונקיה תמיד, בלי קשר לגודל הסטופ — כלומר בפועל
        # גודל קבוע. הבעיה שהיא הסתירה: ה-ATR של הזהב שולש (2024: 14.2 →
        # 2026: 50.7), ולכן סיכון קבוע-בגודל הוא סיכון-שמשולש-בשקלים.
        max_oz = REPORT_LOT_OZ   # 3.9.2: גודל קבוע — המינימום בפלוס500
        # הסיכון האמיתי אם בכל זאת פותחים את המינימום של פלוס500:
        risk_at_min_ils = (risk_usd + SPREAD_POINTS) * REPORT_LOT_OZ * USD_ILS

        # 3.9.1: גיבוי בין-מסגרתי. איתות 4H בלי איתות 6H מקביל באותו
        # כיוון הוא קבוצה מפסידה שיטתית (37% הצלחה, ‏2,094- ש"ח על 71
        # עסקאות). הבדיקה מסתכלת רק אחורה — הגיבוי שמגיע עד 4 שעות אחרי
        # נבדק בסריקה הבאה ונשלח כעדכון נפרד. אין הצצה לעתיד.
        other = "6H" if name == "4H" else "4H"
        backup = None
        if name == "4H":
            cutoff = now_il() - datetime.timedelta(hours=TF_BACKUP_LOOKBACK_H)
            for s in reversed(log[-40:]):
                if s.get("tf") != other or s.get("direction") != direction:
                    continue
                try:
                    st = datetime.datetime.fromisoformat(s["time"])
                except (ValueError, KeyError):
                    continue
                if st >= cutoff:
                    backup = s
                    break

        log.append({
            "tf": name, "direction": direction, "entry": round(c, 2),
            "stop": round(stop, 2), "target": round(target, 2),
            "atr": round(atr, 2), "candle": key, "risk_usd": round(risk_usd, 2),
            "max_oz": round(max_oz, 3),
            "risk_at_min_ils": round(risk_at_min_ils, 1),
            "backup": bool(backup) if name == "4H" else None,
            "time": now_il().isoformat(), "status": "open"
        })

        # אם זה איתות 6H — יש איתות 4H מהשעות האחרונות שחיכה לגיבוי?
        if name == "6H":
            since = now_il() - datetime.timedelta(hours=TF_BACKUP_WINDOW_H)
            for s in reversed(log[-40:]):
                if (s.get("tf") == "4H" and s.get("direction") == direction
                        and s.get("backup") is False):
                    try:
                        st = datetime.datetime.fromisoformat(s["time"])
                    except (ValueError, KeyError):
                        continue
                    if st >= since:
                        s["backup"] = True
                        s["backup_late_h"] = round(
                            (now_il() - st).total_seconds() / 3600.0, 1)
                        send_telegram(
                            f"🟧 <b>[שיטה 3] גיבוי 6H הגיע</b>\n"
                            f"האיתות של 4H מ-{st.strftime('%d/%m %H:%M')} "
                            f"({s['direction']} @{s['entry']}) קיבל גיבוי 6H "
                            f"באותו כיוון אחרי {s['backup_late_h']} שעות.\n"
                            f"הוא עובר מקבוצת <b>37%</b> לקבוצת <b>62%</b>."
                        )
                        break

        # 3.9.1: סימון גיבוי 6H — הדבר הראשון שרואים באיתות 4H.
        backup_line = ""
        if name == "4H":
            if backup:
                backup_line = (
                    f"✅ <b>יש גיבוי 6H</b> באותו כיוון "
                    f"(מ-{backup['time'][11:16]}) — קבוצת <b>62%</b> הצלחה, "
                    f"‏24.3+ ש\"ח לעסקה.\n")
            else:
                backup_line = (
                    f"⚠️ <b>אין גיבוי 6H — קבוצת 37% הצלחה.</b>\n"
                    f"‏71 עסקאות כאלה ב-2.5 שנים: ‏2,094- ש\"ח, "
                    f"‏29.5- לעסקה, שליליות בכל שלוש השנים (p=0.02-0.003).\n"
                    f"גיבוי עוד יכול להגיע עד {TF_BACKUP_WINDOW_H} שעות — "
                    f"תישלח התראה אם כן.\n")

        msg = (
            f"{TF_SIGNAL_OPEN}\n"
            f"{METHOD_MARK[3]} <b>[מעקב בלבד] שיטה 3 — מסגרת {name}</b>\n"
            f"🕐 {now_il().strftime('%d/%m %H:%M')} | נר {name} שנסגר\n"
            f"{'─' * 22}\n"
            f"{backup_line}"
            f"📊 כיוון: <b>{direction}</b>\n"
            f"💰 כניסה: {c:.2f}\n"
            f"🛑 סטופ: {stop:.2f}  ({risk_usd:.1f}$)\n"
            f"🎯 יעד: {target:.2f} ({abs(target-c):.1f}$ = 2×ATR)\n"
            f"📏 ATR({TF_ATR_PERIOD}) = {atr:.2f}$\n"
            f"{'─' * 22}\n"
            f"⚖️ <b>גודל וסיכון — שקלים אמיתיים</b>\n"
            f"גודל: <b>{REPORT_LOT_OZ} אונקיה</b> (המינימום בפלוס500)\n"
            f"🔴 <b>הסיכון האמיתי: {risk_at_min_ils:.0f} ש\"ח</b> "
            f"({100*risk_at_min_ils/ACCOUNT_SIZE:.0f}% מחשבון של {ACCOUNT_SIZE})\n"
        )
        if risk_at_min_ils > ACCOUNT_SIZE * 0.05:
            msg += (f"⚠️ מעל 5% מהחשבון בעסקה אחת. "
                    f"לסיכון סביר דרוש ~{risk_at_min_ils/0.05:,.0f} ש\"ח.\n")
        msg += (
            f"{'─' * 22}\n"
            f"📈 בבדיקה (2.5 שנים): {cfg['far']['trades']} עסק' | "
            f"{cfg['far']['pnl']} ש\"ח | {cfg['far']['periods']} תקופות חיוביות\n"
            f"⏳ החזקה: ימים, לא שעות.\n"
            f"⚠️ המספרים לא שוחזרו במלואם ע\"י tools/tf_engine.py — "
            f"ר' 3.9.1. הכיוון מאושר, הגודל לא.\n"
            f"⚠️ מדגם דק. Forward test — בלי כסף, בלי לשנות פרמטרים.\n"
            f"<i>מעקב בלבד — לא למסחר.</i>\n"
            f"{TF_SIGNAL_CLOSE}"
        )
        send_telegram(msg)
        print(f"[TF-{name}] איתות: {direction} @{c:.2f} | סטופ {stop:.2f} | יעד {target:.2f}", flush=True)

    if changed or tf_closed_any:
        save_data(data)


def slow_scan_and_monitor(data):
    """3.5.0: הלב החי של שיטה 2 — רץ בכל סריקה (10 דק').

    על נר 4 שעות סגור חדש: עדכון סטופ נגרר לעסקה פתוחה, או בדיקת פריצה
    לאיתות חדש (רק כשאין עסקה/איתות ממתין — עסקה אחת בכל רגע).
    בכל סריקה: בדיקה אם המחיר חצה את הסטופ הנגרר → סגירה אוטומטית בזיהוי (כמו 3.4).
    """
    symbol_name = list(SYMBOLS.keys())[0]
    symbol = SYMBOLS[symbol_name]
    now = now_il().replace(tzinfo=None)
    ew = SLOW_ENTRY_DAYS * CANDLES_PER_DAY_4H
    tw = SLOW_TRAIL_DAYS * CANDLES_PER_DAY_4H
    h4 = _fetch_history(symbol, "4h", ew + 40)
    if not h4 or len(h4) < ew + 2:
        print("[SLOW] אין מספיק נרות 4h", flush=True)
        return
    closed_candles = [c for c in h4 if c["t"] + datetime.timedelta(hours=4) <= now]
    if len(closed_candles) < ew + 1:
        return
    last_closed = closed_candles[-1]
    current_price = h4[-1]["c"]  # הנר האחרון (גם אם בבנייה) = המחיר העדכני
    state = data.setdefault("slow_state", {})
    spread_pts = _spread_points()

    open_trade = next((t for t in data.get("trades", [])
                       if t.get("system") == 2 and t.get("status") == "open"), None)

    # --- 3.6.0: shadow שיטה 2, ניטור בכל סריקה — רישום בלבד, לא שולח כלום ---
    # עוקב אחרי כל איתות פריצה כאילו נכנס, בלי תלות בכפתורים/עסקה אמיתית.
    ssh = data.setdefault("slow_shadow", [])
    sh_open = next((s for s in ssh if s.get("status") == "open"), None)
    if sh_open:
        _sl = sh_open["direction"] == "קנייה"
        if (_sl and current_price <= sh_open["stop"]) or ((not _sl) and current_price >= sh_open["stop"]):
            _xp = sh_open["stop"]
            _pts = (_xp - sh_open["entry"]) if _sl else (sh_open["entry"] - _xp)
            _pnl = (_pts - spread_pts) * sh_open.get("ils_per_pt", ils_per_point())
            sh_open["status"] = "closed"
            sh_open["result"] = "win" if _pnl > 0 else "loss"
            sh_open["pnl"] = round(_pnl, 2)
            sh_open["exit"] = round(_xp, 2)
            sh_open["close_time"] = now.isoformat()
            save_data(data)
            print(f"[SLOW-SHADOW] נסגרה: {sh_open['result']} {_pnl:+.1f}", flush=True)

    # --- ניטור עסקה פתוחה: חציית סטופ נגרר → סגירה אוטומטית ---
    if open_trade:
        is_long = open_trade["direction"] == "קנייה"
        # 3.8.0 — ברייקאיבן: אחרי תנועה של TRIGGER$ לטובתנו, הסטופ קופץ
        # לכניסה + OFFSET. מתהדק בלבד; לא נוגע בסטופ נגרר שכבר עבר אותו.
        if SLOW_BE_TRIGGER is not None and not open_trade.get("be_hit"):
            _mfe = (current_price - open_trade["entry"]) if is_long else (open_trade["entry"] - current_price)
            if _mfe >= SLOW_BE_TRIGGER:
                _be = round(open_trade["entry"] + SLOW_BE_OFFSET, 2) if is_long else round(open_trade["entry"] - SLOW_BE_OFFSET, 2)
                open_trade["be_hit"] = True
                _improves = (is_long and _be > open_trade["stop"]) or ((not is_long) and _be < open_trade["stop"])
                if _improves:
                    _old = open_trade["stop"]
                    open_trade["stop"] = _be
                    send_telegram(
                        f"🔒 <b>ברייקאיבן — עסקה {fmt_tn(open_trade['number'])}</b>\n"
                        f"המחיר זז {_mfe:.1f}$ לטובתנו. הסטופ עלה לכניסה +{SLOW_BE_OFFSET:.0f}$.\n"
                        f"סטופ חדש: <b>{open_trade['stop']}</b> (היה {round(_old, 2)})\n"
                        f"מכאן העסקה לא יכולה להפסיד. (עדכן גם בפלוס500)"
                    )
                    print(f"[SLOW] ברייקאיבן: {_old} → {open_trade['stop']}", flush=True)
                save_data(data)
        trail = open_trade["stop"]
        crossed = (is_long and current_price <= trail) or ((not is_long) and current_price >= trail)
        if crossed:
            exit_px = trail
            pts = (exit_px - open_trade["entry"]) if is_long else (open_trade["entry"] - exit_px)
            _tunits = open_trade.get("units") or [{"e": open_trade["entry"]}]
            # 3.9.2: הכל בגודל אמיתי (0.75oz ליחידה), כולל מימון לילה.
            try:
                _et = datetime.datetime.fromisoformat(open_trade["entry_time"]).replace(tzinfo=None)
                _held = max(0.0, (now - _et).total_seconds() / 86400.0)
            except (ValueError, KeyError, TypeError):
                _held = 0.0
            _gross = 0.0
            for _u in _tunits:
                _pu = (exit_px - _u["e"]) if is_long else (_u["e"] - exit_px)
                _gross += (_pu - spread_pts) * REPORT_LOT_OZ * USD_ILS
            _fund = funding_cost_ils(REPORT_LOT_OZ * len(_tunits), _held)
            pnl = _gross - _fund
            result = "win" if pnl > 0 else "loss"
            _finalize_trade(data, open_trade, result, pnl)
            save_data(data)
            icon = "✅" if result == "win" else "🛑"
            keyboard = [[{"text": "👍 קיבלתי", "callback_data": f"ok_{open_trade['id']}"}]]
            send_telegram(
                f"{icon} <b>עסקה {fmt_tn(open_trade['number'])} — הסטופ הנגרר נחצה. נסגרה אוטומטית.</b>\n"
                f"{symbol_name} | מחיר: {round(current_price, 2)} | יציאה: {round(exit_px, 2)}\n"
                f"💰 <b>{pnl:+.2f} ש\"ח</b>  "
                f"({len(_tunits)}×{REPORT_LOT_OZ}oz, שקלים אמיתיים)\n"
                f"   ברוטו {_gross:+.2f} | מימון {-_fund:.2f} ({_held:.1f} ימים)\n"
                + (f"(סגור את <b>שתי</b> הפוזיציות בפלוס500 עכשיו — אשר קבלה 👇)"
                   if len(_tunits) > 1 else f"(סגור גם בפלוס500 עכשיו — אשר קבלה 👇)"),
                keyboard
            )
            print(f"[SLOW] עסקה {open_trade['number']} נסגרה: {result} {pnl:+.1f}", flush=True)
            open_trade = None

    # --- דברים שקורים רק על נר סגור חדש ---
    last_key = last_closed["t"].isoformat()
    if state.get("last_candle") == last_key:
        return
    state["last_candle"] = last_key

    # 3.6.0: shadow שיטה 2 על נר סגור — רץ תמיד, גם כשיש עסקה אמיתית פתוחה
    sh_open = next((s for s in data.get("slow_shadow", []) if s.get("status") == "open"), None)
    if sh_open:
        _win = closed_candles[-tw:]
        _sl = sh_open["direction"] == "קנייה"
        _nt = min(x["l"] for x in _win) if _sl else max(x["h"] for x in _win)
        if (_sl and _nt > sh_open["stop"]) or ((not _sl) and _nt < sh_open["stop"]):
            sh_open["stop"] = round(_nt, 2)
            save_data(data)
    else:
        _prior = closed_candles[-(ew + 1):-1]
        _hh = max(x["h"] for x in _prior)
        _ll = min(x["l"] for x in _prior)
        _d = "קנייה" if last_closed["c"] > _hh else ("מכירה" if last_closed["c"] < _ll else None)
        if _d:
            _e = round(last_closed["c"], 2)
            _tw2 = closed_candles[-tw:]
            _tr = round(min(x["l"] for x in _tw2) if _d == "קנייה" else max(x["h"] for x in _tw2), 2)
            _sp = abs(_e - _tr)
            if _sp > 1e-6:
                data.setdefault("slow_shadow", []).append({
                    "direction": _d, "entry": _e, "stop": _tr,
                    "ils_per_pt": round(ils_per_point(), 4),
                    "entry_time": now.isoformat(), "candle": last_key,
                    "status": "open"
                })
                save_data(data)
                print(f"[SLOW-SHADOW] נפתחה: {_d} @{_e} trail {_tr}", flush=True)

    if open_trade:
        is_long = open_trade["direction"] == "קנייה"

        # 3.9.0: פירמידינג — הוספת יחידה 2 על סגירת נר מעבר לטריגר.
        # שמרני כמו בסימולציה: סגירת נר בלבד, לא תוך-נר. מחיר התוספת
        # נרשם כמחיר הטריגר (כמו במנוע); הכניסה בפועל תהיה סביבו.
        _units = open_trade.setdefault("units", [{"e": open_trade["entry"]}])
        _atrg = open_trade.get("add_trigger")
        if (SLOW_PYRAMID_UNITS > 1 and _atrg is not None
                and len(_units) < SLOW_PYRAMID_UNITS):
            _crossed_up = is_long and last_closed["c"] >= _atrg
            _crossed_dn = (not is_long) and last_closed["c"] <= _atrg
            if _crossed_up or _crossed_dn:
                _units.append({"e": _atrg, "added_time": now.isoformat()})
                open_trade["add_trigger"] = None   # יחידה אחת נוספת בלבד
                save_data(data)
                send_telegram(
                    f"🪜 <b>פירמידינג — עסקה {fmt_tn(open_trade['number'])}</b>\n"
                    f"נר 4 שעות נסגר {'מעל' if is_long else 'מתחת'} טריגר ההוספה ({_atrg}).\n"
                    f"👉 <b>פתח עכשיו פוזיציה שנייה: {SLOW_LOT_OZ} אונקיה, "
                    f"{'קנייה' if is_long else 'מכירה'}</b>\n"
                    f"🛑 סטופ לפוזיציה החדשה: <b>{open_trade['stop']}</b> — זהה לראשונה.\n"
                    f"מעכשיו כל עדכון סטופ חל על <b>שתי</b> הפוזיציות בפלוס500."
                )
                print(f"[SLOW] פירמידינג: יחידה 2 @{_atrg}", flush=True)

        # עדכון סטופ נגרר — מתהדק בלבד
        window = closed_candles[-tw:]
        new_trail = min(x["l"] for x in window) if is_long else max(x["h"] for x in window)
        old_trail = open_trade["stop"]
        tightened = (is_long and new_trail > old_trail) or ((not is_long) and new_trail < old_trail)
        if tightened and abs(new_trail - old_trail) >= 1.0:
            open_trade["stop"] = round(new_trail, 2)
            save_data(data)
            locked = (new_trail - open_trade["entry"]) if is_long else (open_trade["entry"] - new_trail)
            send_telegram(
                f"🔃 <b>סטופ נגרר עודכן — עסקה {fmt_tn(open_trade['number'])}</b>\n"
                f"סטופ חדש: <b>{open_trade['stop']}</b> (היה {round(old_trail, 2)})\n"
                f"מחיר נוכחי: {round(current_price, 2)} | "
                + (f"🔒 נעול רווח: {points_to_ils(locked) and ''}{locked:+.1f} נק'" if locked > 0 else f"מרחק מהכניסה: {locked:+.1f} נק'") + "\n"
                + (f"(עדכן את הסטופ ב<b>שתי</b> הפוזיציות בפלוס500)"
                   if len(open_trade.get("units") or []) > 1
                   else f"(עדכן את הסטופ גם בפלוס500)")
            )
            print(f"[SLOW] trail {old_trail} → {open_trade['stop']}", flush=True)
        save_data(data)
        return

    # --- אין עסקה: בדיקת איתות פריצה על הנר הסגור החדש ---
    slow_pending = {k: v for k, v in data.get("pending", {}).items() if v.get("system") == 2}
    # ניקוי איתותים איטיים שפג תוקפם
    cutoff = (now - datetime.timedelta(hours=SLOW_PENDING_HOURS)).isoformat()
    for k, v in list(slow_pending.items()):
        if v.get("time", "") < cutoff:
            data["pending"].pop(k, None)
            slow_pending.pop(k, None)
    if slow_pending:
        return  # יש איתות ממתין — לא שולחים חדש

    prior = closed_candles[-(ew + 1):-1]
    hh = max(x["h"] for x in prior)
    ll = min(x["l"] for x in prior)
    direction = None
    if last_closed["c"] > hh:
        direction = "קנייה"
    elif last_closed["c"] < ll:
        direction = "מכירה"
    if not direction:
        return

    is_long = direction == "קנייה"
    entry_px = round(last_closed["c"], 2)
    t_win = closed_candles[-tw:]
    trail0 = round(min(x["l"] for x in t_win) if is_long else max(x["h"] for x in t_win), 2)
    stop_pts = abs(entry_px - trail0)
    if stop_pts < 1e-6:
        return
    # 3.9.2: הדיווח בגודל אמיתי. הסיכון הוא תוצאה של רוחב הסטופ,
    # לא קלט — כי הגודל קבוע על המינימום של פלוס500.
    ils_per_pt = ils_per_point()
    risk_real = (stop_pts + SPREAD_POINTS) * REPORT_LOT_OZ * USD_ILS
    risk_pct = 100.0 * risk_real / ACCOUNT_SIZE if ACCOUNT_SIZE else 0.0

    # 3.9.0: פירמידינג — ATR14 על 4h בזמן הכניסה קובע את טריגר ההוספה.
    # עבר בלבד (נרות סגורים), כמו בסימולציה.
    add_trigger = None
    n_atr = None
    if SLOW_PYRAMID_UNITS > 1 and len(closed_candles) >= 20:
        _w = closed_candles[-30:]
        n_atr = calc_atr([x["h"] for x in _w], [x["l"] for x in _w],
                         [x["c"] for x in _w])
        if n_atr:
            _step = SLOW_PYRAMID_STEP_N * n_atr
            add_trigger = round(entry_px + _step if is_long else entry_px - _step, 2)

    data["slow_seq"] = data.get("slow_seq", 0) + 1
    num = f"איטי #{data['slow_seq']}"
    trade_id = make_trade_id("SLOW", now.strftime("%d%H%M"))
    data.setdefault("pending", {})[trade_id] = {
        "number": num, "symbol": symbol_name, "direction": direction,
        "entry": entry_px, "stop": trail0, "target1": None, "target2": None,
        "be_hit": False,
        "units": [{"e": entry_px}],          # 3.9.0: יחידות הפוזיציה (0.75oz כ"א)
        "add_trigger": add_trigger,           # 3.9.0: מחיר ההוספה; None = אין
        "n_atr": round(n_atr, 2) if n_atr else None,
        "stars": None, "system": 2, "ils_per_pt": round(ils_per_pt, 4),
        "time": now.isoformat()
    }
    keyboard = [[
        {"text": "✅ נכנסתי", "callback_data": f"en_{trade_id}"},
        {"text": "❌ דילגתי", "callback_data": f"sk_{trade_id}"}
    ]]
    send_telegram(
        f"{sig_open(2)}\n"
        f"🐢 <b>איתות שיטה 2 — {num} — {symbol_name}</b>\n"
        f"🕐 {now.strftime('%d/%m %H:%M')} | נר 4 שעות נסגר "
        f"{'מעל שיא' if is_long else 'מתחת שפל'} {SLOW_ENTRY_DAYS} ימים\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📊 כיוון: <b>{direction} {'🟢' if is_long else '🔴'}</b>\n"
        f"💰 כניסה: סביב <b>{entry_px}</b> — יחידה 1: <b>{SLOW_LOT_OZ} אונקיה</b>\n"
        f"🛑 סטופ נגרר התחלתי: <b>{trail0}</b> ({stop_pts:.1f} נק')\n"
        f"🎯 טארגט: אין — יוצאים רק כשהסטופ הנגרר נחצה\n"
        + (f"🪜 טריגר להוספה (יחידה 2, {SLOW_LOT_OZ} אונקיה): נר 4ש' נסגר "
           f"{'מעל' if is_long else 'מתחת'} <b>{add_trigger}</b> — תישלח התראה\n"
           if add_trigger else "")
        + f"💸 <b>הסיכון האמיתי: {risk_real:.0f} ש\"ח</b> ({risk_pct:.0f}% מהחשבון) "
        f"— {REPORT_LOT_OZ}oz × {stop_pts:.1f}$ + ספרד\n"
        f"💸 מימון לילה: {FUNDING_ILS_PER_OZ_DAY * SLOW_LOT_OZ:.2f} ש\"ח ליום "
        f"ליחידה (נמדד)\n"
        f"⏳ החזקה צפויה: ימים עד שבועות | תוקף האיתות: {SLOW_PENDING_HOURS} שעות\n"
        f"━━━━━━━━━━━━━━━\n"
        f"(פותחים {SLOW_LOT_OZ} אונקיה בפלוס500 — לא יותר. ההוספה רק בהתראה.)\n"
        f"{sig_close(2)}",
        keyboard
    )
    save_data(data)
    print(f"[SLOW] ✅ איתות {num}: {direction} @{entry_px} trail {trail0}", flush=True)

def run_slow_report():
    """דוח שיטה 2: 9 קומבינציות + עמידות לטובה + וריאנט טארגט 5 נק' להשוואה."""
    symbol = list(SYMBOLS.values())[0]
    h4 = _fetch_history(symbol, "4h", 3000)
    if not h4 or len(h4) < 400:
        return ["⚠️ משיכת נרות 4 שעות נכשלה או קצרה מדי. נסה שוב מאוחר יותר."]
    span_days = (h4[-1]["t"] - h4[0]["t"]).days
    lines = ["🐢 <b>שיטה 2 — עוקב מגמה איטי (נר 4 שעות)</b>",
             f"תקופת בדיקה: ~{span_days} ימים ({len(h4)} נרות) | סיכון {SLOW_RISK_ILS:.0f} ש\"ח לעסקה",
             "כניסה: פריצת N ימים | יציאה: סטופ נגרר M ימים | עסקה אחת בכל רגע", ""]
    combos = []
    for n_days in (10, 15, 20):
        for m_days in (3, 5, 7):
            r = _simulate_slow(h4, entry_days=n_days, trail_days=m_days)
            combos.append(((n_days, m_days), r))
            lines.append(f"פריצה {n_days}י'/נגרר {m_days}י': {r['trades']} עסק' | {r['win_rate']} | "
                         f"{r['pnl']:+.0f} ש\"ח | ממוצע ✅{r['avg_win']:+.0f}/❌{r['avg_loss']:+.0f} | {r['avg_days']}ימ'")
    lines.append("")
    pos_combos = [c for c in combos if c[1]["pnl"] > 0]
    lines.append(f"📊 קומבינציות רווחיות: {len(pos_combos)} מתוך {len(combos)}")
    (bn, bm), best = max(combos, key=lambda x: x[1]["pnl"])
    lines.append(f"🧪 <b>עמידות לטובה</b> (פריצה {bn}י'/נגרר {bm}י', בסיס {best['pnl']:+.0f}):")
    for slip in (3.0, 6.0, 10.0):
        rs = _simulate_slow(h4, entry_days=bn, trail_days=bm, slippage_points=slip)
        lines.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח")
    lines.append("")
    # וריאנט המשתמש להשוואה: אותן כניסות, טארגט קבוע 5 נק'
    r5 = _simulate_slow(h4, entry_days=bn, trail_days=bm, fixed_target_points=5.0)
    r5s = _simulate_slow(h4, entry_days=bn, trail_days=bm, fixed_target_points=5.0, slippage_points=3.0)
    lines.append("🔬 <b>להשוואה — \"טארגט 5 נקודות\"</b> (אותן כניסות, סוגר ב-5+ נק'):")
    lines.append(f"  בלי רעש: {r5['trades']} עסק' | {r5['win_rate']} | {r5['pnl']:+.0f} ש\"ח")
    lines.append(f"  עם 3 נק' רעש: {r5s['win_rate']} | {r5s['pnl']:+.0f} ש\"ח")
    lines.append("")
    lines.append("💡 ממוצע ✅/❌ = רווח/הפסד ממוצע לעסקה | ימ' = משך החזקה ממוצע בימים")
    text = "\n".join(lines)
    msgs = []
    while len(text) > 3800:
        cut = text.rfind("\n", 0, 3800)
        if cut <= 0:
            break
        msgs.append(text[:cut])
        text = text[cut + 1:]
    msgs.append(text)
    return msgs

def run_h9_backtest():
    """‏/h9 — חלון הפעילות (שיטה 1). ההנחה היחידה שמעולם לא נבדקה:
    הבוט ער 08:00-21:00 וחוסם כניסות מ-19:00. שני המהלכים הגדולים של
    28-29/07 קרו כשהוא כבוי. כאן בודקים חלונות רחבים יותר — כולל 24 שעות.
    שים לב: הבסיס כאן הוא החלון החי (8-22 עם חיתוך 19:00), ולכן ההשוואה
    היא 'מה היה קורה אילו הבוט היה ער', לא עוד פילטר כניסות."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return ["⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."]

    # כמה מהנרות בכלל קיימים מחוץ לחלון? (בדיקת שפיות לנתונים)
    night = sum(1 for c in m15 if not (8 <= c["t"].hour < 22))
    date_from, date_to = m15[0]["t"].strftime("%d/%m"), m15[-1]["t"].strftime("%d/%m")
    # 3.6.5: יעד/סטופ קבועים נכנסים לבסיס כדי שהמנוע יריץ את הלוגיקה החיה
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)

    base = _simulate(m15, h1, **live)
    parts = [f"🌙 <b>/h9 — חלון הפעילות | {date_from} עד {date_to}</b>\n"
             f"נרות מחוץ לחלון החי בנתונים: {night} מתוך {len(m15)}\n\n"
             f"<b>⚙️ בסיס (החלון החי: 08-22, חיתוך {LAST_ENTRY_HOUR}:00):</b>\n"
             f"🔔 {base['trades']} עסק' | 📊 {base['win_rate']} | 💰 {base['pnl']:+.2f} ש\"ח\n"]

    variants = []
    lines = ["<b>🕐 גריד חלונות (חלון פעיל / שעת חיתוך):</b>"]
    grid = [
        ("8-22, חיתוך 21", dict(active_start=8, active_end=22, last_entry_hour=21)),
        ("8-24, חיתוך 22", dict(active_start=8, active_end=24, last_entry_hour=22)),
        ("6-24, חיתוך 22", dict(active_start=6, active_end=24, last_entry_hour=22)),
        ("24 שעות, חיתוך 22", dict(active_start=0, active_end=24, last_entry_hour=22)),
        ("24 שעות, בלי חיתוך", dict(active_start=0, active_end=24, last_entry_hour=None)),
        ("14-24 (אחה\"צ+ערב)", dict(active_start=14, active_end=24, last_entry_hour=None)),
    ]
    for label, kw in grid:
        full = dict(live); full.update(kw)
        r = _simulate(m15, h1, **full)
        variants.append((label, kw, r))
        lines.append(f"  {label}: {r['trades']} עסק' | {r['win_rate']} | "
                     f"{r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f})")
    parts.append("\n".join(lines))

    best_label, best_kw, best_r = max(variants, key=lambda x: x[2]["pnl"])
    verdict = []
    if best_r["pnl"] > base["pnl"]:
        verdict.append(f"\n🧪 <b>מבחן עמידות לחלון הטוב ({best_label}):</b>")
        robust = True
        for slip in (3.0, 6.0, 10.0):
            full = dict(live); full.update(best_kw); full["slippage_points"] = slip
            rs = _simulate(m15, h1, **full)
            verdict.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח "
                           f"({rs['pnl'] - base['pnl']:+.0f} מול בסיס ללא הזזה)")
            if rs["pnl"] <= base["pnl"]:
                robust = False
        verdict.append("\n✅ שורד עמידות — מועמד ליישום." if robust
                       else "\n⚠️ קורס תחת הזזה — יתרון לא אמיתי.")
        verdict.append("⚠️ שים לב: הרחבת שעות דורשת גם שהבוט ירוץ בפועל בשעות האלה "
                       "(שינוי בקוד החי) — ושהספרד בלילה לרוב רחב יותר מהמודל כאן.")
    else:
        verdict.append("\n❌ אף חלון מורחב לא שיפר את הבסיס — שעות הפעילות אינן הבעיה.")
    parts.append("\n".join(verdict))
    return parts

def run_h8_backtest():
    """‏/h8 — השערה 8: פירמידינג בסגנון טרטלס על שיטה 2.
    בסיס = הקומבינציה החיה (20/3, יחידה אחת). גריד: 2/3/4 יחידות בצעד
    0.5N, ואז הטוב שבהם בצעד 1.0N. עמידות 3/6/10 נק' לטוב ביותר.
    פירמידינג מכפיל סיכון — לכן מדווחים גם הפסד-מצרפי-גרוע וכמות
    יחידות ממוצעת/מקס'. מחזירה רשימת הודעות."""
    symbol = list(SYMBOLS.values())[0]
    h4 = _fetch_history(symbol, "4h", 3000)
    if not h4 or len(h4) < 400:
        return ["⚠️ משיכת נרות 4 שעות נכשלה או קצרה מדי. נסה שוב מאוחר יותר."]
    span_days = (h4[-1]["t"] - h4[0]["t"]).days

    base = _simulate_slow(h4, entry_days=SLOW_ENTRY_DAYS, trail_days=SLOW_TRAIL_DAYS)
    parts = [f"🧪 <b>/h8 — פירמידינג (שיטה 2) | ~{span_days} ימים, {len(h4)} נרות</b>\n"
             f"מוסיפים יחידה ({SLOW_RISK_ILS:.0f} ש\"ח סיכון) כל פעם שנר נסגר "
             f"צעד×N מעבר לתוספת האחרונה (N=ATR14). כל היחידות יוצאות יחד על הנגרר.\n\n"
             f"<b>⚙️ בסיס (20/3, יחידה אחת):</b>\n"
             f"🔔 {base['trades']} עסק' | 📊 {base['win_rate']} | 💰 {base['pnl']:+.0f} ש\"ח\n"
             f"ממוצע ✅{base['avg_win']:+.0f}/❌{base['avg_loss']:+.0f} | "
             f"הפסד מצרפי גרוע: {base['worst']:+.0f} ש\"ח\n"]

    variants = []
    lines = ["<b>🔺 גריד יחידות (צעד 0.5N):</b>"]
    for u in (2, 3, 4):
        kw = dict(pyramid_units=u, pyramid_step_n=0.5)
        r = _simulate_slow(h4, entry_days=SLOW_ENTRY_DAYS, trail_days=SLOW_TRAIL_DAYS, **kw)
        variants.append((f"{u} יח' @0.5N", kw, r))
        lines.append(f"  {u} יח': {r['win_rate']} | {r['pnl']:+.0f} ש\"ח ({r['pnl'] - base['pnl']:+.0f}) | "
                     f"גרוע: {r['worst']:+.0f} | יח' בפועל: ממוצע {r['avg_units']}, מקס' {r['max_units']}")
    best_u_label, best_u_kw, best_u_r = max(variants, key=lambda x: x[2]["pnl"])
    kw_1n = dict(best_u_kw); kw_1n["pyramid_step_n"] = 1.0
    r1n = _simulate_slow(h4, entry_days=SLOW_ENTRY_DAYS, trail_days=SLOW_TRAIL_DAYS, **kw_1n)
    variants.append((best_u_label.replace("@0.5N", "@1.0N"), kw_1n, r1n))
    lines.append(f"  צעד 1.0N ({best_u_kw['pyramid_units']} יח'): {r1n['win_rate']} | {r1n['pnl']:+.0f} ש\"ח "
                 f"({r1n['pnl'] - base['pnl']:+.0f}) | גרוע: {r1n['worst']:+.0f}")
    parts.append("\n".join(lines))

    best_label, best_kw, best_r = max(variants, key=lambda x: x[2]["pnl"])
    verdict = []
    if best_r["pnl"] > base["pnl"]:
        verdict.append(f"\n🧪 <b>מבחן עמידות לטוב ({best_label}):</b>")
        robust = True
        for slip in (3.0, 6.0, 10.0):
            rs = _simulate_slow(h4, entry_days=SLOW_ENTRY_DAYS, trail_days=SLOW_TRAIL_DAYS,
                                slippage_points=slip, **best_kw)
            verdict.append(f"  ‏{slip:.0f} נק' נגד: {rs['win_rate']} | {rs['pnl']:+.0f} ש\"ח | גרוע: {rs['worst']:+.0f}")
            if rs["pnl"] <= base["pnl"]:
                robust = False
        verdict.append("\n✅ שורד עמידות — מועמד ליישום." if robust
                       else "\n⚠️ קורס תחת הזזה — יתרון לא אמיתי.")
        verdict.append("⚠️ לפני יישום: ההפסד המצרפי הגרוע הוא הסיכון האמיתי לעסקה — "
                       "ודא שהוא מתאים לחשבון (כל יחידה = עוד 40 ש\"ח סיכון).")
    else:
        verdict.append("\n❌ אף וריאנט פירמידינג לא שיפר את הבסיס — ההשערה לא מאושרת על המדגם.")
    parts.append("\n".join(verdict))
    return parts

# ============================================================
# 3.4.2: הצלבת מנוע מול מציאות — פקודת /cross בטלגרם
# רקע: המנוע מראה +402 על חודש בעוד המציאות מדממת (623-, 21%).
# ההצלבה מדפיסה יום-מול-יום: מה המנוע מדמה (זמן/כיוון/מחיר/תוצאה)
# מול העסקאות האמיתיות מה-state. קריאה וחישוב בלבד.
# ============================================================
def run_cross_check(data):
    """מחזיר רשימת הודעות: עסקאות המנוע מול העסקאות האמיתיות, 4 ימי מסחר אחרונים."""
    symbol = list(SYMBOLS.values())[0]
    m15 = _fetch_history(symbol, "15min", 2900)
    h1 = _fetch_history(symbol, "1h", 800)
    if not m15 or not h1 or len(m15) < 200 or len(h1) < 100:
        return ["⚠️ לא הצלחתי למשוך מספיק נתונים היסטוריים. נסה שוב מאוחר יותר."]
    # 3.6.5: יעד/סטופ קבועים נכנסים לבסיס כדי שהמנוע יריץ את הלוגיקה החיה
    live = dict(stop_floor_pct=STOP_FLOOR_PCT, max_stretch_pct=MAX_STRETCH_PCT,
                rsi_extreme_block=True, last_entry_hour=LAST_ENTRY_HOUR,
                fixed_target_pts=FIXED_TARGET_USD, fixed_stop_pts=FIXED_STOP_USD)
    r = _simulate(m15, h1, **live)
    detail = r.get("detail", [])
    days = sorted({c["t"].strftime("%Y-%m-%d") for c in m15})[-4:]
    icon = {"win": "✅", "loss": "❌", "timeout": "⏰", "be": "🤝"}
    arrow = {"long": "🟢קנייה", "short": "🔴מכירה"}
    lines = ["🔬 <b>הצלבה: מנוע backtest מול המציאות</b>",
             "לכל יום — מה המנוע מדמה מול מה שנרשם בבוט החי", ""]
    for day in days:
        sim_day = [c for c in detail if c.get("et") is not None
                   and c["et"].strftime("%Y-%m-%d") == day]
        sim_day.sort(key=lambda c: c["et"])
        sim_pnl = round(sum(c["pnl"] for c in sim_day), 2)
        d_disp = datetime.datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m")
        lines.append(f"═══ {d_disp} ═══")
        lines.append(f"🤖 <b>המנוע</b> — {len(sim_day)} עסק' | {sim_pnl:+.0f} ש\"ח:")
        if sim_day:
            for c in sim_day:
                lines.append(f"  {icon.get(c['result'],'▫️')} {c['et'].strftime('%H:%M')} "
                             f"{arrow.get(c['dir'],'?')} @{c['entry']:.1f} ← {c['pnl']:+.0f}")
        else:
            lines.append("  (אין עסקאות)")
        real_day = [t for t in data.get("trades", [])
                    if str(t.get("entry_time", "")).startswith(day)]
        real_day.sort(key=lambda t: t.get("entry_time", ""))
        day_pnl = data.get("daily_stats", {}).get(day, {}).get("pnl")
        pnl_txt = f"{day_pnl:+.0f} ש\"ח" if isinstance(day_pnl, (int, float)) else "—"
        lines.append(f"👤 <b>הבוט החי</b> — {len(real_day)} עסק' | {pnl_txt}:")
        if real_day:
            for t in real_day:
                try:
                    hh = datetime.datetime.fromisoformat(t["entry_time"]).strftime("%H:%M")
                except Exception:
                    hh = "?"
                dr = "🟢קנייה" if t.get("direction") == "קנייה" else "🔴מכירה"
                pnl_v = t.get("pnl")
                pnl_s = f"{pnl_v:+.0f}" if isinstance(pnl_v, (int, float)) else "פתוחה"
                lines.append(f"  {icon.get(t.get('result'),'▫️')} {hh} {dr} @{t.get('entry',0):.1f} ← {pnl_s}")
        else:
            lines.append("  (אין עסקאות)")
        lines.append("")
    lines.append("🔎 קריאה: זמנים/כיוונים/מחירים שונים = המנוע רואה שוק אחר;")
    lines.append("עסקאות דומות עם תוצאות שונות = הבעיה בסימולציית הסגירה")
    text = "\n".join(lines)
    msgs = []
    while len(text) > 3800:
        cut = text.rfind("\n", 0, 3800)
        if cut <= 0:
            break
        msgs.append(text[:cut])
        text = text[cut + 1:]
    msgs.append(text)
    return msgs

# ============================================================
# 3.4.1: ניתוח MFE/MAE — פקודת /mfe בטלגרם
# עונה על שתי שאלות: "טארגט רחוק מדי?" מול "כניסה מאוחרת?"
# לכל עסקה סגורה מה-Gist: כמה המחיר הלך לטובתנו (MFE) ונגדנו (MAE)
# לפני הסגירה, באחוזים מהדרך לטארגט1/לסטופ.
# קריאה וחישוב בלבד — לא נוגע בנתונים ולא בלוגיקת המסחר.
# ============================================================
def _trade_excursions(trade, m15):
    """מחזיר (mfe_pct, mae_pct, n_candles) לעסקה סגורה, או None אם אין נרות בטווח."""
    try:
        et = datetime.datetime.fromisoformat(trade["entry_time"]).replace(tzinfo=None)
        ct = datetime.datetime.fromisoformat(trade["close_time"]).replace(tzinfo=None)
    except Exception:
        return None
    # כולל את נר הכניסה (נפתח עד 15 דק' לפני הכניסה). הטיה אפשרית: תנועה
    # בתוך הנר לפני הכניסה נספרת — מגזים מעט את ה-MFE. הטיה שמרנית:
    # אם גם ככה ה-MFE יוצא אפסי, מסקנת "כניסה מאוחרת" רק מתחזקת.
    lo_bound = et - datetime.timedelta(minutes=15)
    window = [c for c in m15 if lo_bound <= c["t"] <= ct]
    if not window:
        return None
    entry = trade["entry"]; target = trade["target1"]; stop = trade["stop"]
    hi = max(c["h"] for c in window)
    lo = min(c["l"] for c in window)
    if trade["direction"] == "קנייה":
        mfe = (hi - entry) / (target - entry) * 100 if target != entry else 0.0
        mae = (entry - lo) / (entry - stop) * 100 if entry != stop else 0.0
    else:
        mfe = (entry - lo) / (entry - target) * 100 if entry != target else 0.0
        mae = (hi - entry) / (stop - entry) * 100 if stop != entry else 0.0
    return (max(0.0, mfe), max(0.0, mae), len(window))

def run_mfe_analysis(data):
    """מחזיר רשימת הודעות טלגרם: התפלגות MFE של כל העסקאות הסגורות + פירוט."""
    m15 = _fetch_history(SYMBOLS["זהב"], "15min", 4000)
    if not m15:
        return ["⚠️ משיכת נרות נכשלה — נסה שוב מאוחר יותר"]
    closed = [t for t in data.get("trades", []) if t.get("status") == "closed"
              and t.get("entry_time") and t.get("close_time")]
    if not closed:
        return ["אין עסקאות סגורות לניתוח"]
    closed.sort(key=lambda t: t["entry_time"])
    buckets = [("0-10% — לא ביקרה ברווח", 0, 10),
               ("10-33% — ביקור קצר", 10, 33),
               ("33-66% — הלכה חצי דרך", 33, 66),
               ("66-99% — כמעט טארגט", 66, 99),
               ("100%+ — הגיעה לטארגט", 99, 10**9)]
    counts = {b[0]: 0 for b in buckets}
    lines = []
    analyzed = 0
    no_data = 0
    losses_mfe = []
    for t in closed:
        ex = _trade_excursions(t, m15)
        if ex is None:
            no_data += 1
            continue
        mfe, mae, _n = ex
        analyzed += 1
        res = t.get("result", "?")
        icon = {"win": "✅", "loss": "❌", "timeout": "⏰"}.get(res, "▫️")
        if res in ("loss", "timeout"):
            losses_mfe.append(mfe)
        for name, b_lo, b_hi in buckets:
            if b_lo <= mfe < b_hi:
                counts[name] += 1
                break
        try:
            dur_h = (datetime.datetime.fromisoformat(t["close_time"])
                     - datetime.datetime.fromisoformat(t["entry_time"])).total_seconds() / 3600
        except Exception:
            dur_h = 0.0
        lines.append(f"{icon} {fmt_tn(t.get('number','?'))} | רווח מקס' {mfe:.0f}% | נגד {mae:.0f}% | {dur_h:.1f}ש'")
    if analyzed == 0:
        return ["⚠️ אין נרות תואמים לעסקאות (ייתכן שהעסקאות ישנות מ-40 יום)"]
    msg1 = ["📐 <b>ניתוח MFE — כמה כל עסקה ביקרה ברווח לפני הסגירה</b>",
            f"נותחו {analyzed} עסקאות סגורות" + (f" | אין נרות ל-{no_data}" if no_data else ""),
            "אחוזים = כמה מהדרך לטארגט1 המחיר עבר לטובתנו", ""]
    msg1.append("<b>התפלגות (כל העסקאות):</b>")
    for name, _b1, _b2 in buckets:
        msg1.append(f"• {name}: {counts[name]}")
    if losses_mfe:
        s = sorted(losses_mfe)
        n = len(s)
        med = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
        under10 = sum(1 for v in s if v < 10)
        over50 = sum(1 for v in s if v >= 50)
        msg1 += ["", "<b>הפסדים ו-timeouts בלבד:</b>",
                 f"• חציון ביקור ברווח: {med:.0f}%",
                 f"• לא ביקרו ברווח כלל (<10%): {under10} מתוך {n}",
                 f"• עברו חצי דרך לטארגט (50%+): {over50} מתוך {n}",
                 "",
                 "🔎 קריאה: רוב מתחת ל-10% = כניסה מאוחרת (מחמירים כניסות);",
                 "רוב מעל 50% = טארגט רחוק (בודקים טארגט קצר/Break-even בשלב ד')"]
    msgs = ["\n".join(msg1)]
    detail = "📋 <b>פירוט לפי עסקה</b>\n" + "\n".join(lines)
    while len(detail) > 3800:
        cut = detail.rfind("\n", 0, 3800)
        if cut <= 0:
            break
        msgs.append(detail[:cut])
        detail = detail[cut + 1:]
    msgs.append(detail)
    return msgs

def _retro_check(data, trade, signal_dt, now):
    """3.4: בלחיצת 'נכנסתי' מאוחרת — בודק אם הסטופ/טארגט כבר נפגעו מאז האיתות.
    אם כן: סוגר מיד עם התוצאה הנכונה ו-close_time של נר הפגיעה. מחזיר True אם נסגרה."""
    symbol_code = SYMBOLS.get(trade["symbol"])
    if not symbol_code:
        return False
    need = int((now - signal_dt).total_seconds() // (15 * 60)) + 2
    pd = get_prices(symbol_code, outputsize=min(50, max(5, need)))
    if not pd or not pd.get("closes") or not pd.get("last_time"):
        return False
    n = len(pd["closes"])
    last_t = pd["last_time"]
    is_long = trade["direction"] == "קנייה"
    for i in range(n):
        candle_t = last_t - datetime.timedelta(minutes=15 * (n - 1 - i))
        if candle_t <= signal_dt:
            continue
        hi, lo = pd["highs"][i], pd["lows"][i]
        stop_hit = (lo <= trade["stop"]) if is_long else (hi >= trade["stop"])
        target_hit = (hi >= trade["target1"]) if is_long else (lo <= trade["target1"])
        if stop_hit:  # שמרני: סטופ קודם
            loss = points_to_ils(abs(trade["entry"] - trade["stop"])) + SPREAD_COST_ILS
            _finalize_trade(data, trade, "loss", -loss, close_dt=candle_t)
            trade["ack"] = True  # המשתמש כאן עכשיו — אין צורך בתזכורות
            send_telegram(
                f"🛑 <b>עסקה {fmt_tn(trade.get('number','?'))} — הסטופ כבר נפגע בזמן שהיית עסוק</b>\n"
                f"נסגרה רטרואקטיבית ({candle_t.strftime('%H:%M')})\n"
                f"💸 {trade['pnl']} ש\"ח"
            )
            update_indicator_weights(data)
            return True
        if target_hit:
            profit = points_to_ils(abs(trade["target1"] - trade["entry"])) - SPREAD_COST_ILS
            _finalize_trade(data, trade, "win", profit, close_dt=candle_t)
            trade["ack"] = True
            send_telegram(
                f"🎯 <b>עסקה {fmt_tn(trade.get('number','?'))} — הטארגט כבר נפגע בזמן שהיית עסוק!</b>\n"
                f"נסגרה רטרואקטיבית ({candle_t.strftime('%H:%M')})\n"
                f"💰 +{trade['pnl']} ש\"ח\n"
                f"(אם אתה עדיין בפוזיציה בפלוס500 — סגור שם)"
            )
            update_indicator_weights(data)
            return True
    return False

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
                # 3.4: העסקה מעוגנת לזמן האיתות — לא לזמן הלחיצה.
                # המשתמש נכנס בפועל בזמן האיתות ומאשר כשמתפנה.
                try:
                    signal_dt = datetime.datetime.fromisoformat(signal["time"])
                except (KeyError, ValueError):
                    signal_dt = now
                timeout = (signal_dt + datetime.timedelta(hours=TRADE_TIMEOUT_HOURS)).isoformat()
                num = signal.get("number", "?")
                # 3.5.0: כניסה לעסקת שיטה 2 — מבנה שונה: בלי טארגט/timeout, עם סטופ נגרר
                if signal.get("system") == 2:
                    trade = {
                        "id": trade_id, "number": num, "symbol": signal["symbol"],
                        "direction": signal["direction"], "entry": signal["entry"],
                        "stop": signal["stop"], "target1": None, "target2": None,
                        "system": 2, "ils_per_pt": signal.get("ils_per_pt", ils_per_point()),
                        "stars": None, "entry_time": signal_dt.isoformat(),
                        "confirmed_time": now.isoformat(), "status": "open",
                        # ── 3.9.3: השדות האלה נכתבו ל-pending ב-3.9.0 אבל
                        # מעולם לא הועתקו לרשומת העסקה. התוצאה:
                        # open_trade.get("add_trigger") היה תמיד None,
                        # ולכן **הפירמידינג של 3.9.0 מעולם לא ירה בחי.**
                        "units": signal.get("units") or [{"e": signal["entry"]}],
                        "add_trigger": signal.get("add_trigger"),
                        "n_atr": signal.get("n_atr"),
                        "be_hit": signal.get("be_hit", False),
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
                        f"🐢 <b>עסקה {fmt_tn(num)} נפתחה — {signal['symbol']} (שיטה 2)</b>\n"
                        f"כיוון: {'קנייה 🟢' if signal['direction'] == 'קנייה' else 'מכירה 🔴'}\n"
                        f"כניסה: {signal['entry']} | סטופ נגרר: {signal['stop']}\n"
                        f"🎯 בלי טארגט — הבוט יזיז את הסטופ ויודיע. החזקה: ימים.\n"
                        f"אין צורך לשבת מול המסך — כל שינוי יגיע בהתראה.",
                        keyboard
                    )
                    print(f"[CALLBACK] 🐢 עסקת שיטה 2 {fmt_tn(num)} נפתחה", flush=True)
                    continue
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
                    "entry_time": signal_dt.isoformat(),
                    "confirmed_time": now.isoformat(),
                    "timeout": timeout,
                    "status": "open",
                    "target_alerted": False,
                    "stop_alerted": False,
                    "timeout_sent": False
                }
                # 3.5.1: הקשר אנליטי מהאיתות עובר לרשומת העסקה (אם קיים)
                for _k in ("adx", "adx_slope5", "rsi", "atr", "ema_dev", "day_open",
                           "move_from_open", "move_atr", "macd", "macd_sign_agree"):
                    if _k in signal:
                        trade[_k] = signal[_k]
                data["trades"].append(trade)
                pending.pop(trade_id, None)
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["entered"] = data["daily_stats"][today].get("entered", 0) + 1

                # 3.4: אישור מאוחר — בדיקה רטרואקטיבית: אולי הסטופ/טארגט כבר נפגעו בינתיים?
                late_min = (now - signal_dt).total_seconds() / 60
                retro_closed = False
                if late_min > 16:  # יותר מנר אחד של 15 דק'
                    retro_closed = _retro_check(data, trade, signal_dt, now)

                save_data(data)
                if not retro_closed:
                    keyboard = [[{"text": "🔒 סגרתי עסקה", "callback_data": f"cl_{trade_id}"}]]
                    late_note = f"\n⏱️ אושר {int(late_min)} דק' אחרי האיתות — העסקה נמדדת מזמן האיתות" if late_min > 16 else ""
                    send_telegram(
                        f"✅ <b>עסקה {fmt_tn(num)} נפתחה — {signal['symbol']}</b>\n"
                        f"כיוון: {'קנייה 🟢' if signal['direction'] == 'קנייה' else 'מכירה 🔴'}\n"
                        f"כניסה: {signal['entry']} | סטופ: {signal['stop']}\n"
                        f"🎯 טארגט 1: {signal['target1']}\n"
                        f"⏰ תזכורת: {datetime.datetime.fromisoformat(timeout).strftime('%H:%M')}"
                        f"{late_note}",
                        keyboard
                    )
                print(f"[CALLBACK] ✅ עסקה {fmt_tn(num)} נפתחה: {trade_id} (איחור {int(late_min)} דק')", flush=True)

            # ❌ דילגתי
            elif cbd.startswith("sk_"):
                today = get_today_key()
                if today not in data["daily_stats"]:
                    data["daily_stats"][today] = {}
                data["daily_stats"][today]["skipped"] = data["daily_stats"][today].get("skipped", 0) + 1
                save_data(data)
                send_telegram("❌ דילגת — ממשיך לסרוק 👀 (הסימולציה עוקבת אחרי האיתות בשבילך)")
                print(f"[CALLBACK] ❌ דילג: {cbd[3:]}", flush=True)

            # 👍 אישור קבלה של סגירה אוטומטית (3.4)
            elif cbd.startswith("ok_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id), None)
                if trade:
                    trade["ack"] = True
                    save_data(data)
                send_telegram("👍 נרשם")

            # 🔒 סגרתי עסקה (תפריט סגירה ידני)
            elif cbd.startswith("cl_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id and t["status"] == "open"), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה פתוחה")
                    continue
                # ── 3.9.3: שיטה 2 נסגרת לפי מחיר, לא לפי "סוג יציאה" ──
                # שלוש סיבות: (1) אין לה target1 — הכפתור "הגעתי לטארגט"
                # קרס ב-TypeError. (2) המסלולים הישנים חישבו ב-1.5oz בלי
                # מימון — בדיוק הבאג ש-3.9.2 נועד לתקן, ורק המסלול
                # האוטומטי תוקן. (3) מחיר הסגירה בפועל שונה מהסטופ הנגרר
                # שהבוט מניח; בעסקה איטי #1 הפער היה 38.94$.
                if trade.get("system") == 2:
                    trade["waiting_close_px"] = True
                    save_data(data)
                    _u = len(trade.get("units") or [1])
                    send_telegram(
                        f"🐢 <b>עסקה {fmt_tn(trade.get('number','?'))} — באיזה מחיר נסגרת?</b>\n"
                        f"שלח את <b>Close Rate</b> מפלוס500 (לדוגמה: 4390.74).\n"
                        f"כניסה רשומה: {trade.get('entry')} | "
                        f"{_u} יח' × {REPORT_LOT_OZ}oz | "
                        f"מוחזקת {slow_held_days(trade):.1f} ימים\n"
                        f"<i>אם המחיר שונה מהמחיר שהבוט הניח — זה מה שקובע.</i>"
                    )
                    print(f"[CALLBACK] 🔒 שיטה 2 — ממתין למחיר סגירה: {trade_id}", flush=True)
                    continue
                keyboard = [
                    [{"text": "🎯 הגעתי לטארגט", "callback_data": f"rf_{trade_id}"}],
                    [{"text": "💰 יצאתי מוקדם", "callback_data": f"re_{trade_id}"}],
                    [{"text": "❌ יצאתי בהפסד", "callback_data": f"rl_{trade_id}"}]
                ]
                send_telegram(f"📊 <b>עסקה {fmt_tn(trade.get('number','?'))} — איך יצאת?</b>", keyboard)
                print(f"[CALLBACK] 🔒 סגירה: {trade_id}", flush=True)

            # תוצאה — רווח (כפתור ישן/סגירה ידנית לפני פגיעה)
            elif cbd.startswith("rf_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                if trade["status"] == "closed":
                    trade["ack"] = True
                    save_data(data)
                    send_telegram(f"👍 עסקה {fmt_tn(trade.get('number','?'))} כבר נסגרה אוטומטית ({trade.get('pnl')} ש\"ח) — נרשם")
                    continue
                # 3.9.3: מגן — שיטה 2 אין לה target1 (None). לפני התיקון
                # הכפתור הזה קרס ב-TypeError על עסקת שיטה 2.
                if trade.get("system") == 2 or trade.get("target1") is None:
                    trade["waiting_close_px"] = True
                    save_data(data)
                    send_telegram(
                        f"🐢 לעסקה {fmt_tn(trade.get('number','?'))} אין טארגט קבוע.\n"
                        f"שלח את <b>Close Rate</b> מפלוס500 ואחשב רווח אמיתי."
                    )
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
                if trade.get("system") == 2:
                    trade["waiting_close_px"] = True
                    save_data(data)
                    send_telegram(
                        f"🐢 עסקה {fmt_tn(trade.get('number','?'))} — שלח את "
                        f"<b>Close Rate</b> מפלוס500 (לא סכום). "
                        f"אני מחשב ברוטו, מימון ונטו."
                    )
                    continue
                trade["waiting_early_exit"] = True
                save_data(data)
                send_telegram("💰 <b>כמה עשית?</b>\nשלח לי את הסכום בש\"ח")

            # תוצאה — הפסד (כפתור ישן/סגירה ידנית לפני פגיעה)
            elif cbd.startswith("rl_"):
                trade_id = cbd[3:]
                trade = next((t for t in data["trades"] if t["id"] == trade_id), None)
                if not trade:
                    send_telegram("⚠️ לא נמצאה עסקה")
                    continue
                if trade["status"] == "closed":
                    trade["ack"] = True
                    save_data(data)
                    send_telegram(f"👍 עסקה {fmt_tn(trade.get('number','?'))} כבר נסגרה אוטומטית ({trade.get('pnl')} ש\"ח) — נרשם")
                    continue
                # 3.9.3: מגן — שיטה 2 נסגרת לפי מחיר אמיתי, לא לפי הסטופ
                # הרשום. הסטופ הנגרר זז ואינו מחיר היציאה בפועל.
                if trade.get("system") == 2:
                    trade["waiting_close_px"] = True
                    save_data(data)
                    send_telegram(
                        f"🐢 עסקה {fmt_tn(trade.get('number','?'))} — שלח את "
                        f"<b>Close Rate</b> מפלוס500 ואחשב הפסד אמיתי "
                        f"(0.75oz, כולל מימון)."
                    )
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

            # 3.5.1: השערה 1 — חסימת רדיפה (מרחק מפתיחת יום ב-ATR)
            if text.lower() in ("/h1", "h1", "השערה1", "השערה 1"):
                send_telegram("⏳ בודק את השערה 1 (חסימת רדיפה) על ~30 ימים...")
                try:
                    for _part in run_h1_backtest():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            # 3.5.2: השערה 2 — ADX עולה (מגמה נבנית, לא מוצה)
            if text.lower() in ("/h2", "h2", "השערה2", "השערה 2"):
                send_telegram("⏳ בודק את השערה 2 (ADX עולה) על ~30 ימים... (עד 2 דק' — חישוב כפול)")
                try:
                    for _part in run_h2_backtest():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            # 3.5.3: /h3 — שלושת חתכי הנתונים החיים (בוקר / לונג-בלבד / גאומטריה)
            if text.lower() in ("/h3", "h3", "השערה3", "השערה 3"):
                send_telegram("⏳ בודק את חתכי הנתונים (בוקר / לונג / גאומטריה) על ~30 ימים...")
                try:
                    for _part in run_h3_backtest():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            # 3.6.1: /h9 — חלון הפעילות (שיטה 1)
            if text.lower() in ("/h9", "h9", "השערה9", "השערה 9"):
                send_telegram("⏳ בודק חלונות פעילות (כולל 24 שעות) על ~30 ימים...")
                try:
                    for _part in run_h9_backtest():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            # 3.6.0: /h8 — פירמידינג על שיטה 2
            if text.lower() in ("/h8", "h8", "השערה8", "השערה 8"):
                send_telegram("⏳ בודק פירמידינג (שיטה 2) על ~570 ימים...")
                try:
                    for _part in run_h8_backtest():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הבדיקה נכשלה: {e}")
                continue

            # 3.7.2: /reset — ניקוי נתוני שיטה 1 הישנים מהגיסט
            if text.lower() in ("/reset", "reset", "ניקוי"):
                try:
                    _open2 = [t for t in data.get("trades", [])
                              if t.get("system") == 2 and t.get("status") == "open"]
                    _msg = ["🧹 <b>ניקוי גיסט — תצוגה מקדימה</b>\n",
                            "<b>יימחק:</b>",
                            f"  🚨 עסקאות שיטה 1: {len([t for t in data.get('trades', []) if t.get('system') != 2])}",
                            f"  👥 רשומות shadow: {len(data.get('shadow_trades', []))}",
                            f"  📅 ימי סטטיסטיקה: {len(data.get('daily_stats', {}))}",
                            f"  ⏳ ממתינות: {len(data.get('pending', {}))}",
                            "  📊 סטטיסטיקה מצטברת → מאופסת\n",
                            "<b>יישמר:</b>",
                            f"  🐢 עסקאות שיטה 2 פתוחות: {len(_open2)}",
                            f"  🐢 shadow שיטה 2: {len(data.get('slow_shadow', []))}",
                            f"  📊 איתותי שיטה 3: {len(data.get('tf_signals', []))}",
                            "  🐢 מצב נרות שיטה 2 ו-3\n",
                            "⚠️ פעולה בלתי הפיכה.",
                            "לאישור שלח: <code>/reset confirm</code>"]
                    send_telegram("\n".join(_msg))
                except Exception as e:
                    send_telegram(f"⚠️ שגיאה: {e}")
                continue

            if text.lower() in ("/reset confirm", "reset confirm"):
                try:
                    _kept = [t for t in data.get("trades", [])
                             if t.get("system") == 2 and t.get("status") == "open"]
                    _n_tr = len(data.get("trades", [])) - len(_kept)
                    _n_sh = len(data.get("shadow_trades", []))
                    data["trades"] = _kept
                    data["shadow_trades"] = []
                    data["daily_stats"] = {}
                    data["signal_history"] = []
                    data["pending"] = {}
                    data["all_time_stats"] = {"total_trades": 0, "wins": 0, "losses": 0,
                                              "total_pnl": 0, "early_exits": 0, "timeouts": 0}
                    data.pop("shadow_last_signal", None)
                    save_data(data)
                    send_telegram(
                        f"✅ <b>הגיסט נוקה</b>\n"
                        f"נמחקו: {_n_tr} עסקאות, {_n_sh} רשומות shadow\n"
                        f"נשמרו: {len(_kept)} עסקאות שיטה 2 פתוחות, "
                        f"{len(data.get('slow_shadow', []))} shadow-2, "
                        f"{len(data.get('tf_signals', []))} איתותי שיטה 3\n"
                        f"📊 הסטטיסטיקה מתחילה מאפס.")
                    print(f"[RESET] נוקו {_n_tr} עסקאות ו-{_n_sh} shadow", flush=True)
                except Exception as e:
                    send_telegram(f"⚠️ הניקוי נכשל: {e}")
                continue

            # 3.6.0: /shadow2 — סיכום ה-shadow של שיטה 2
            if text.lower() in ("/shadow2", "shadow2", "צל2"):
                try:
                    _ssh = data.get("slow_shadow", [])
                    _cl = [s for s in _ssh if s.get("status") == "closed"]
                    _op = next((s for s in _ssh if s.get("status") == "open"), None)
                    _w = [s for s in _cl if s.get("result") == "win"]
                    _msg = [f"🐢👥 <b>Shadow שיטה 2</b>",
                            f"נסגרו: {len(_cl)} | ✅ {len(_w)} | 💰 {sum(s.get('pnl', 0) for s in _cl):+.2f} ש\"ח"]
                    if _op:
                        _msg.append(f"פתוחה: {_op['direction']} @{_op['entry']} | נגרר: {_op['stop']} "
                                    f"(מ-{_op.get('entry_time', '')[:16]})")
                    else:
                        _msg.append("אין פוזיציית shadow פתוחה כרגע.")
                    if not _ssh:
                        _msg.append("(המעקב התחיל בגרסה 3.6.0 — הרשומות יצטברו עם האיתותים הבאים)")
                    send_telegram("\n".join(_msg))
                except Exception as e:
                    send_telegram(f"⚠️ שגיאה: {e}")
                continue

            # 3.4.1: ניתוח MFE — "טארגט רחוק מדי?" מול "כניסה מאוחרת?"
            if text.lower() in ("/mfe", "mfe", "ניתוח"):
                send_telegram("⏳ מנתח את כל העסקאות הסגורות מול נרות היסטוריים...")
                try:
                    for _part in run_mfe_analysis(data):
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ הניתוח נכשל: {e}")
                continue

            # 3.4.2: הצלבת מנוע מול מציאות
            if text.lower() in ("/cross", "cross", "הצלבה"):
                send_telegram("⏳ מריץ את המנוע ומצליב מול העסקאות האמיתיות...")
                try:
                    for _part in run_cross_check(data):
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ ההצלבה נכשלה: {e}")
                continue

            # 3.4.5: שיטה 2 — עוקב מגמה איטי
            if text.lower() in ("/slow", "slow", "איטי"):
                send_telegram("⏳ מריץ את שיטה 2 על ~שנתיים של נרות 4 שעות... (עד 2 דקות)")
                try:
                    for _part in run_slow_report():
                        send_telegram(_part)
                except Exception as e:
                    send_telegram(f"⚠️ ההרצה נכשלה: {e}")
                continue

            # 3.4: פקודת /status — הבוט חי? מה המצב?
            if text.lower() in ("/status", "status", "סטטוס"):
                try:
                    now = now_il()
                    open_real = [t for t in data["trades"] if t["status"] == "open"]
                    open_shadow = [s for s in data.get("shadow_trades", []) if s.get("status") == "open"]
                    unacked = [t for t in data["trades"] if t["status"] == "closed" and t.get("ack") is False]
                    breaker = consecutive_loss_block(data)
                    today = get_today_key()
                    daily = data["daily_stats"].get(today, {})
                    last_scan = _runtime.get("last_scan")
                    scan_txt = last_scan.strftime("%H:%M:%S") if last_scan else "עוד לא נסרק"
                    trend_txt = "?"
                    try:
                        tr = get_trend_filter(SYMBOLS["זהב"])
                        if tr:
                            state = {"long": "עלייה 🟢", "short": "ירידה 🔴", "none": "דדזון ⚪"}.get(tr["allowed"], "?")
                            trend_txt = f"{state} ({tr['deviation_pct']:+.2f}% מ-EMA50)"
                    except Exception:
                        pass
                    _th = TRADING_HOURS.get("זהב", {"start": 6, "end": 22})
                    in_hours = _th["start"] <= now.hour < _th["end"]
                    evening = now.hour >= LAST_ENTRY_HOUR
                    send_telegram(
                        f"🩺 <b>סטטוס — גרסה {BOT_VERSION}</b>\n"
                        f"✅ חי | 🕐 {now.strftime('%H:%M:%S')}\n"
                        f"🔎 סריקה אחרונה: {scan_txt}\n"
                        f"📈 מגמה: {trend_txt}\n"
                        f"🕗 שעות מסחר: {'כן' if in_hours else 'לא'}"
                        + (f" | 🌆 אחרי {LAST_ENTRY_HOUR}:00 — אין איתותים חדשים" if evening and in_hours else "") + "\n"
                        f"🛑 ברייקר: {'פעיל — עצירה עד מחר' if breaker else 'לא פעיל'}\n"
                        f"📂 סלוטים: {len(open_real)}/{MAX_PARALLEL_TRADES} תפוסים"
                        + (f" | 👁️ סימולציה פתוחה: {len(open_shadow)}" if open_shadow else "") + "\n"
                        + (f"🔔 ממתין לאישור שלך: {len(unacked)}\n" if unacked else "")
                        + f"📊 היום: {daily.get('signals_sent', 0)} איתותים | {daily.get('entered', 0)} כניסות | {round(daily.get('pnl', 0), 2)} ש\"ח"
                    )
                except Exception as e:
                    send_telegram(f"🩺 חי, אבל שגיאה בהרכבת הסטטוס: {e}")
                continue

            # ── 3.9.3: מחיר סגירה אמיתי לעסקת שיטה 2 ─────────────────
            px_trade = next((t for t in data["trades"]
                             if t.get("waiting_close_px") and t["status"] == "open"), None)
            if px_trade:
                try:
                    exit_px = float(text.replace(",", "").strip())
                except ValueError:
                    send_telegram("⚠️ שלח מספר בלבד — למשל 4390.74")
                    continue
                if not (100.0 < exit_px < 100000.0):
                    send_telegram("⚠️ המחיר לא נראה סביר לזהב. שלח שוב.")
                    continue
                net, gross, fund, days = slow_real_pnl(
                    px_trade, exit_px, charge_spread=False)
                _u = len(px_trade.get("units") or [1])
                _is_long = px_trade.get("direction") == "קנייה"
                _pts = ((exit_px - px_trade["entry"]) if _is_long
                        else (px_trade["entry"] - exit_px))
                px_trade["exit"] = exit_px
                px_trade["exit_source"] = "manual_plus500"
                px_trade.pop("waiting_close_px", None)
                _finalize_trade(data, px_trade, "win" if net > 0 else "loss", net)
                px_trade["ack"] = True
                save_data(data)
                send_telegram(
                    f"{'🎉' if net > 0 else '📉'} <b>עסקה "
                    f"{fmt_tn(px_trade.get('number','?'))} נסגרה — "
                    f"{net:+.2f} ש\"ח נטו</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"כניסה {px_trade['entry']} → יציאה {exit_px} "
                    f"({_pts:+.2f}$)\n"
                    f"{_u} יח' × {REPORT_LOT_OZ}oz | {days:.2f} ימי החזקה\n"
                    f"ברוטו {gross:+.2f} | מימון {-fund:.2f}\n"
                    f"<i>ספרד לא נוכה — הוא כבר בתוך שערי פלוס500.</i>"
                )
                print(f"[SLOW] סגירה ידנית @{exit_px} → {net:+.2f} ש\"ח", flush=True)
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
def slow_held_days(trade, close_dt=None):
    """3.9.3: ימי החזקה בפועל. קורא entry_time — לא "time", שלא קיים
    ברשומת עסקה (הוא קיים רק ב-pending). זה היה מקור באג שהראה
    מימון 0 בכל דוח יומי."""
    end = close_dt or now_il()
    for k in ("entry_time", "confirmed_time", "time"):
        raw = trade.get(k)
        if not raw:
            continue
        try:
            et = datetime.datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            continue
        if et.tzinfo and not end.tzinfo:
            et = et.replace(tzinfo=None)
        elif end.tzinfo and not et.tzinfo:
            et = et.replace(tzinfo=end.tzinfo)
        return max(0.0, (end - et).total_seconds() / 86400.0)
    return 0.0


def slow_real_pnl(trade, exit_px, close_dt=None, charge_spread=True):
    """3.9.3: רווח/הפסד אמיתי לעסקת שיטה 2, בגודל REPORT_LOT_OZ ליחידה.
    מחזיר (net, gross, funding, days).

    charge_spread=False כשמחיר היציאה הוא מילוי אמיתי מפלוס500 — שם
    הספרד כבר בתוך שערי הפתיחה והסגירה (מסך Closed Position: Net P&L
    = P&L מינוס מימון בלבד, אין שורת ספרד). כשהמחיר מגיע מנר/סטופ
    מחושב — הספרד כן נגבה."""
    is_long = trade.get("direction") == "קנייה"
    units = trade.get("units") or [{"e": trade.get("entry")}]
    days = slow_held_days(trade, close_dt)
    gross = 0.0
    for u in units:
        e = u.get("e", trade.get("entry"))
        pts = (exit_px - e) if is_long else (e - exit_px)
        if charge_spread:
            pts -= SPREAD_POINTS
        gross += pts * REPORT_LOT_OZ * USD_ILS
    fund = funding_cost_ils(REPORT_LOT_OZ * len(units), days)
    return round(gross - fund, 2), round(gross, 2), round(fund, 2), days


def _finalize_trade(data, trade, result, pnl, close_dt=None):
    """3.4: סגירה רשמית ברגע הזיהוי — close_time לפי השוק, לא לפי האישור."""
    trade["status"] = "closed"
    trade["result"] = result
    trade["pnl"] = round(pnl, 2)
    trade["close_time"] = (close_dt or now_il()).isoformat()
    trade["ack"] = False
    trade["last_alert"] = now_il().isoformat()
    trade["alert_repeats"] = 0
    stats = data["all_time_stats"]
    if result == "win":
        stats["wins"] += 1
    elif result == "loss":
        stats["losses"] += 1
    else:  # timeout — נספר לפי סימן ה-pnl אך לא כרווח/הפסד מלא בסטטיסטיקת win rate
        stats.setdefault("timeouts", 0)
        stats["timeouts"] += 1
    stats["total_trades"] += 1
    stats["total_pnl"] = round(stats.get("total_pnl", 0) + trade["pnl"], 2)
    day = trade["close_time"][:10]
    if day not in data["daily_stats"]:
        data["daily_stats"][day] = {}
    data["daily_stats"][day]["pnl"] = round(data["daily_stats"][day].get("pnl", 0) + trade["pnl"], 2)

def monitor_open_trades(data):
    now = now_il()
    candle_cache = {}
    changed = False

    def get_candle(symbol_name):
        symbol_code = SYMBOLS.get(symbol_name)
        if not symbol_code:
            return None
        if symbol_code not in candle_cache:
            pd = get_prices(symbol_code, outputsize=5)
            if pd and pd["closes"]:
                candle_cache[symbol_code] = {
                    "high": pd["highs"][-1],
                    "low": pd["lows"][-1],
                    "close": pd["closes"][-1]
                }
        return candle_cache.get(symbol_code)

    for trade in data["trades"]:
        if trade.get("system") == 2:
            continue  # 3.5.0: עסקאות שיטה 2 מנוהלות ב-slow_scan_and_monitor (סטופ נגרר, בלי timeout)
        if trade["status"] == "open":
            candle = get_candle(trade["symbol"])
            if not candle:
                continue

            high, low, current = candle["high"], candle["low"], candle["close"]
            direction = trade["direction"]
            stop = trade["stop"]
            target1 = trade["target1"]
            num = trade.get("number", "?")

            target_hit = (high >= target1) if direction == "קנייה" else (low <= target1)
            stop_hit = (low <= stop) if direction == "קנייה" else (high >= stop)

            # עדיפות לסטופ (שמרני) במקרה ששניהם נגעו באותו נר
            if stop_hit:
                risk_distance = abs(trade["entry"] - stop)
                loss = points_to_ils(risk_distance) + SPREAD_COST_ILS
                _finalize_trade(data, trade, "loss", -loss)
                changed = True
                keyboard = [[{"text": "👍 קיבלתי", "callback_data": f"ok_{trade['id']}"}]]
                send_telegram(
                    f"🛑 <b>עסקה {fmt_tn(num)} — נגעת בסטופ. נסגרה אוטומטית.</b>\n"
                    f"{trade['symbol']} | מחיר: {current} | סטופ: {stop}\n"
                    f"💸 {trade['pnl']} ש\"ח\n"
                    f"(3.4: הסגירה נרשמה ברגע הזיהוי — אשר קבלה 👇)",
                    keyboard
                )
                update_indicator_weights(data)
                print(f"[AUTO] 🛑 סטופ עסקה {fmt_tn(num)} — נסגרה בזיהוי", flush=True)

            elif target_hit:
                reward_distance = abs(target1 - trade["entry"])
                profit = points_to_ils(reward_distance) - SPREAD_COST_ILS
                _finalize_trade(data, trade, "win", profit)
                changed = True
                keyboard = [[{"text": "👍 קיבלתי", "callback_data": f"ok_{trade['id']}"}]]
                send_telegram(
                    f"🎯 <b>עסקה {fmt_tn(num)} — טארגט 1! נסגרה אוטומטית.</b>\n"
                    f"{trade['symbol']} | מחיר: {current} | טארגט: {target1}\n"
                    f"💰 +{trade['pnl']} ש\"ח\n"
                    f"(אם אתה עדיין בפוזיציה בפלוס500 — סגור שם. אשר קבלה 👇)",
                    keyboard
                )
                update_indicator_weights(data)
                print(f"[AUTO] 🎯 טארגט עסקה {fmt_tn(num)} — נסגרה בזיהוי", flush=True)

            else:
                # timeout 6 שעות: סגירה אוטומטית במחיר השוק (כמו במנוע ה-backtest)
                try:
                    expired = now >= datetime.datetime.fromisoformat(trade["timeout"])
                except (TypeError, ValueError):
                    expired = True
                if expired:
                    diff = (current - trade["entry"]) if direction == "קנייה" else (trade["entry"] - current)
                    pnl = points_to_ils(diff) - SPREAD_COST_ILS if diff > 0 else -(points_to_ils(abs(diff)) + SPREAD_COST_ILS)
                    _finalize_trade(data, trade, "timeout", pnl)
                    changed = True
                    keyboard = [[{"text": "👍 קיבלתי", "callback_data": f"ok_{trade['id']}"}]]
                    send_telegram(
                        f"⏰ <b>עסקה {fmt_tn(num)} — {TRADE_TIMEOUT_HOURS} שעות. נסגרה אוטומטית במחיר שוק.</b>\n"
                        f"מחיר: {current} | כניסה: {trade['entry']}\n"
                        f"💰 {trade['pnl']} ש\"ח\n"
                        f"(אם אתה עדיין בפוזיציה — סגור בפלוס500. אשר קבלה 👇)",
                        keyboard
                    )
                    print(f"[AUTO] ⏰ timeout עסקה {fmt_tn(num)} — נסגרה במחיר שוק", flush=True)

        # תזכורת חוזרת: עסקה סגורה שלא אושרה תוך 30 דק' — עד 5 תזכורות
        elif trade["status"] == "closed" and trade.get("ack") is False:
            try:
                last = datetime.datetime.fromisoformat(trade.get("last_alert", trade["close_time"]))
                overdue = (now - last).total_seconds() >= REMINDER_MINUTES * 60
            except (TypeError, ValueError):
                overdue = True
            if overdue and trade.get("alert_repeats", 0) < MAX_REMINDERS:
                trade["alert_repeats"] = trade.get("alert_repeats", 0) + 1
                trade["last_alert"] = now.isoformat()
                changed = True
                keyboard = [[{"text": "👍 קיבלתי", "callback_data": f"ok_{trade['id']}"}]]
                res_txt = {"win": "רווח 🎯", "loss": "הפסד 🛑", "timeout": "סגירת זמן ⏰"}.get(trade.get("result"), "")
                send_telegram(
                    f"🔔 <b>תזכורת {trade['alert_repeats']}/{MAX_REMINDERS} — עסקה {fmt_tn(trade.get('number','?'))} נסגרה ({res_txt})</b>\n"
                    f"💰 {trade.get('pnl')} ש\"ח — אשר קבלה 👇",
                    keyboard
                )

    # ================= 3.4: מעקב סימולציה (שקט — בלי טלגרם) =================
    for sh in data.get("shadow_trades", []):
        if sh.get("status") != "open":
            continue
        candle = get_candle(sh["symbol"])
        if not candle:
            continue
        high, low, current = candle["high"], candle["low"], candle["close"]
        is_long = sh["direction"] == "קנייה"
        stop_hit = (low <= sh["stop"]) if is_long else (high >= sh["stop"])
        target_hit = (high >= sh["target1"]) if is_long else (low <= sh["target1"])
        result = None
        if stop_hit:
            result, pnl = "loss", -(points_to_ils(abs(sh["entry"] - sh["stop"])) + SPREAD_COST_ILS)
        elif target_hit:
            result, pnl = "win", points_to_ils(abs(sh["target1"] - sh["entry"])) - SPREAD_COST_ILS
        else:
            try:
                if now >= datetime.datetime.fromisoformat(sh["timeout"]):
                    diff = (current - sh["entry"]) if is_long else (sh["entry"] - current)
                    result = "timeout"
                    pnl = points_to_ils(diff) - SPREAD_COST_ILS if diff > 0 else -(points_to_ils(abs(diff)) + SPREAD_COST_ILS)
            except (TypeError, ValueError):
                pass
        if result:
            sh["status"] = "closed"
            sh["result"] = result
            sh["pnl"] = round(pnl, 2)
            sh["close_time"] = now.isoformat()
            changed = True
            print(f"[SHADOW] {sh['number']} נסגרה: {result} {sh['pnl']}", flush=True)

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

    # 3.4: מסלול הסימולציה — כל מה שהמערכת ראתה היום
    shadows_today = [s for s in data.get("shadow_trades", [])
                     if s.get("status") == "closed" and s.get("close_time", "").startswith(today)]
    sh_wins = sum(1 for s in shadows_today if s["result"] == "win")
    sh_losses = sum(1 for s in shadows_today if s["result"] == "loss")
    sh_touts = sum(1 for s in shadows_today if s["result"] == "timeout")
    sh_pnl = round(sum(s.get("pnl", 0) for s in shadows_today), 2)
    sh_open = sum(1 for s in data.get("shadow_trades", []) if s.get("status") == "open")
    blocked = daily.get("blocked", {})
    blocked_txt = ""
    if blocked:
        blocked_txt = "🚫 נחסמו תפעולית: " + " | ".join(f"{k}: {v}" for k, v in blocked.items()) + "\n"
    skipped = daily.get("skipped", 0)

    shadow_section = (
        f"\n👁️ <b>עיני המערכת (סימולציה, בלי מגבלות):</b>\n"
        f"נסגרו היום: {len(shadows_today)} | ✅ {sh_wins} | ❌ {sh_losses}"
        + (f" | ⏰ {sh_touts}" if sh_touts else "")
        + (f" | פתוחות: {sh_open}" if sh_open else "") + "\n"
        f"💰 {sh_pnl} ש\"ח\n"
        f"{blocked_txt}"
        + (f"👋 דילגת על: {skipped}\n" if skipped else "")
    ) if (shadows_today or sh_open or blocked or skipped) else ""

    # ── 3.9.1: שיטה 2 — הדוח היה עיוור אליה לגמרי ────────────────
    # עד כאן הדוח קרא רק signals_sent/entered/pnl/shadow_trades, שכולם
    # שייכים לשיטה 1. שיטה 2 היא היחידה שחיה עם כפתורים — והיא לא הופיעה.
    s2_open = [t for t in data.get("trades", [])
               if t.get("system") == 2 and t.get("status") == "open"]
    s2_closed_today = [t for t in data.get("trades", [])
                       if t.get("system") == 2 and t.get("status") == "closed"
                       and str(t.get("close_time", "")).startswith(today)]
    s2_pending = {k: v for k, v in data.get("pending", {}).items()
                  if v.get("system") == 2}
    s2_shadow_open = [s for s in data.get("slow_shadow", [])
                      if s.get("status") == "open"]

    s2 = f"\n{METHOD_MARK[2]} <b>שיטה 2 (דונקיאן {SLOW_ENTRY_DAYS}/{SLOW_TRAIL_DAYS}):</b>\n"
    if s2_open:
        for t in s2_open:
            units = t.get("units", [{"e": t.get("entry")}])
            oz = len(units) * SLOW_LOT_OZ
            # 3.9.3: היה t["time"] — מפתח שלא קיים ברשומת עסקה (רק
            # ב-pending). ה-except בלע את ה-KeyError והמימון הוצג
            # כ-0 בכל דוח, תמיד.
            days = slow_held_days(t)
            s2 += (f"📍 פתוחה: {t.get('direction')} @{t.get('entry')} | "
                   f"{len(units)} יח' ({oz:.2f} אונקיות)\n"
                   f"🛑 סטופ נגרר: {t.get('stop')}"
                   + (" (ברייקאיבן פעיל)" if t.get("be_hit") else "") + "\n"
                   f"⏳ מוחזקת {days:.1f} ימים | "
                   f"💸 מימון מצטבר: {funding_cost_ils(oz, days):.1f}- ש\"ח\n")
            if t.get("add_trigger") and len(units) < SLOW_PYRAMID_UNITS:
                s2 += f"🪜 טריגר ליחידה הבאה: {t['add_trigger']}\n"
    elif s2_pending:
        s2 += f"⏳ איתות ממתין לאישור: {len(s2_pending)}\n"
    else:
        s2 += "😴 אין פוזיציה ואין איתות ממתין.\n"
    if s2_closed_today:
        s2 += (f"🔚 נסגרו היום: {len(s2_closed_today)} | "
               f"{round(sum(t.get('pnl', 0) for t in s2_closed_today), 2)} ש\"ח\n")
    if s2_shadow_open:
        sh = s2_shadow_open[0]
        s2 += (f"👁️ צל: {sh.get('direction')} @{sh.get('entry')} | "
               f"סטופ {sh.get('stop')}\n")

    # ── 3.9.1: שיטה 3 — איתותי 4H/6H מ-tf_signals ────────────────
    tf_all = data.get("tf_signals", [])
    tf_today = [s for s in tf_all if str(s.get("time", "")).startswith(today)]
    s3 = f"\n{METHOD_MARK[3]} <b>שיטה 3 (מעקב בלבד):</b>\n"
    if tf_today:
        for s in tf_today:
            mark = ""
            if s.get("tf") == "4H":
                mark = " ✅גיבוי 6H" if s.get("backup") else " ⚠️בלי גיבוי (37%)"
            s3 += (f"{s.get('tf')}: {s.get('direction')} @{s.get('entry')} | "
                   f"סטופ {s.get('stop')} | יעד {s.get('target')}{mark}\n")
        n_no = sum(1 for s in tf_today
                   if s.get("tf") == "4H" and s.get("backup") is False)
        if n_no:
            s3 += f"⚠️ {n_no} איתותי 4H בלי גיבוי 6H — הקבוצה המפסידה.\n"
    else:
        s3 += "אין איתותים חדשים היום.\n"
    tf_week = len([s for s in tf_all[-60:]])
    s3 += f"📚 סה\"כ איתותים ביומן: {tf_week}\n"

    send_telegram(
        f"📊 <b>דוח יומי — {today}</b>\n\n"
        f"{METHOD_MARK[1]} <b>שיטה 1 (מעקב בלבד) + התיק שלך:</b>\n"
        f"🔔 איתותים שנשלחו: {signals_today}\n"
        f"✅ עסקאות שנכנסת: {entered_today}\n"
        f"💰 רווח/הפסד היום: {round(pnl_today, 2)} ש\"ח\n"
        f"{shadow_section}"
        f"{s2}"
        f"{s3}\n"
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
    print(f"🤖 בוט מסחר מופעל! [גרסה {BOT_VERSION}]", flush=True)
    print(f"TOKEN exists: {bool(TELEGRAM_TOKEN)}", flush=True)
    print(f"CHAT_ID set: {bool(CHAT_ID)}", flush=True)
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
        f"🤖 <b>בוט המסחר הופעל!</b> (גרסה {BOT_VERSION})\n\n"
        "🐢 <b>שיטה 2 — למסחר:</b> פריצת 20 ימים על נר 4 שעות,\n"
        "סטופ נגרר, בלי טארגט. איתות עם כפתורים = אמיתי.\n"
        "צפי: 1-2 איתותים בשבוע. שקט = תקין.\n\n"
        + ("🔇 <b>שיטה 1 — שקטה:</b> רצה ברקע, לא שולחת הודעות.\n"
           "הצללים והדוח היומי ממשיכים כרגיל.\n\n"
           if OLD_SIGNALS_SILENT else
           "🚨 <b>המערכת הישנה — מעקב בלבד:</b> בלי כפתורים,\n"
           "לא למסחר. הסימולציה ממשיכה לרשום אותה.\n\n") +
        "💡 /backtest | /h1 | /h2 | /h3 | /h8 | /h9 | /shadow2 | /reset | /status | /mfe | /cross | /slow\n"
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
                _runtime["last_scan"] = now
                print(f"\n--- סריקה #{scan_count} {now.strftime('%H:%M:%S')} ---", flush=True)

                for name, code in SYMBOLS.items():
                    try:
                        analyze_and_signal(name, code, data)
                        time.sleep(3)
                    except Exception as e:
                        print(f"שגיאה ב{name}: {e}", flush=True)

                monitor_open_trades(data)

                # 3.5.0: שיטה 2 — סריקה וניטור (איתותי 🐢 + סטופ נגרר)
                if SLOW_LIVE:
                    try:
                        slow_scan_and_monitor(data)
                        try:
                            tf_scan(data)          # שיטה 3 — 4H + 6H, מעקב בלבד
                        except Exception as _e:
                            print(f"[TF] שגיאה: {_e}", flush=True)
                    except Exception as e:
                        print(f"[SLOW] שגיאה: {e}", flush=True)

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
            if now.hour == TRADING_HOURS["זהב"]["start"] and now.minute < 11 and last_morning_ping != today_key:
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
