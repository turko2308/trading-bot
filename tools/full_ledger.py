"""
tools/full_ledger.py — לדג'ר מאוחד: שיטה 2 (חי+shadow) + שיטה 3 (כל האיתותים), כרונולוגי
================================================================================================
נבנה 14/09/2026 בעקבות בקשת איזק לתמונה מלאה אחת, לא בדיקות נקודתיות.

קורא bot_data.json (מה-Gist) ומאחד את כל שלוש השיטות לטבלה כרונולוגית אחת:
  שיטה 1 — renko_signals (Renko קופסה-קטנה, מ-3.10.0, מעקב בלבד)
  שיטה 2 — trades (חי) + slow_shadow (כולל מה שלא נכנס בפועל)
  שיטה 3 — tf_signals (6H; 4H מושבת מ-14/09/2026)
פלט: טבלה כרונולוגית + פילוח לפי שיטה/תוצאה.

עדכון 15/09/2026: נוספה שיטה 1 (Renko). הערת המגבלה הישנה על חוסר דאטה
לאוג-ספט 2026 בוטלה — data/xauusd_h1.csv ו-h4.csv עודכנו ל-3 שנים מלאות
(14/9/2023-14/9/2026). חישוב MFE עדיין לא ממומש כאן, אבל הדאטה קיים אם ירצו.

הרצה: python tools/full_ledger.py bot_data.json
"""
import json, sys

def load(path):
    return json.load(open(path))

def build_ledger(d):
    rows = []
    for t in d.get("trades", []):
        rows.append({
            "system": "שיטה 2 (חי)", "time": t.get("entry_time"),
            "direction": t.get("direction"), "entry": t.get("entry"),
            "stop": t.get("stop"), "status": t.get("status"),
            "result": t.get("result"), "pnl": t.get("pnl"),
            "close_time": t.get("close_time"),
        })
    for s in d.get("slow_shadow", []):
        rows.append({
            "system": "שיטה 2 (shadow)", "time": s.get("entry_time"),
            "direction": s.get("direction"), "entry": s.get("entry"),
            "stop": s.get("stop"), "status": s.get("status"),
            "result": s.get("result"), "pnl": s.get("pnl"),
            "close_time": s.get("close_time"),
        })
    for s in d.get("renko_signals", []):
        rows.append({
            "system": "שיטה 1 (Renko)", "time": s.get("time"),
            "direction": s.get("direction"), "entry": s.get("entry"),
            "stop": s.get("stop"), "status": s.get("status"),
            "result": ("win" if (s.get("pnl") or 0) > 0 else "loss") if s.get("status") == "closed" else None,
            "pnl": s.get("pnl"), "close_time": s.get("close_time"),
        })
    for s in d.get("tf_signals", []):
        rows.append({
            "system": f"שיטה 3 ({s.get('tf')})", "time": s.get("time"),
            "direction": s.get("direction"), "entry": s.get("entry"),
            "stop": s.get("stop"), "status": s.get("status"),
            "result": s.get("result"), "pnl": s.get("pnl"),
            "close_time": s.get("close_time"),
        })
    rows = [r for r in rows if r.get("time")]
    return sorted(rows, key=lambda r: r["time"])

def summary(rows):
    from collections import defaultdict
    by_sys = defaultdict(lambda: {"win":0,"loss":0,"open":0,"pnl":0.0})
    for r in rows:
        s = by_sys[r["system"]]
        res = r.get("result")
        if res == "win": s["win"] += 1
        elif res == "loss": s["loss"] += 1
        else: s["open"] += 1
        s["pnl"] += r.get("pnl") or 0
    return by_sys

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "bot_data.json"
    d = load(path)
    ledger = build_ledger(d)

    print("="*100)
    print(f"לדג'ר כרונולוגי מאוחד — {len(ledger)} רשומות")
    print("="*100)
    for r in ledger:
        pnl = r.get("pnl")
        pnl_s = f"{pnl:8.2f}" if pnl is not None else "    —   "
        print(f"{r['time'][:16]}  {r['system']:16s} {str(r['direction']):6s} "
              f"entry={r['entry']:8.2f}  {str(r.get('result') or r.get('status')):8s}  pnl={pnl_s}")

    print("\n" + "="*100)
    print("סיכום לפי שיטה")
    print("="*100)
    for sys_name, s in sorted(summary(ledger).items()):
        total = s["win"] + s["loss"]
        wr = 100*s["win"]/total if total else 0
        print(f"{sys_name:18s}  win={s['win']:2d} loss={s['loss']:2d} open={s['open']:2d}  "
              f"win%={wr:5.1f}  pnl={s['pnl']:9.2f}")

    all_pnl = sum(r.get("pnl") or 0 for r in ledger)
    print(f"\nסה\"כ הכל (כל השיטות): {all_pnl:.2f}")
    print("\n(MFE לא מחושב כאן — הדאטה קיים ב-data/ אם ירצו להוסיף.)")
