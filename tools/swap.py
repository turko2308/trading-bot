import statistics
from be import load, sim, maxdd
h4=load("xauusd_h4.csv")

def apply_carry(det, ils_per_day):
    out=[]
    for t in det:
        c=dict(t); c["pnl"]=t["pnl"]-ils_per_day*t["days"]; out.append(c)
    return out

def stats(det):
    n=len(det); w=[x for x in det if x["pnl"]>0]
    return n, 100*len(w)/n, sum(x["pnl"] for x in det), sum(x["pnl"] for x in det)/n

for nm,kw in [("בסיס",dict()),("BE 25+3",dict(be_trigger=25,be_offset=3))]:
    r=sim(h4,**kw); d=r["detail"]
    days=[x["days"] for x in d]
    print(f"\n### {nm}")
    print(f"החזקה: ממוצע {statistics.mean(days):.1f} ימים | חציון {statistics.median(days):.1f} | הכי ארוך {max(days):.0f}")
    print(f"סה\"כ ימי חשיפה: {sum(days):.0f}")
    print(f"{'עלות/יום':>10}{'רווח':>9}{'לעסקה':>9}{'הצל%':>8}")
    for c in [0,1,2,3,4,5,7,10]:
        n,wr,p,per = stats(apply_carry(d,c))
        print(f"{c:>9.0f}₪{p:>9.0f}{per:>9.1f}{wr:>7.0f}%")
