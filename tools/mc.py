import random, statistics
from be import load, sim, maxdd
random.seed(42)
h4 = load("xauusd_h4.csv")

def mc(det, iters=2000):
    p = [t["pnl"] for t in det]
    dds, ends = [], []
    for _ in range(iters):
        random.shuffle(p)
        eq=peak=0.0; dd=0.0
        for x in p:
            eq+=x; peak=max(peak,eq); dd=min(dd,eq-peak)
        dds.append(dd); ends.append(eq)
    dds.sort()
    return statistics.median(dds), dds[int(0.05*len(dds))], dds[0]

def boot(det, iters=2000):
    p=[t["pnl"] for t in det]; n=len(p); tot=[]
    for _ in range(iters):
        s=sum(random.choice(p) for _ in range(n)); tot.append(s)
    tot.sort()
    return sum(1 for x in tot if x>0)/len(tot)*100, tot[int(0.05*len(tot))], tot[int(0.95*len(tot))]

print("=== Monte Carlo (2,000 ערבובי סדר) — ירידה מקסימלית ===")
print(f"{'וריאנט':<16}{'חציון':>10}{'5% גרוע':>10}{'הכי גרוע':>11}")
print("-"*48)
for trig,off in [(None,0),(25,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    d = sim(h4, be_trigger=trig, be_offset=off)["detail"]
    m,p5,w = mc(d)
    print(f"{name:<16}{m:>10.0f}{p5:>10.0f}{w:>11.0f}")

print("\n=== Bootstrap (2,000 דגימות) — הסתברות לרווח ===")
print(f"{'וריאנט':<16}{'P(רווח)':>10}{'5%':>9}{'95%':>9}")
print("-"*46)
for trig,off in [(None,0),(25,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    d = sim(h4, be_trigger=trig, be_offset=off)["detail"]
    pr,lo,hi = boot(d)
    print(f"{name:<16}{pr:>9.1f}%{lo:>9.0f}{hi:>9.0f}")

print("\n=== החלקה 10$ + Monte Carlo (התרחיש האמיתי שלך) ===")
for trig,off in [(None,0),(25,3)]:
    name = "בסיס" if trig is None else f"BE @ {trig}$ +{off}"
    r = sim(h4, slippage=10, be_trigger=trig, be_offset=off)
    m,p5,w = mc(r["detail"]); pr,lo,hi = boot(r["detail"])
    print(f"{name:<16} רווח {r['pnl']:>5.0f} | P(רווח) {pr:>5.1f}% | MC חציון {m:>6.0f} | גרוע {w:>6.0f}")
