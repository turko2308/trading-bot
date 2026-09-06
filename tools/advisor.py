"""
tools/advisor.py — שכבת פרשנות לדוח היומי (3.9.9)
==================================================
מה זה כן: קורא את הגיסט ומתריע על חריגות מהפרוטוקול ומממצאים
שכבר מתועדים ב-STATUS/DECISIONS — ברגע שהן קורות, במקום לגלות
שבוע אחרי.

מה זה לא: לא מודל חיזוי, לא בוחר עסקאות, לא נוגע בלוגיקת המסחר.
המדגם האפקטיבי של שיטה 3 הוא ~170 אשכולות בלתי-תלויים (לא 501),
וזה קטן מכדי לאמן עליו משהו. כל ניסיון כזה נדחה מראש.

── עיקרון הבטיחות ──────────────────────────────────────────────
כל המספרים מחושבים ב-Python דטרמיניסטית (`build_observations`).
ה-LLM מקבל אותם מוכנים ורק מנסח/מתעדף. הוא לא מחשב, לא מסיק,
ולא ממציא מספרים. אם קריאת ה-API נכשלת — מוחזר הפלט הדטרמיניסטי
כמו שהוא. הבוט לא נופל ולא משנה התנהגות.
"""
import os
import json
import datetime

# ── ספים, כולם מבוססי ממצא מתועד ────────────────────────────
CLUSTER_WINDOW_H = 12      # איתותים באותו כיוון בתוך X שעות = אשכול אחד
STALE_MINUTES = 30         # איתות ישן מזה = לבדוק אם עוד רלוונטי
STALE_ATR_FRAC = 0.25      # ...או אם המחיר זז יותר מ-X×ATR
NO_BACKUP_WIN = 47.3       # §21.6 (מתוקן 06/09)
NO_BACKUP_PNL = -1297
BACKUP_WIN = 60.8
LLM_MODEL = "claude-sonnet-4-6"


def _parse(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", ""))
    except Exception:
        return None


def build_observations(data, now=None, current_price=None):
    """מחזיר רשימת הערות דטרמיניסטיות. אין כאן LLM ואין אקראיות —
    אותו קלט תמיד מחזיר אותו פלט."""
    now = now or datetime.datetime.now()
    obs = []
    tf = data.get("tf_signals", []) or []
    trades = data.get("trades", []) or []

    # ── 1. איתות 4H פתוח בלי גיבוי 6H ──────────────────────
    # §21.6: הקבוצה הזאת ב-47.3% ומפסידה 1,297 ₪ על 93 עסקאות,
    # מול 60.8% ורווח בקבוצה עם גיבוי.
    nb = [s for s in tf
          if s.get("status") == "open" and s.get("tf") == "4H"
          and s.get("backup") is False]
    for s in nb:
        obs.append({
            "sev": "warn",
            "tag": "אין גיבוי 6H",
            "text": (f"איתות {s.get('id', '?')} ({s.get('direction')}) פתוח בלי "
                     f"גיבוי 6H. הקבוצה הזאת: {NO_BACKUP_WIN}% הצלחה, "
                     f"{NO_BACKUP_PNL:+} ₪ על 93 עסקאות. עם גיבוי: {BACKUP_WIN}%."),
        })

    # ── 2. אשכול: כמה איתותים באותו כיוון = הימור אחד ────────
    # נמדד 06/09: 501 עסקאות = ~170 אשכולות בלתי-תלויים.
    # חציון אשכול 2, מקסימום 12.
    recent = []
    for s in tf:
        t = _parse(s.get("time"))
        if t and (now - t).total_seconds() <= CLUSTER_WINDOW_H * 3600:
            recent.append((t, s))
    by_dir = {}
    for t, s in recent:
        by_dir.setdefault(s.get("direction"), []).append(s)
    for d, group in by_dir.items():
        if len(group) >= 3:
            obs.append({
                "sev": "warn",
                "tag": "אשכול",
                "text": (f"{len(group)} איתותי {d} ב-{CLUSTER_WINDOW_H} השעות "
                         f"האחרונות. זה הימור אחד על אותה תנועה, לא "
                         f"{len(group)} עסקאות נפרדות — הסיכון מצטבר, "
                         f"הפיזור לא."),
            })

    # ── 3. איתות שהתיישן ─────────────────────────────────────
    # §21.8: #041 — נר נסגר 18:00, נצפה 19:29, המחיר כבר זז 17$.
    if current_price:
        for s in tf:
            if s.get("status") != "open":
                continue
            t = _parse(s.get("time"))
            if not t:
                continue
            age_min = (now - t).total_seconds() / 60.0
            entry = s.get("entry")
            atr = s.get("atr") or 0
            if not entry or age_min < STALE_MINUTES:
                continue
            drift = abs(current_price - entry)
            if atr and drift >= STALE_ATR_FRAC * atr:
                obs.append({
                    "sev": "warn",
                    "tag": "איתות התיישן",
                    "text": (f"איתות {s.get('id', '?')} בן {age_min:.0f} דק'. "
                             f"כניסה {entry}, מחיר עכשיו {current_price:.2f} — "
                             f"פער {drift:.1f}$ ({drift/atr:.2f}×ATR). "
                             f"כניסה עכשיו היא רדיפה, לא האיתות שנבדק."),
                })

    # ── 4. חריגה מהפרוטוקול: עסקת שיטה 2 פתוחה בלי אישור ────
    # קרה ב-02/09 (איטי #4): נכנס בדמו בלי ללחוץ "נכנסתי",
    # והגיסט לא יודע על הכניסה.
    for t_ in trades:
        if (t_.get("system") == 2 and t_.get("status") == "open"
                and not t_.get("entered")):
            obs.append({
                "sev": "info",
                "tag": "פרוטוקול",
                "text": (f"עסקת שיטה 2 {t_.get('number', '?')} פתוחה בלי סימון "
                         f"'נכנסתי'. אם נכנסת בפועל — הגיסט לא יודע, "
                         f"והסטטיסטיקה תהיה חסרה."),
            })

    # ── 5. סקאלה ישנה בסטטיסטיקה ─────────────────────────────
    ats = data.get("all_time_stats", {}) or {}
    if ats.get("total_pnl"):
        obs.append({
            "sev": "info",
            "tag": "סקאלה ישנה",
            "text": (f"all_time_stats מחזיק {ats.get('total_pnl')} ₪ מסקאלת "
                     f"risk-40. לא בר-השוואה לדיווח 0.75oz. /resetstats מנקה."),
        })

    # ── 6. רצף הפסדים בשיטה 3 היום ──────────────────────────
    today = now.strftime("%Y-%m-%d")
    closed_today = [s for s in tf
                    if s.get("status") == "closed"
                    and str(s.get("closed_at", ""))[:10] == today]
    if len(closed_today) >= 3:
        losses = [s for s in closed_today if (s.get("pnl") or 0) < 0]
        if len(losses) == len(closed_today):
            tot = sum(s.get("pnl") or 0 for s in closed_today)
            obs.append({
                "sev": "warn",
                "tag": "רצף הפסדים",
                "text": (f"כל {len(closed_today)} האיתותים שנסגרו היום הפסידו "
                         f"({tot:+.0f} ₪). בשיטה עם ~58% הצלחה זה בטווח "
                         f"הנורמלי — לא סיבה לשנות פרמטרים."),
            })

    return obs


def format_block(obs, max_items=5):
    """פלט דטרמיניסטי לטלגרם. עובד גם בלי LLM."""
    if not obs:
        return "🤖 <b>הערות</b>\nאין חריגות.\n"
    order = {"warn": 0, "info": 1}
    obs = sorted(obs, key=lambda o: order.get(o["sev"], 2))[:max_items]
    lines = ["🤖 <b>הערות</b>"]
    for o in obs:
        mark = "⚠️" if o["sev"] == "warn" else "•"
        lines.append(f"{mark} <b>{o['tag']}:</b> {o['text']}")
    return "\n".join(lines) + "\n"


def llm_polish(obs, api_key=None, timeout=20):
    """שכבה אופציונלית: מנסחת ומתעדפת. לא מחשבת ולא מוסיפה מספרים.
    כישלון => None, והמתקשר נופל חזרה ל-format_block."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not obs:
        return None
    try:
        import requests
        facts = "\n".join(f"[{o['sev']}] {o['tag']}: {o['text']}" for o in obs)
        prompt = (
            "אלה הערות שחושבו דטרמיניסטית ממערכת מסחר. נסח אותן מחדש "
            "בעברית, קצר וישיר, לכל היותר 5 שורות, מהחשוב לפחות חשוב.\n\n"
            "חוקים נוקשים:\n"
            "- אל תמציא, תשנה או תעגל שום מספר. השתמש רק במספרים שכאן.\n"
            "- אל תוסיף המלצות מסחר, תחזיות, או ניתוח שוק.\n"
            "- אל תציע לשנות פרמטרים.\n"
            "- אם משהו לא ברור, השמט אותו.\n"
            "- החזר טקסט בלבד, בלי הקדמה.\n\n"
            f"ההערות:\n{facts}"
        )
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": LLM_MODEL, "max_tokens": 500,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        if r.status_code != 200:
            print(f"[ADVISOR] API {r.status_code}", flush=True)
            return None
        parts = r.json().get("content", [])
        txt = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        txt = txt.strip()
        return f"🤖 <b>הערות</b>\n{txt}\n" if txt else None
    except Exception as e:
        print(f"[ADVISOR] שגיאה: {e}", flush=True)
        return None


def advisor_block(data, now=None, current_price=None, use_llm=True):
    """נקודת הכניסה היחידה. לעולם לא זורק חריגה."""
    try:
        obs = build_observations(data, now=now, current_price=current_price)
        if use_llm:
            polished = llm_polish(obs)
            if polished:
                return polished
        return format_block(obs)
    except Exception as e:
        print(f"[ADVISOR] כשל: {e}", flush=True)
        return ""
