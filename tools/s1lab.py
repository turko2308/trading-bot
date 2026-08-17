import pickle, datetime, statistics as st
D=pickle.load(open("prep.pkl","rb"))
m15=D["m15"]; ema=D["ema"]
SPREAD=0.77; OZ=0.75; R=3.0014
h1t=sorted(ema)

def ema_at(t):
    # ה-EMA של הנר השעתי הסגור האחרון
    k=t.replace(minute=0,second=0,microsecond=0)-datetime.timedelta(hours=1)
    return ema.get(k)

def signals(dev_min):
    """איתות שיטה 1 מפושט: חציית EMA50 שעתי בנר m15 + מרחק מינימלי מה-EMA."""
    out=[]; prev=None
    for b in m15:
        e=ema_at(b["t"])
        if e is None: prev=None; continue
        side = 1 if b["c"]>e else -1
        if prev is not None and side!=prev:
            dev=abs(b["c"]-e)/e*100
            if dev>=dev_min:
                out.append({"t":b["t"],"d":side,"e":b["c"],"dev":dev})
        prev=side
    return out

def run(sigs, mode, target=None, stop=10.0, trail_h=None, slip=0.0, timeout_h=6, be=None):
    """mode: 'fixed' = יעד+סטופ  |  'trail' = סטופ נגרר לפי שפל/שיא N שעות"""
    idx={b["t"]:i for i,b in enumerate(m15)}
    res=[]; last_end=None
    for s in sigs:
        if last_end and s["t"]<=last_end: continue
        i=idx.get(s["t"])
        if i is None or i+1>=len(m15): continue
        ent=s["e"]+slip*s["d"]
        long_= s["d"]==1
        sl = ent-stop if long_ else ent+stop
        tp = (ent+target if long_ else ent-target) if target else None
        deadline=s["t"]+datetime.timedelta(hours=timeout_h) if timeout_h else None
        peak=ent; hit=None; px=None; when=None
        for j in range(i+1, len(m15)):
            b=m15[j]
            if trail_h:
                w=[x for x in m15[max(0,j-trail_h*4):j]]
                if w:
                    nt = max(sl, min(x["l"] for x in w)) if long_ else min(sl, max(x["h"] for x in w))
                    sl = nt
                if be is not None:
                    mfe=(b["h"]-ent) if long_ else (ent-b["l"])
                    if mfe>=be:
                        bp=ent+3 if long_ else ent-3
                        sl = max(sl,bp) if long_ else min(sl,bp)
            lo,hi=b["l"],b["h"]
            if long_ and lo<=sl: hit,px,when="stop",sl,b["t"]; break
            if not long_ and hi>=sl: hit,px,when="stop",sl,b["t"]; break
            if tp is not None:
                if long_ and hi>=tp: hit,px,when="tp",tp,b["t"]; break
                if not long_ and lo<=tp: hit,px,when="tp",tp,b["t"]; break
            if deadline and b["t"]>=deadline: hit,px,when="to",b["c"],b["t"]; break
        if hit is None: continue
        pts=(px-ent) if long_ else (ent-px)
        pnl=(pts-SPREAD-slip)*OZ*R
        res.append({"pnl":pnl,"pts":pts,"hit":hit,"t":s["t"]})
        last_end=when
    if not res: return None
    eq=0;pk=0;dd=0
    for r in res:
        eq+=r["pnl"]; pk=max(pk,eq); dd=min(dd,eq-pk)
    return {"n":len(res),"pnl":sum(r["pnl"] for r in res),
            "wr":100*sum(1 for r in res if r["pnl"]>0)/len(res),"dd":dd,
            "avg":sum(r["pnl"] for r in res)/len(res)}
