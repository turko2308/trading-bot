"""
tools/feature_screen.py — סינון 16 מאפיינים לא-קונבנציונליים (variance ratio, entropy, jumps, tick-volume flow, Renko brick rate...) (20/09/2026)
מדד: Spearman IC בדגימה לא-חופפת, train=עד 06/2025 מול holdout=07/2025+. 90 בדיקות; סף Bonferroni |t|≈3.4.
תוצאה 20/09: t מקסימלי באימון 2.38; בהחזק כל ה-IC בערך 0. אין מאפיין שעבר.
הרצה מתוך tools/: python feature_screen.py (קורא ../data/xauusd_h1.csv)
"""
import numpy as np, pandas as pd, itertools, warnings; warnings.filterwarnings("ignore")
from scipy.stats import spearmanr
d=pd.read_csv("../data/xauusd_h1.csv"); d["t"]=pd.to_datetime(d["timestamp"],utc=True).dt.tz_localize(None); d=d.set_index("t")
o,h,l,c,v=[d[k].values.astype(float) for k in ("open","high","low","close","volume")]; n=len(d); T=d.index
r=np.diff(np.log(c),prepend=np.nan)
S=lambda x,w: pd.Series(x)
F={}
def roll(f,w): return np.array([f(i,w) if i>=w else np.nan for i in range(n)])
rs=pd.Series(r)
F["VR4_72"]=(pd.Series(np.log(c)).diff(4).rolling(72).var()/(4*rs.rolling(72).var())).values
def pe(i,w=48):
    x=r[i-w+1:i+1]; 
    if np.isnan(x).any(): return np.nan
    pats={}
    for k in range(len(x)-2):
        p=tuple(np.argsort(x[k:k+3])); pats[p]=pats.get(p,0)+1
    q=np.array(list(pats.values()),float)/sum(pats.values()); return -(q*np.log(q)).sum()/np.log(6)
F["PERM_ENTROPY48"]=roll(pe,48)
rv=rs.pow(2).rolling(24).sum(); bv=(np.pi/2)*(rs.abs()*rs.abs().shift(1)).rolling(24).sum()
F["JUMP24"]=((rv-bv)/rv).values
park=(np.log(h/l)**2/(4*np.log(2)))
F["INTRABAR_EFF48"]=(pd.Series(park).rolling(48).sum()/rs.pow(2).rolling(48).sum()).values
F["TORTUOSITY24"]=(pd.Series(np.abs(np.diff(c,prepend=np.nan))).rolling(24).sum()/pd.Series(np.abs(c-pd.Series(c).shift(24).values))).values
# renko 10$ brick rate & reversal share over last 24 bars
ref=c[0]; ev=np.zeros(n); rev=np.zeros(n); last=0
for i in range(n):
    while c[i]-ref>=10: ref+=10; ev[i]+=1; rev[i]+= (last==-1); last=1
    while ref-c[i]>=10: ref-=10; ev[i]+=1; rev[i]+= (last==1); last=-1
F["BRICKS24"]=pd.Series(ev).rolling(24).sum().values
F["BRICK_REV24"]=(pd.Series(rev).rolling(24).sum()/(pd.Series(ev).rolling(24).sum()+1e-9)).values
lv=np.log(v); F["VOLZ168"]=((pd.Series(lv)-pd.Series(lv).rolling(168).mean())/pd.Series(lv).rolling(168).std()).values
hod=pd.Series(v,index=T); F["RELVOL_HOD"]=(hod/hod.groupby(T.hour).transform(lambda s:s.rolling(60,min_periods=20).median().shift(1))).values
sg=np.sign(c-o)
F["IMB12"]=(pd.Series(v*sg).rolling(12).sum()/pd.Series(v).rolling(12).sum()).values
clv=(2*c-h-l)/np.where(h>l,h-l,np.nan)
F["CLVFLOW12"]=(pd.Series(v*clv).rolling(12).sum()/pd.Series(v).rolling(12).sum()).values
F["VOLVOL_COUPLE24"]=pd.Series(np.abs(r)).rolling(24).corr(pd.Series(v)).values
F["SKEW48"]=rs.rolling(48).skew().values; F["KURT48"]=rs.rolling(48).kurt().values
rvol6=rs.rolling(6).std(); F["VOLOFVOL48"]=(rvol6.rolling(48).std()/rvol6.rolling(48).mean()).values
past=np.sign(c-pd.Series(c).shift(24).values)          # 24h trend direction
SPLIT=pd.Timestamp("2025-07-01"); res=[]
for H in (2,4,6):
    fwd=pd.Series(c).shift(-H).values-c
    okt=(pd.Series(T).shift(-H)-pd.Series(T)).dt.total_seconds().values==H*3600
    for name,f in F.items():
        for kind in ("direct","cont"):
            x=f if kind=="direct" else f*past
            y=fwd
            m=okt&~np.isnan(x)&~np.isnan(y)&(np.arange(n)%H==0)&~np.isnan(past)
            out=[]
            for lab,sel in (("train",m&(T<SPLIT)),("hold",m&(T>=SPLIT))):
                if sel.sum()<200: out.append((np.nan,np.nan)); continue
                ic=spearmanr(x[sel],y[sel])[0]; out.append((ic,ic*np.sqrt(sel.sum())))
            res.append((H,name,kind,out[0][0],out[0][1],out[1][0],out[1][1]))
R=pd.DataFrame(res,columns=["H","feature","kind","ic_tr","t_tr","ic_ho","t_ho"])
print("tests:",len(R)," | Bonferroni threshold |t|≈%.2f"%3.4)
print("\nTOP 12 by |t| on TRAIN (2023-09…2025-06) and what happened on HOLDOUT (2025-07…2026-09):")
R["absT"]=R.t_tr.abs(); top=R.sort_values("absT",ascending=False).head(12)
print(top.drop(columns="absT").round(3).to_string(index=False))
print("\nPassing train |t|>=3.4 AND holdout same sign with |t|>=1.65:",int(((R.absT>=3.4)&(np.sign(R.ic_tr)==np.sign(R.ic_ho))&(R.t_ho.abs()>=1.65)).sum()))
print("Overall: share of features whose holdout sign matches train among train |t|>=2:", round(float(((R.absT>=2)&(np.sign(R.ic_tr)==np.sign(R.ic_ho))).sum()/max(1,(R.absT>=2).sum())),2))
R.to_pickle("screen.pkl")
