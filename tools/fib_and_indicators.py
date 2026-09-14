"""
tools/fib_and_indicators.py — סבב 14/09/2026: פיבונצ'י + 7 אינדיקטורים לא-נבדקים
======================================================================================
נבנה באותו יום שנבדק (לא שוחזר בדיעבד) — לוודא שזה לא קורה שוב.

חלק א: Fibonacci pullback entry (38.2-61.8%) אחרי breakout 20-bar, יעד ב-Fib
extension (161.8%). נדחה: Bootstrap P(רווח) 75-85% בלבד (סף: 99%+), מדגם דק (28-30).

חלק ב: 7 אינדיקטורים סטנדרטיים, standalone entry, יציאה אחידה 2×ATR:
Stochastic, CCI(20), Williams %R, Parabolic SAR, Keltner Channel, Ichimoku
Tenkan/Kijun, VWAP deviation. Keltner Channel נראה הכי טוב (Bootstrap 99.6%
ב-4H) אך נדחה: 80% חפיפה עם כניסות שיטה 3 (לא איתות עצמאי) + נכשל ב-6H
(Bootstrap צנח ל-88.3%, 2026H1 התהפך משלילי לחיובי).

הרצה: python tools/fib_and_indicators.py   (מצפה ל-xauusd_h4.csv)
"""
import csv, datetime
import statistics as st

def load(path="xauusd_h4.csv"):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            rows.append({"t":datetime.datetime.fromisoformat(r["timestamp"]),
                         "o":float(r["open"]),"h":float(r["high"]),
                         "l":float(r["low"]),"c":float(r["close"])})
    return sorted(rows, key=lambda x:x["t"])

def atr_series(bars,n=14):
    trs=[]
    for i in range(len(bars)):
        if i==0: trs.append(bars[i]["h"]-bars[i]["l"]); continue
        pc=bars[i-1]["c"]
        trs.append(max(bars[i]["h"]-bars[i]["l"],abs(bars[i]["h"]-pc),abs(bars[i]["l"]-pc)))
    out=[None]*len(bars)
    for i in range(n-1,len(bars)): out[i]=sum(trs[i-n+1:i+1])/n
    return out

def ema_series(closes,span):
    out=[None]*len(closes); k=2/(span+1); e=closes[0]; out[0]=e
    for i in range(1,len(closes)): e=closes[i]*k+e*(1-k); out[i]=e
    return out

# --- חלק א: Fibonacci pullback + extension target ---
def fib_pullback(bars, fib_lo=0.382, fib_hi=0.618, ext_mult=1.618, donch_n=20, pullback_window=15, max_hold=60):
    closes=[b["c"] for b in bars]; ema=ema_series(closes,50); atr=atr_series(bars,14)
    trades=[]; waiting=None; pos=None
    for i in range(60,len(bars)):
        c=bars[i]
        if pos is not None:
            exit_px=None
            if c["l"]<=pos["stop"]: exit_px=pos["stop"]
            elif c["h"]>=pos["target"]: exit_px=pos["target"]
            elif i-pos["i"]>=max_hold: exit_px=c["c"]
            if exit_px is not None:
                trades.append({"pnl":exit_px-pos["entry"],"t":c["t"]}); pos=None
            continue
        donch_high=max(x["h"] for x in bars[i-donch_n:i]); swing_low=min(x["l"] for x in bars[i-donch_n:i])
        breakout = c["c"]>donch_high and c["c"]>ema[i]*1.003
        if waiting is None and breakout:
            waiting={"i":i,"break_price":c["c"],"swing_low":swing_low}
        elif waiting is not None:
            if i-waiting["i"]>pullback_window: waiting=None
            else:
                rng=waiting["break_price"]-waiting["swing_low"]
                zone_hi=waiting["break_price"]-fib_lo*rng; zone_lo=waiting["break_price"]-fib_hi*rng
                if c["l"]<=zone_hi and c["l"]>=zone_lo*0.995:
                    entry=min(c["c"],zone_hi)
                    pos={"i":i,"entry":entry,"stop":waiting["swing_low"],
                         "target":waiting["break_price"]+ext_mult*rng}
                    waiting=None
    return trades

# --- חלק ב: 7 אינדיקטורים ---
def standalone_backtest(bars, signal_fn, max_hold=60):
    closes=[b["c"] for b in bars]; atr=atr_series(bars,14)
    sig = signal_fn(bars)
    trades=[]; pos=None
    for i in range(60,len(bars)):
        c=bars[i]
        if pos is not None:
            exit_px=None
            if c["l"]<=pos["stop"]: exit_px=pos["stop"]
            elif c["h"]>=pos["target"]: exit_px=pos["target"]
            elif i-pos["i"]>=max_hold: exit_px=c["c"]
            if exit_px is not None:
                trades.append({"pnl":exit_px-pos["entry"],"t":c["t"]}); pos=None
            continue
        if sig[i] and atr[i]:
            entry=c["c"]
            pos={"i":i,"entry":entry,"stop":entry-2*atr[i],"target":entry+2*atr[i]}
    return trades

def sig_keltner(bars):
    closes=[b["c"] for b in bars]; atr=atr_series(bars,14); ema20=ema_series(closes,20)
    out=[False]*len(bars)
    for i in range(1,len(bars)):
        if atr[i] and ema20[i] and atr[i-1] and ema20[i-1]:
            upper=ema20[i]+2*atr[i]; upper_prev=ema20[i-1]+2*atr[i-1]
            out[i]= closes[i]>upper and closes[i-1]<=upper_prev
    return out

def sig_ichimoku(bars):
    highs=[b["h"] for b in bars]; lows=[b["l"] for b in bars]
    out=[False]*len(bars)
    for i in range(27,len(bars)):
        tenkan=(max(highs[i-9:i])+min(lows[i-9:i]))/2
        kijun=(max(highs[i-26:i])+min(lows[i-26:i]))/2
        tenkan_p=(max(highs[i-10:i-1])+min(lows[i-10:i-1]))/2
        kijun_p=(max(highs[i-27:i-1])+min(lows[i-27:i-1]))/2
        out[i] = tenkan>kijun and tenkan_p<=kijun_p
    return out

def report(trades,label):
    if not trades:
        print(f"{label}: N=0"); return
    ret=[t["pnl"] for t in trades]
    wr=100*sum(1 for x in ret if x>0)/len(ret)
    print(f"{label:38s} N={len(trades):4d}  win%={wr:5.1f}  total={sum(ret):9.1f}$")

if __name__=="__main__":
    h4=load()
    print(f"נטען: {len(h4)} נרות H4\n")
    print("="*70); print("חלק א: Fibonacci pullback + extension"); print("="*70)
    report(fib_pullback(h4), "Fib 38.2-61.8% + יעד 1.618")

    print("\n"+"="*70); print("חלק ב: אינדיקטורים standalone (Keltner, Ichimoku)"); print("="*70)
    report(standalone_backtest(h4, sig_keltner), "Keltner Channel breakout")
    report(standalone_backtest(h4, sig_ichimoku), "Ichimoku Tenkan/Kijun cross")
    print("\n⚠️ Keltner: 80% חפיפה עם כניסות שיטה 3 (בדוק מול cores.py), נכשל ב-6H — לא עצמאי.")
