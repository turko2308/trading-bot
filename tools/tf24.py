import pickle, datetime, sys, importlib.util, collections, random, statistics as st
sys.path.insert(0,"/home/claude")
spec=importlib.util.spec_from_file_location("bot","/home/claude/trading_bot_393.py")
bot=importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)
D=pickle.load(open("prep.pkl","rb")); m15=D["m15"]
SP=bot.SPREAD_POINTS; OZ=0.75; R=3.0014

def build(bars,h):
    b={}
    for x in bars:
        k=x["t"].replace(minute=0,second=0,microsecond=0)
        k=k.replace(hour=(k.hour//h)*h)
        g=b.setdefault(k,{"t":k,"o":x["o"],"h":x["h"],"l":x["l"],"c":x["c"]})
        g["h"]=max(g["h"],x["h"]); g["l"]=min(g["l"],x["l"]); g["c"]=x["c"]
    return [b[k] for k in sorted(b)]
h4=build(m15,4); h6=build(m15,6)

def sigs(bars, look=20):
    out=[]
    for i in range(look+60,len(bars)):
        w=bars[i-look:i]
        hh=max(x["h"] for x in w); ll=min(x["l"] for x in w)
        cl=[x["c"] for x in bars[max(0,i-160):i+1]]
        if len(cl)<55: continue
        e=bot.calc_ema_series(cl,50)
        if not e: continue
        ema=e[-1]; c=bars[i]["c"]
        hi=[x["h"] for x in bars[max(0,i-30):i+1]]; lo=[x["l"] for x in bars[max(0,i-30):i+1]]
        atr=bot.calc_atr(hi,lo,cl[-len(hi):])
        if not atr: continue
        if c>hh and c>ema: out.append({"t":bars[i]["t"],"d":1,"e":c,"atr":atr,"tf":"4H" if bars is h4 else "6H"})
        elif c<ll and c<ema: out.append({"t":bars[i]["t"],"d":-1,"e":c,"atr":atr,"tf":"4H" if bars is h4 else "6H"})
    return out

s4=sigs(h4); s6=sigs(h6)
W=datetime.timedelta(hours=4)
s6k=[(x["t"],x["d"]) for x in s6]
for s in s4:
    s["bk"]=any(d==s["d"] and abs(s["t"]-t)<=W for t,d in s6k)

# אינדקס m15 לפי זמן להאצה
idx=sorted(range(len(m15)), key=lambda i:m15[i]["t"])
times=[m15[i]["t"] for i in idx]
import bisect
def resolve(s, slip):
    ent = s["e"] + slip*s["d"]
    tp = ent + 2*s["atr"]*s["d"]
    sl = ent - 2*s["atr"]*s["d"]
    j = bisect.bisect_right(times, s["t"])
    for k in range(j, len(m15)):
        b=m15[k]
        if s["d"]==1:
            if b["l"]<=sl: return "loss", sl, b["t"]
            if b["h"]>=tp: return "win", tp, b["t"]
        else:
            if b["h"]>=sl: return "loss", sl, b["t"]
            if b["l"]<=tp: return "win", tp, b["t"]
    return None,None,None

def money(s, px, slip):
    ent = s["e"] + slip*s["d"]
    pts=(px-ent)*s["d"]
    return (pts-SP)*OZ*R

def run(pool, hours=None, slip=0.0):
    out=[]
    for s in pool:
        if hours and not (hours[0] <= s["t"].hour < hours[1]): continue
        r,px,ct = resolve(s, slip)
        if not r: continue
        out.append({"pnl":money(s,px,slip),"r":r,"t":s["t"],"h":s["t"].hour,"ct":ct})
    return out

def stats(c):
    if not c: return None
    eq=0;pk=0;dd=0
    for x in sorted(c,key=lambda z:z["ct"]):
        eq+=x["pnl"];pk=max(pk,eq);dd=min(dd,eq-pk)
    p=[x["pnl"] for x in c]
    sr = (st.mean(p)/st.pstdev(p)) if len(p)>1 and st.pstdev(p)>0 else 0
    return {"n":len(c),"pnl":sum(p),"wr":100*sum(1 for x in c if x["r"]=="win")/len(c),
            "dd":dd,"avg":st.mean(p),"sr":sr}
