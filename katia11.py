# -*- coding: utf-8 -*-
"""
Katia11 Pro — Termux Edition
ملف واحد | يعمل 24/7 على Android عبر Termux
pip install requests  (الوحيدة المطلوبة)

طريقة التشغيل 24/7 في Termux:
  1. termux-wake-lock          <- يمنع Android من ايقاف البوت
  2. python katia11.py         <- شغّله
  3. اضغط HOME (لا تغلق Termux)

لايقاف البوت لاحقاً:
  pkill -f katia11.py
"""
import os, json, time, logging, threading, signal, sys
from datetime import datetime
from requests import Session
from requests.adapters import HTTPAdapter, Retry

# حماية من ايقاف غير متوقع
def _handle_signal(sig, frame):
    print("\n[Bot] اشارة ايقاف - أوقف البوت بأمان.")
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)

# ══════════════════════════════════════════════
# ضع مفاتيحك هنا فقط
# ══════════════════════════════════════════════
TELEGRAM_TOKEN  = "7749364195:AAHGXvt3Ml61-XSIJ0Kb-iLQ_erx6cKP6FA"
ADMIN_ID        = "6192895163"
TWELVEDATA_KEY  = "1735ad7b45a24467a5e43b494cadbb99"

# ══════════════════════════════════════════════
# اعدادات عامة
# ══════════════════════════════════════════════
MAX_USERS       = 1000
COOLDOWN_SEC    = 20
CACHE_TTL_SEC   = 12 * 60
ALERT_INTERVAL  = 15 * 60
ALERT_COOLDOWN  = 60 * 60
TRACK_INTERVAL  = 3 * 60
MIN_RR          = 2.0
LOG_FILE        = "katia11.log"
USERS_FILE      = "katia11_users.json"
TRACK_FILE      = "katia11_track.json"
STATS_FILE      = "katia11_stats.json"

# اوزان التحليل (مجموعها ~100)
W = {
    "htf_d1":12,"htf_h4":8,"htf_h1":5,
    "bos":8,"choch":6,
    "ob_entry":9,"ob_nearby":4,
    "fvg_entry":7,"fvg_nearby":3,
    "liq_sweep":10,"liq_target":3,
    "pd_zone":5,"bookmap":6,"candle":8,"ema_align":6,
}

# عتبات القرار
SCORE_AP=45; SCORE_A=32; SCORE_B=22; SCORE_MIN=15

# اعدادات المحللات
ATR_PERIOD=14; PIVOT_L=5; PIVOT_R=5
LIQ_TOL=0.0012; LIQ_SWEEP_C=0.001
OB_ATR_MIN=1.2; FVG_ATR_MIN=0.3
SL_ATR_MULT=1.5; SL_BUFFER=0.2
TP1_R=0.382; TP2_R=0.618
D1_LIMIT=200; H4_LIMIT=200; H1_LIMIT=200; M15_LIMIT=300

# ══════════════════════════════════════════════
# الاصول
# ══════════════════════════════════════════════
SYMBOLS = {
    "1":{"name":"EUR/USD","source":"twelve","symbol":"EUR/USD","dec":5,"pip":0.0001},
    "2":{"name":"GBP/USD","source":"twelve","symbol":"GBP/USD","dec":5,"pip":0.0001},
    "3":{"name":"USD/JPY","source":"twelve","symbol":"USD/JPY","dec":3,"pip":0.01},
    "4":{"name":"XAU/USD","source":"twelve","symbol":"XAU/USD","alt":"XAUUSD","dec":2,"pip":0.10},
    "5":{"name":"BTC/USDT","source":"binance","symbol":"BTCUSDT","dec":1,"pip":1.0},
    "6":{"name":"ETH/USDT","source":"binance","symbol":"ETHUSDT","dec":2,"pip":0.1},
}

# ══════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("katia11")

# ══════════════════════════════════════════════
# HTTP Session + Cache + Circuit Breaker
# ══════════════════════════════════════════════
def _make_session():
    retry = Retry(total=3, backoff_factor=1.5,
                  status_forcelist=[429,500,502,503,504],
                  allowed_methods=["GET","POST"])
    s = Session()
    s.mount("https://", HTTPAdapter(max_retries=retry,
                                    pool_connections=4, pool_maxsize=12))
    s.headers["User-Agent"] = "Katia11Pro/4.0"
    return s

_sess = _make_session()
_cache = {}
_cache_lock = threading.Lock()

def _cget(key):
    with _cache_lock:
        e = _cache.get(key)
        if e and time.monotonic()-e[0] < CACHE_TTL_SEC:
            return e[1]
    return None

def _cset(key, val):
    with _cache_lock:
        _cache[key] = (time.monotonic(), val)

_cb = {"fails":0,"open":False,"at":0.0}

def _cb_ok():   _cb.update({"fails":0,"open":False})
def _cb_fail():
    _cb["fails"]+=1
    if _cb["fails"]>=5: _cb.update({"open":True,"at":time.monotonic()})

def safe_get(url, params=None, timeout=14):
    if _cb["open"]:
        if time.monotonic()-_cb["at"]>60: _cb.update({"open":False,"fails":0})
        else: return None
    try:
        r=_sess.get(url, params=params, timeout=timeout)
        r.raise_for_status(); _cb_ok()
        return r.json()
    except Exception as e:
        log.warning("GET %s: %s", url[:50], e)
        _cb_fail(); return None

# ══════════════════════════════════════════════
# TwelveData
# ══════════════════════════════════════════════
TD_BASE="https://api.twelvedata.com"
TD_IV={"d1":"1day","h4":"4h","h1":"1h","m15":"15min"}

def td_klines(symbol, interval, limit, alt=None):
    ck="td:{}:{}".format(symbol,interval)
    c=_cget(ck)
    if c: return c
    for sym in ([symbol,alt] if alt else [symbol]):
        data=safe_get(TD_BASE+"/time_series",{
            "symbol":sym,"interval":TD_IV[interval],
            "outputsize":limit,"apikey":TWELVEDATA_KEY,"format":"JSON"})
        if not data or data.get("status")=="error": continue
        vals=data.get("values",[])
        if len(vals)<10: continue
        raw=[]
        for v in reversed(vals):
            try: raw.append([v["datetime"],float(v["open"]),float(v["high"]),
                             float(v["low"]),float(v["close"]),float(v.get("volume") or 0)])
            except: continue
        if len(raw)>=10:
            _cset(ck,raw)
            log.info("TD %s [%s] %d candles",sym,interval,len(raw))
            return raw
    return None

# ══════════════════════════════════════════════
# Binance
# ══════════════════════════════════════════════
BN_BASE="https://api.binance.com/api/v3"
BN_IV={"d1":"1d","h4":"4h","h1":"1h","m15":"15m"}

def bn_klines(symbol, interval, limit):
    ck="bn:{}:{}".format(symbol,interval)
    c=_cget(ck)
    if c: return c
    data=safe_get(BN_BASE+"/klines",{"symbol":symbol,"interval":BN_IV[interval],"limit":limit})
    if not isinstance(data,list) or len(data)<10: return None
    raw=[[k[0],float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5])] for k in data]
    _cset(ck,raw); return raw

def bn_depth(symbol):
    d=safe_get(BN_BASE+"/depth",{"symbol":symbol,"limit":200})
    return d if isinstance(d,dict) and d.get("bids") else None

def bn_price(symbol):
    d=safe_get(BN_BASE+"/ticker/price",{"symbol":symbol})
    return float(d["price"]) if d and "price" in d else None

# ══════════════════════════════════════════════
# نموذج الشمعات
# ══════════════════════════════════════════════
class C:
    __slots__=("o","h","l","c","v")
    def __init__(self,raw):
        self.o=[float(k[1]) for k in raw]
        self.h=[float(k[2]) for k in raw]
        self.l=[float(k[3]) for k in raw]
        self.c=[float(k[4]) for k in raw]
        self.v=[float(k[5]) for k in raw]
    def __len__(self): return len(self.c)
    @property
    def last(self): return self.c[-1]
    def tail(self,n):
        cc=C.__new__(C)
        cc.o=self.o[-n:];cc.h=self.h[-n:]
        cc.l=self.l[-n:];cc.c=self.c[-n:]
        cc.v=self.v[-n:]; return cc

def make_c(raw):
    return C(raw) if raw and len(raw)>=10 else None

# ══════════════════════════════════════════════
# جلب السوق (متوازٍ)
# ══════════════════════════════════════════════
def fetch_market(info):
    src=info["source"]; sym=info["symbol"]; alt=info.get("alt")
    res={}; lock=threading.Lock()
    def _get(tf,lim):
        raw=(td_klines(sym,tf,lim,alt) if src=="twelve" else bn_klines(sym,tf,lim))
        with lock: res[tf]=make_c(raw)
    depth=[None]
    threads=[threading.Thread(target=_get,args=t,daemon=True)
             for t in [("d1",D1_LIMIT),("h4",H4_LIMIT),("h1",H1_LIMIT),("m15",M15_LIMIT)]]
    if src=="binance":
        def _dep(): depth[0]=bn_depth(sym)
        threads.append(threading.Thread(target=_dep,daemon=True))
    for t in threads: t.start()
    for t in threads: t.join(timeout=25)
    return res.get("d1"),res.get("h4"),res.get("h1"),res.get("m15"),depth[0]

def fetch_live_price(info):
    if info["source"]=="binance": return bn_price(info["symbol"])
    raw=td_klines(info["symbol"],"m15",2,info.get("alt"))
    return float(raw[-1][4]) if raw else None

# ══════════════════════════════════════════════
# ادوات رياضية
# ══════════════════════════════════════════════
def pivot_highs(h,L=PIVOT_L,R=PIVOT_R):
    n=len(h); out=[]
    for i in range(L,n-R):
        if all(h[i]>h[i-j] for j in range(1,L+1)) and all(h[i]>h[i+j] for j in range(1,R+1)):
            out.append((i,h[i]))
    return out

def pivot_lows(l,L=PIVOT_L,R=PIVOT_R):
    n=len(l); out=[]
    for i in range(L,n-R):
        if all(l[i]<l[i-j] for j in range(1,L+1)) and all(l[i]<l[i+j] for j in range(1,R+1)):
            out.append((i,l[i]))
    return out

def calc_atr(c,p=ATR_PERIOD):
    if len(c.c)<p+1: return None
    v=sum(max(c.h[i]-c.l[i],abs(c.h[i]-c.c[i-1]),abs(c.l[i]-c.c[i-1])) for i in range(1,p+1))/p
    for i in range(p+1,len(c.c)):
        tr=max(c.h[i]-c.l[i],abs(c.h[i]-c.c[i-1]),abs(c.l[i]-c.c[i-1]))
        v=(v*(p-1)+tr)/p
    return v

def calc_ema(prices,period):
    if len(prices)<period: return None
    k=2.0/(period+1); v=sum(prices[:period])/period
    for p in prices[period:]: v=p*k+v*(1-k)
    return v

def trend_of(c):
    if not c or len(c)<30: return "neutral"
    ph=pivot_highs(c.h); pl=pivot_lows(c.l)
    if len(ph)<2 or len(pl)<2: return "neutral"
    hh=ph[-1][1]>ph[-2][1]; lh=ph[-1][1]<ph[-2][1]
    hl=pl[-1][1]>pl[-2][1]; ll=pl[-1][1]<pl[-2][1]
    if hh and hl: return "bullish"
    if ll and lh: return "bearish"
    return "neutral"

# ══════════════════════════════════════════════
# محللات SMC
# ══════════════════════════════════════════════
def analyze_structure(c):
    c=c.tail(100); p=c.last
    ph=pivot_highs(c.h); pl=pivot_lows(c.l)
    if len(ph)<2 or len(pl)<2:
        return dict(trend="neutral",bos="",choch="",last_ph=None,last_pl=None,score=0,ph=len(ph),pl=len(pl))
    lph1,lph2=ph[-1][1],ph[-2][1]; lpl1,lpl2=pl[-1][1],pl[-2][1]
    hh=lph1>lph2; lh=lph1<lph2; hl=lpl1>lpl2; ll=lpl1<lpl2
    trend="neutral"; bos=""; choch=""; score=0
    if hh and hl:
        trend="bullish"
        if p>lph1: bos="BOS صاعد"; score+=W["bos"]
    elif ll and lh:
        trend="bearish"
        if p<lpl1: bos="BOS هابط"; score-=W["bos"]
    if trend=="bullish" and ll: choch="CHoCH هابط"; score-=W["choch"]
    elif trend=="bearish" and hh: choch="CHoCH صاعد"; score+=W["choch"]
    return dict(trend=trend,bos=bos,choch=choch,last_ph=lph1,last_pl=lpl1,score=score,ph=len(ph),pl=len(pl))

def analyze_ob(c,atr_val):
    c=c.tail(100); p=c.last; n=len(c.c)
    avg_v=(sum(c.v)/n) or 1; bodies=[abs(c.c[i]-c.o[i]) for i in range(n)]
    avg_b=(sum(bodies)/n) or 1e-9
    min_mv=(atr_val*OB_ATR_MIN) if atr_val else avg_b*1.5
    bull_obs=[]; bear_obs=[]
    for i in range(2,n-4):
        bi=bodies[i] or 1e-9; vi=c.v[i]
        if c.c[i]<c.o[i]:
            mv=max(c.c[i+1]-c.o[i+1],c.c[i+2]-c.o[i+2],0.0)
            if mv>=min_mv:
                s=min(10.0,(vi/avg_v)*(mv/bi)*0.5)
                bull_obs.append({"top":c.o[i],"bot":c.c[i],"str":round(s,1),"age":n-i,"fresh":p>c.o[i]})
        if c.c[i]>c.o[i]:
            mv=max(c.o[i+1]-c.c[i+1],c.o[i+2]-c.c[i+2],0.0)
            if mv>=min_mv:
                s=min(10.0,(vi/avg_v)*(mv/bi)*0.5)
                bear_obs.append({"top":c.c[i],"bot":c.o[i],"str":round(s,1),"age":n-i,"fresh":p<c.o[i]})
    nb=[o for o in bull_obs if o["top"]<p and o["fresh"]]
    ns=[o for o in bear_obs if o["bot"]>p and o["fresh"]]
    if nb: nb.sort(key=lambda x:(-x["str"],x["age"]))
    if ns: ns.sort(key=lambda x:(-x["str"],x["age"]))
    bull=nb[0] if nb else None; bear=ns[0] if ns else None
    in_bull=bool(bull and bull["bot"]<=p<=bull["top"])
    in_bear=bool(bear and bear["bot"]<=p<=bear["top"])
    score=0
    if in_bull: score+=W["ob_entry"]
    elif nb:
        gp=(p-bull["top"])/(bull["top"]-bull["bot"]+1e-9)
        if gp<0.20: score+=W["ob_nearby"]
    if in_bear: score-=W["ob_entry"]
    elif ns:
        gp=(bear["bot"]-p)/(bear["top"]-bear["bot"]+1e-9)
        if gp<0.20: score-=W["ob_nearby"]
    return dict(bull=bull,bear=bear,in_bull=in_bull,in_bear=in_bear,bull_cnt=len(nb),bear_cnt=len(ns),score=score)

def analyze_fvg(c,atr_val):
    c=c.tail(100); p=c.last; n=len(c.c)
    min_sz=(atr_val*FVG_ATR_MIN) if atr_val else 0.0
    bf=[]; sf=[]
    for i in range(1,n-1):
        if c.l[i+1]>c.h[i-1]:
            sz=c.l[i+1]-c.h[i-1]
            if sz>=min_sz: bf.append({"top":c.l[i+1],"bot":c.h[i-1],"size":sz})
        if c.h[i+1]<c.l[i-1]:
            sz=c.l[i-1]-c.h[i+1]
            if sz>=min_sz: sf.append({"top":c.l[i-1],"bot":c.h[i+1],"size":sz})
    ob=[f for f in bf if f["top"]<p]; os_=[f for f in sf if f["bot"]>p]
    nb=max(ob,key=lambda x:x["top"]) if ob else None
    ns=min(os_,key=lambda x:x["bot"]) if os_ else None
    in_bull=bool(nb and nb["bot"]<=p<=nb["top"]); in_bear=bool(ns and ns["bot"]<=p<=ns["top"])
    score=0
    if in_bull: score+=W["fvg_entry"]
    elif nb:
        if (p-nb["top"])/(nb["size"]+1e-9)<0.15: score+=W["fvg_nearby"]
    if in_bear: score-=W["fvg_entry"]
    elif ns:
        if (ns["bot"]-p)/(ns["size"]+1e-9)<0.15: score-=W["fvg_nearby"]
    return dict(near_bull=nb,near_bear=ns,in_bull=in_bull,in_bear=in_bear,bull_cnt=len(ob),bear_cnt=len(os_),score=score)

def analyze_liq(c):
    c=c.tail(150); p=c.last
    def cluster(vals):
        zns=[]; vis=set()
        for i in range(len(vals)):
            if i in vis: continue
            cl=[vals[i]]
            for j in range(i+1,len(vals)):
                if j not in vis and abs(vals[j]-vals[i])/vals[i]<LIQ_TOL:
                    cl.append(vals[j]); vis.add(j)
            if len(cl)>=2: zns.append({"level":sum(cl)/len(cl),"count":len(cl)})
            vis.add(i)
        return zns
    bsl=cluster(c.h); ssl=cluster(c.l)
    above=[z for z in bsl if z["level"]>p]; below=[z for z in ssl if z["level"]<p]
    nb_bsl=min(above,key=lambda x:abs(x["level"]-p)) if above else None
    nb_ssl=min(below,key=lambda x:abs(x["level"]-p)) if below else None
    sw_bull=sw_bear=False
    if len(c.h)>=10:
        rh=max(c.h[-10:]); rl=min(c.l[-10:])
        for z in bsl:
            if rh>z["level"]*(1+LIQ_SWEEP_C) and p<z["level"]: sw_bear=True; break
        for z in ssl:
            if rl<z["level"]*(1-LIQ_SWEEP_C) and p>z["level"]: sw_bull=True; break
    score=0
    if sw_bull: score+=W["liq_sweep"]
    if sw_bear: score-=W["liq_sweep"]
    if nb_bsl and abs(p-nb_bsl["level"])/p<0.003: score-=W["liq_target"]
    if nb_ssl and abs(p-nb_ssl["level"])/p<0.003: score+=W["liq_target"]
    return dict(near_bsl=nb_bsl,near_ssl=nb_ssl,sweep_bull=sw_bull,sweep_bear=sw_bear,bsl_cnt=len(above),ssl_cnt=len(below),score=score)

def analyze_pd(c):
    c=c.tail(200); h=max(c.h); l=min(c.l); p=c.last
    if h==l: return dict(zone="محايد",pct=50.0,eq=None,fib618=None,score=0,rh=h,rl=l)
    rng=h-l; pct=(p-l)/rng*100; eq=l+rng*0.50; f618=l+rng*0.618
    if   pct>=78.6: zone,score="Premium عميق",-W["pd_zone"]
    elif pct>=70.5: zone,score="Silver Zone 70.5%",-int(W["pd_zone"]*0.7)
    elif pct>=61.8: zone,score="Golden Zone 61.8%",-int(W["pd_zone"]*0.4)
    elif pct>=50.0: zone,score="Equilibrium",0
    elif pct>=29.0: zone,score="Discount Zone",int(W["pd_zone"]*0.7)
    else:           zone,score="Discount عميق",W["pd_zone"]
    return dict(zone=zone,pct=pct,eq=eq,fib618=f618,score=score,rh=h,rl=l)

def analyze_htf(d1,h4,h1):
    td1=trend_of(d1); th4=trend_of(h4); th1=trend_of(h1)
    score=0
    if td1=="bullish": score+=W["htf_d1"]
    elif td1=="bearish": score-=W["htf_d1"]
    if th4=="bullish": score+=W["htf_h4"]
    elif th4=="bearish": score-=W["htf_h4"]
    if th1=="bullish": score+=W["htf_h1"]
    elif th1=="bearish": score-=W["htf_h1"]
    if td1=="bullish" and th4=="bullish" and th1=="bullish": lbl="صاعد قوي (D1+H4+H1)"
    elif td1=="bearish" and th4=="bearish" and th1=="bearish": lbl="هابط قوي (D1+H4+H1)"
    elif score>12: lbl="صاعد (D1+H4)"
    elif score<-12: lbl="هابط (D1+H4)"
    elif score>0: lbl="ميل صاعد"
    elif score<0: lbl="ميل هابط"
    else: lbl="محايد"
    return dict(label=lbl,d1=td1,h4=th4,h1=th1,score=score)

def analyze_ema(c):
    e21=calc_ema(c.c,21); e55=calc_ema(c.c,55); e200=calc_ema(c.c,200)
    score=0; ab=False; be=False
    if e21 and e55 and e200:
        if e21>e55>e200: ab=True; score=W["ema_align"]
        elif e21<e55<e200: be=True; score=-W["ema_align"]
    return dict(e21=e21,e55=e55,e200=e200,aligned_bull=ab,aligned_bear=be,score=score)

def analyze_bookmap(depth):
    if not depth: return dict(signal="غير متوفر",score=0,ratio=50.0,whale_b=0,whale_a=0)
    try:
        bids=[(float(b[0]),float(b[1])) for b in depth["bids"]]
        asks=[(float(a[0]),float(a[1])) for a in depth["asks"]]
        vb=sum(q for _,q in bids); va=sum(q for _,q in asks); tot=vb+va
        if tot==0: return dict(signal="توازن",score=0,ratio=50.0,whale_b=0,whale_a=0)
        ratio=vb/tot*100
        avg_b=vb/len(bids) if bids else 1; avg_a=va/len(asks) if asks else 1
        wb=sum(1 for _,q in bids if q>avg_b*3); wa=sum(1 for _,q in asks if q>avg_a*3)
        if ratio>65 and wb>=2:   sig,sc="تراكم شراء مؤسسي",W["bookmap"]
        elif ratio<35 and wa>=2: sig,sc="ضغط بيع مؤسسي",-W["bookmap"]
        elif ratio>58:           sig,sc="ميل شراء",int(W["bookmap"]*0.4)
        elif ratio<42:           sig,sc="ميل بيع",-int(W["bookmap"]*0.4)
        else:                    sig,sc="توازن",0
        return dict(signal=sig,score=sc,ratio=ratio,whale_b=wb,whale_a=wa)
    except Exception as e:
        log.error("bookmap: %s",e)
        return dict(signal="خطأ",score=0,ratio=50.0,whale_b=0,whale_a=0)

def detect_pattern(c):
    n=len(c.c)
    if n<2: return "neutral"
    i=n-1
    o1,h1_,l1,c1=c.o[i-1],c.h[i-1],c.l[i-1],c.c[i-1]
    o2,h2,l2,c2=c.o[i],c.h[i],c.l[i],c.c[i]
    b1=abs(c1-o1); b2=abs(c2-o2); rng2=(h2-l2) or 1e-9
    if c1<o1 and c2>o2 and c2>o1 and o2<c1 and b2>b1*0.8: return "bullish_engulf"
    if c1>o1 and c2<o2 and c2<o1 and o2>c1 and b2>b1*0.8: return "bearish_engulf"
    lw=min(o2,c2)-l2; uw=h2-max(o2,c2)
    if lw>rng2*0.6 and b2<rng2*0.35 and c2>=o2: return "pin_bull"
    if uw>rng2*0.6 and b2<rng2*0.35 and c2<=o2: return "pin_bear"
    if b2<rng2*0.1: return "doji"
    return "neutral"

def analyze_confirm(c,ob,fvg,pre_score):
    pat=detect_pattern(c)
    is_bull=pre_score>=SCORE_MIN; is_bear=pre_score<=-SCORE_MIN
    score=0; entry_type="انتظار"
    PAT={
        "bullish_engulf":"Bullish Engulfing",
        "bearish_engulf":"Bearish Engulfing",
        "pin_bull":"Pin Bar صاعد",
        "pin_bear":"Pin Bar هابط",
        "doji":"Doji","neutral":"لا نمط"
    }
    pat_s=PAT.get(pat,"—")
    if is_bull:
        in_z=ob["in_bull"] or fvg["in_bull"]
        if pat=="bullish_engulf" and in_z: score=W["candle"]; entry_type="دخول فوري"
        elif pat=="pin_bull" and in_z: score=int(W["candle"]*0.8); entry_type="دخول فوري"
        elif pat in ("bullish_engulf","pin_bull"): score=int(W["candle"]*0.5); entry_type="دخول محتمل"
        elif in_z: score=int(W["candle"]*0.25); entry_type="انتظر شمعة"
    elif is_bear:
        in_z=ob["in_bear"] or fvg["in_bear"]
        if pat=="bearish_engulf" and in_z: score=-W["candle"]; entry_type="دخول فوري"
        elif pat=="pin_bear" and in_z: score=-int(W["candle"]*0.8); entry_type="دخول فوري"
        elif pat in ("bearish_engulf","pin_bear"): score=-int(W["candle"]*0.5); entry_type="دخول محتمل"
        elif in_z: score=-int(W["candle"]*0.25); entry_type="انتظر شمعة"
    return dict(pattern=pat,pat_s=pat_s,score=score,entry_type=entry_type)

# ══════════════════════════════════════════════
# القرار النهائي
# ══════════════════════════════════════════════
def make_decision(ms,ob,fvg,liq,pd,htf,ema,bk,conf):
    score=round(ms["score"]+ob["score"]+fvg["score"]+liq["score"]+
                pd["score"]+htf["score"]+ema["score"]+bk["score"]+conf["score"],1)
    events=[]
    if htf["score"]!=0: events.append("HTF: "+htf["label"])
    if ms["bos"]:   events.append(ms["bos"])
    if ms["choch"]: events.append(ms["choch"])
    if ob["in_bull"]: events.append("OB صاعد (str="+str(ob["bull"]["str"])+")")
    if ob["in_bear"]: events.append("OB هابط (str="+str(ob["bear"]["str"])+")")
    if fvg["in_bull"]: events.append("FVG صاعد")
    if fvg["in_bear"]: events.append("FVG هابط")
    if liq["sweep_bull"]: events.append("اصطياد سيولة صاعد")
    if liq["sweep_bear"]: events.append("اصطياد سيولة هابط")
    if ema["aligned_bull"]: events.append("EMA صاعد")
    if ema["aligned_bear"]: events.append("EMA هابط")
    if bk["score"]!=0: events.append("BookMap: "+bk["signal"])
    if conf["entry_type"]=="دخول فوري": events.append(conf["pat_s"]+" -> "+conf["entry_type"])
    events=events[:8]
    if   score>=SCORE_AP:  sig,q,dr="شراء قوي - اعداد مثالي","A+","bull"
    elif score>=SCORE_A:   sig,q,dr="شراء - شروط SMC مكتملة","A","bull"
    elif score>=SCORE_B:   sig,q,dr="شراء محتمل - انتظر تاكيداً","B","bull"
    elif score<=-SCORE_AP: sig,q,dr="بيع قوي - اعداد مثالي","A+","bear"
    elif score<=-SCORE_A:  sig,q,dr="بيع - شروط SMC مكتملة","A","bear"
    elif score<=-SCORE_B:  sig,q,dr="بيع محتمل - انتظر تاكيداً","B","bear"
    else:                  sig,q,dr="انتظار - لا اعداد واضح","C","neutral"
    return dict(signal=sig,quality=q,score=score,direction=dr,events=events)

# ══════════════════════════════════════════════
# TP/SL ثلاثة اهداف
# ══════════════════════════════════════════════
def calc_tpsl(price,score,ob,fvg,liq,atr_val,pip):
    if not atr_val or abs(score)<SCORE_MIN: return None
    is_bull=score>0; margin=atr_val*SL_BUFFER
    if is_bull:
        ref=ob.get("bull") or fvg.get("near_bull")
        sl=(ref.get("bot",price-atr_val*SL_ATR_MULT)-margin) if ref else price-atr_val*SL_ATR_MULT
        sl=min(sl,price-pip*5)
    else:
        ref=ob.get("bear") or fvg.get("near_bear")
        sl=(ref.get("top",price+atr_val*SL_ATR_MULT)+margin) if ref else price+atr_val*SL_ATR_MULT
        sl=max(sl,price+pip*5)
    risk=abs(price-sl) or pip
    if is_bull:
        cands=[]
        if ob.get("bear") and ob["bear"]["bot"]>price: cands.append(ob["bear"]["bot"]-margin)
        if liq.get("near_bsl") and liq["near_bsl"]["level"]>price: cands.append(liq["near_bsl"]["level"]-margin)
        if fvg.get("near_bear") and fvg["near_bear"]["bot"]>price: cands.append(fvg["near_bear"]["bot"]-margin)
        tp3=max(cands) if cands else price+risk*4
        tp3=max(tp3,price+risk*MIN_RR)
        rng=tp3-price; tp1=price+rng*TP1_R; tp2=price+rng*TP2_R
    else:
        cands=[]
        if ob.get("bull") and ob["bull"]["top"]<price: cands.append(ob["bull"]["top"]+margin)
        if liq.get("near_ssl") and liq["near_ssl"]["level"]<price: cands.append(liq["near_ssl"]["level"]+margin)
        if fvg.get("near_bull") and fvg["near_bull"]["top"]<price: cands.append(fvg["near_bull"]["top"]+margin)
        tp3=min(cands) if cands else price-risk*4
        tp3=min(tp3,price-risk*MIN_RR)
        rng=price-tp3; tp1=price-rng*TP1_R; tp2=price-rng*TP2_R
    return dict(tp1=tp1,tp2=tp2,tp3=tp3,sl=sl,
                rr1=round(abs(price-tp1)/risk,2),
                rr3=round(abs(price-tp3)/risk,2),
                risk_pips=round(risk/pip),
                reward_pips=round(abs(price-tp3)/pip))

# ══════════════════════════════════════════════
# التحليل الكامل
# ══════════════════════════════════════════════
def run_analysis(info):
    d1,h4,h1,m15,depth=fetch_market(info)
    if not m15: return None
    try:
        p=m15.last; pip=info["pip"]; atr_val=calc_atr(m15)
        ms=analyze_structure(m15); ob=analyze_ob(m15,atr_val)
        fvg=analyze_fvg(m15,atr_val); liq=analyze_liq(m15)
        pd=analyze_pd(m15); htf=analyze_htf(d1,h4,h1)
        ema=analyze_ema(m15); bk=analyze_bookmap(depth)
        pre=make_decision(ms,ob,fvg,liq,pd,htf,ema,bk,
                          dict(pattern="neutral",pat_s="—",score=0,entry_type="انتظار"))
        conf=analyze_confirm(m15,ob,fvg,pre["score"])
        dec=make_decision(ms,ob,fvg,liq,pd,htf,ema,bk,conf)
        tpsl=calc_tpsl(p,dec["score"],ob,fvg,liq,atr_val,pip)
        return dict(name=info["name"],price=p,source=info["source"],
                    ms=ms,ob=ob,fvg=fvg,liq=liq,pd=pd,htf=htf,ema=ema,bk=bk,
                    conf=conf,dec=dec,atr=atr_val,tpsl=tpsl)
    except Exception as e:
        log.exception("analysis: %s",e); return None

# ══════════════════════════════════════════════
# تنسيق الرسائل
# ══════════════════════════════════════════════
def F(v,d):
    if v is None: return "—"
    return "{:.{}f}".format(v,d)

def build_msg(r,d):
    f=lambda v:F(v,d)
    dec=r["dec"]; sc=dec["score"]; q=dec["quality"]
    if   sc>=SCORE_AP:  hdr="شراء قوي"; icon="🟢🟢🟢"
    elif sc>=SCORE_A:   hdr="شراء";     icon="🟢🟢"
    elif sc>=SCORE_B:   hdr="شراء محتمل"; icon="🟡🟢"
    elif sc<=-SCORE_AP: hdr="بيع قوي";  icon="🔴🔴🔴"
    elif sc<=-SCORE_A:  hdr="بيع";      icon="🔴🔴"
    elif sc<=-SCORE_B:  hdr="بيع محتمل"; icon="🟡🔴"
    else:               hdr="انتظار";   icon="⚪⚪"
    qi={"A+":"🏆🏆","A":"🏆","B":"⭐","C":"⏳"}.get(q,"⏳")
    src="TwelveData" if r["source"]=="twelve" else "Binance"
    ob=r["ob"]; fvg=r["fvg"]; liq=r["liq"]; ema=r["ema"]
    ob_b=ob["bull"]; ob_s=ob["bear"]
    ob_bs=(f"{f(ob_b['bot'])} - {f(ob_b['top'])} (str={ob_b['str']})") if ob_b else "—"
    ob_ss=(f"{f(ob_s['bot'])} - {f(ob_s['top'])} (str={ob_s['str']})") if ob_s else "—"
    nb=fvg["near_bull"]; ns=fvg["near_bear"]
    fvg_bs=(f"{f(nb['bot'])} - {f(nb['top'])}") if nb else "—"
    fvg_ss=(f"{f(ns['bot'])} - {f(ns['top'])}") if ns else "—"
    bsl=liq["near_bsl"]; ssl_=liq["near_ssl"]
    bsl_s=(f"{f(bsl['level'])} ({bsl['count']} قمة)") if bsl else "—"
    ssl_s=(f"{f(ssl_['level'])} ({ssl_['count']} قاع)") if ssl_ else "—"
    ema_s=("EMA صاعد (21>55>200)" if ema["aligned_bull"] else "EMA هابط (21<55<200)" if ema["aligned_bear"] else "—")
    bk=r["bk"]; conf=r["conf"]; tpsl=r["tpsl"]
    tp1=f(tpsl["tp1"]) if tpsl else "—"
    tp2=f(tpsl["tp2"]) if tpsl else "—"
    tp3=f(tpsl["tp3"]) if tpsl else "—"
    sl_=f(tpsl["sl"])  if tpsl else "—"
    rr1="1:{}".format(tpsl["rr1"]) if tpsl else "—"
    rr3="1:{}".format(tpsl["rr3"]) if tpsl else "—"
    rp="{} pips".format(tpsl["risk_pips"]) if tpsl else "—"
    wp="{} pips".format(tpsl["reward_pips"]) if tpsl else "—"
    events="\n".join("  * {}".format(e) for e in dec["events"]) or "  * لا اشارات"
    ob_in=("داخل OB صاعد!" if ob["in_bull"] else "داخل OB هابط!" if ob["in_bear"] else "")
    fvg_in=("داخل FVG صاعد!" if fvg["in_bull"] else "داخل FVG هابط!" if fvg["in_bear"] else "")
    sw=("اصطياد صاعد" if liq["sweep_bull"] else "اصطياد هابط" if liq["sweep_bear"] else "—")
    now=datetime.now().strftime("%H:%M:%S")
    lines=[
        "╔══════════════════════════╗",
        "  {icon}  {hdr}  {icon}".format(icon=icon,hdr=hdr),
        "  {sig}".format(sig=dec["signal"]),
        "╚══════════════════════════╝",
        "",
        "Katia11 Pro | {src}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "الاصل: {name}",
        "السعر: {price}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "الجودة: {qi} {q} | النقاط: {sc:+.0f}",
        "النمط: {pat} -> {entry}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "الاتجاه العام:",
        "  {htf_lbl}",
        "  D1:{d1} H4:{h4} H1:{h1}",
        "  {ema_s}",
        "",
        "هيكل السوق (M15):",
        "  الاتجاه: {trend}",
        "  BOS:   {bos}",
        "  CHoCH: {choch}",
        "  قمة:{lph} | قاع:{lpl}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Order Blocks:",
        "  صاعد: {ob_b}",
        "  هابط: {ob_s}",
        "  {ob_in}",
        "",
        "Fair Value Gaps:",
        "  صاعد: {fvg_b}",
        "  هابط: {fvg_s}",
        "  {fvg_in}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "السيولة:",
        "  BSL: {bsl}",
        "  SSL: {ssl}",
        "  {sw}",
        "",
        "Premium/Discount:",
        "  {pd_zone} ({pd_pct:.1f}%)",
        "  EQ: {eq}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "BookMap: {bk_sig} ({bk_r:.0f}%)",
        "  حيتان ش:{wb} ب:{wa}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "الاشارات النشطة:",
        "{events}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "ادارة الصفقة:",
        "  TP1: {tp1} ({rr1})",
        "  TP2: {tp2}",
        "  TP3: {tp3} ({wp}) ({rr3})",
        "  SL:  {sl} ({rp})",
        "  ATR: {atr}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "@Katia11_Pro | {now}",
    ]
    return "\n".join(lines).format(
        icon=icon,hdr=hdr,sig=dec["signal"],src=src,
        name=r["name"],price=f(r["price"]),
        qi=qi,q=q,sc=sc,
        pat=conf["pat_s"],entry=conf["entry_type"],
        htf_lbl=r["htf"]["label"],d1=r["htf"]["d1"],h4=r["htf"]["h4"],h1=r["htf"]["h1"],
        ema_s=ema_s,trend=r["ms"]["trend"],
        bos=r["ms"]["bos"] or "—",choch=r["ms"]["choch"] or "—",
        lph=f(r["ms"]["last_ph"]),lpl=f(r["ms"]["last_pl"]),
        ob_b=ob_bs,ob_s=ob_ss,ob_in=ob_in,
        fvg_b=fvg_bs,fvg_s=fvg_ss,fvg_in=fvg_in,
        bsl=bsl_s,ssl=ssl_s,sw=sw,
        pd_zone=r["pd"]["zone"],pd_pct=r["pd"]["pct"],eq=f(r["pd"]["eq"]),
        bk_sig=bk["signal"],bk_r=bk["ratio"],wb=bk["whale_b"],wa=bk["whale_a"],
        events=events,
        tp1=tp1,tp2=tp2,tp3=tp3,sl=sl_,rr1=rr1,rr3=rr3,rp=rp,wp=wp,
        atr=F(r["atr"],d+2),now=now,
    )

def build_alert_msg(r,d,choice):
    f=lambda v:F(v,d); dec=r["dec"]; tpsl=r["tpsl"]
    dr="شراء" if dec["score"]>0 else "بيع"
    qi={"A+":"🏆🏆","A":"🏆"}.get(dec["quality"],"⭐")
    now=datetime.now().strftime("%H:%M")
    return (
        "🔔 اشعار تلقائي - اعداد مكتمل!\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "{dr} {name} {qi}[{q}] | نقاط: {sc:+.0f}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "السعر: {price}\n"
        "النمط: {pat}\n"
        "HTF: {htf}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "TP1: {tp1}  TP3: {tp3}\n"
        "SL:  {sl}\n"
        "RR:  {rr}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "للتحليل الكامل ارسل: {ch}\n"
        "{now}"
    ).format(
        dr=dr,name=r["name"],qi=qi,q=dec["quality"],sc=dec["score"],
        price=f(r["price"]),pat=r["conf"]["pat_s"],htf=r["htf"]["label"],
        tp1=f(tpsl["tp1"]) if tpsl else "—",
        tp3=f(tpsl["tp3"]) if tpsl else "—",
        sl=f(tpsl["sl"]) if tpsl else "—",
        rr="1:{}".format(tpsl["rr3"]) if tpsl else "—",
        ch=choice,now=now,
    )

def build_welcome(name):
    return (
        "مرحباً {name}!\n\n"
        "Katia11 Pro - SMC Ultra\n\n"
        "اختر الاصل:\n"
        "1 - EUR/USD\n2 - GBP/USD\n3 - USD/JPY\n"
        "4 - XAU/USD\n5 - BTC/USDT\n6 - ETH/USDT\n\n"
        "الاوامر:\n"
        "/stats - احصائيات الاشارات\n"
        "/active - الاشارات النشطة\n"
        "━━━━━━━━━━━━━━━\n"
        "D1+H4+H1+M15 | OB | FVG | BOS\n"
        "Liquidity Sweep | EMA 21/55/200\n"
        "TP1 + TP2 + TP3 + SL ديناميكي\n"
        "تتبع تلقائي + اشعار عند الوصول"
    ).format(name=name)

# ══════════════════════════════════════════════
# ادارة المستخدمين والاحصائيات
# ══════════════════════════════════════════════
_users={}; _ulock=threading.Lock()
_cds={}; _clock=threading.Lock()
_stats={}; _slock=threading.Lock()
_tracks={}; _tlock=threading.Lock()
_alert_sent={}

def _load_json(path,default):
    try:
        if os.path.exists(path):
            with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except: pass
    return default

def _save_json(path,data):
    try:
        tmp=path+".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
    except Exception as e: log.error("save %s: %s",path,e)

def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M")

def load_all():
    global _users,_stats,_tracks
    _users=_load_json(USERS_FILE,{})
    _stats=_load_json(STATS_FILE,{})
    _tracks=_load_json(TRACK_FILE,{})
    if ADMIN_ID not in _users:
        _users[ADMIN_ID]={"name":"المالك","joined":_now()}
    now=time.time()
    _tracks={k:v for k,v in _tracks.items() if v.get("expires_at",0)>now and v.get("status")=="active"}
    log.info("Loaded: users=%d stats=%d tracks=%d",len(_users),len(_stats),len(_tracks))

def is_allowed(uid,name):
    uid=str(uid)
    with _ulock:
        if uid in _users: return True
        if len(_users)<MAX_USERS:
            _users[uid]={"name":name,"joined":_now()}
            _save_json(USERS_FILE,_users); return True
    return False

def all_uids():
    with _ulock: return list(_users.keys())

def cd_remaining(uid,choice):
    key="{}:{}".format(uid,choice)
    with _clock:
        e=time.monotonic()-_cds.get(key,0)
        r=COOLDOWN_SEC-e
        return r if r>0 else None

def set_cd(uid,choice):
    with _clock: _cds["{}:{}".format(uid,choice)]=time.monotonic()

def record_signal(sym,name,direction,quality):
    with _slock:
        if sym not in _stats:
            _stats[sym]={"name":name,"signals":0,"bull":0,"bear":0,"tp3":0,"sl":0,"last":""}
        s=_stats[sym]; s["signals"]+=1
        if direction=="bull": s["bull"]+=1
        elif direction=="bear": s["bear"]+=1
        s["last"]=_now(); _save_json(STATS_FILE,_stats)

def record_result(sym,name,result,pips):
    with _slock:
        if sym not in _stats:
            _stats[sym]={"name":name,"signals":0,"bull":0,"bear":0,"tp3":0,"sl":0,"last":""}
        s=_stats[sym]
        if result=="tp3": s["tp3"]+=1
        elif result=="sl": s["sl"]+=1
        _save_json(STATS_FILE,_stats)

def build_stats():
    with _slock:
        if not _stats: return "لا توجد احصائيات بعد."
        lines=["احصائيات الاداء:\n"]
        for sym,s in _stats.items():
            closed=s.get("tp3",0)+s.get("sl",0)
            wr="{:.0f}%".format(s["tp3"]/closed*100) if closed>0 else "—"
            lines.append("{}: {} اشارة | TP3:{} SL:{} | Win:{}".format(
                s.get("name",sym),s["signals"],s.get("tp3",0),s.get("sl",0),wr))
        return "\n".join(lines)

# ══════════════════════════════════════════════
# نظام تتبع الاشارات
# ══════════════════════════════════════════════
VALIDITY={"A+":90*60,"A":60*60,"B":30*60}

def _fmt(v,d):
    if v is None: return "—"
    return "{:.{}f}".format(v,d)

def add_track(info,dec,tpsl,chat_ids):
    q=dec["quality"]
    if q not in VALIDITY or not tpsl: return
    tid="{}_{}".format(info["symbol"].replace("/",""),int(time.time()))
    with _tlock:
        _tracks[tid]={
            "sym":info["symbol"],"name":info["name"],"source":info["source"],
            "alt":info.get("alt"),"pip":info["pip"],"dec":info["dec"],
            "direction":dec["direction"],"quality":q,
            "tp1":tpsl["tp1"],"tp2":tpsl["tp2"],"tp3":tpsl["tp3"],"sl":tpsl["sl"],
            "tp_hit":0,"chat_ids":list(chat_ids),"status":"active",
            "expires_at":time.time()+VALIDITY[q],
            "created":datetime.now().strftime("%H:%M"),
        }
        _save_json(TRACK_FILE,_tracks)

def _check_tracks():
    with _tlock:
        active={k:v for k,v in _tracks.items() if v.get("status")=="active"}
    if not active: return
    finished=[]
    for tid,t in active.items():
        try:
            now=time.time(); pip=t["pip"]; d=t["dec"]
            info={"source":t["source"],"symbol":t["sym"],"alt":t.get("alt")}
            price=fetch_live_price(info)
            dr=t["direction"]; result=None
            if now>=t["expires_at"] and price is not None:
                result="expired"
            elif price is not None:
                if dr=="bull":
                    if t["tp_hit"]==0 and price>=t["tp1"]: t["tp_hit"]=1; result="tp1"
                    elif t["tp_hit"]==1 and price>=t["tp2"]: t["tp_hit"]=2; result="tp2"
                    elif price>=t["tp3"]: result="tp3"
                    elif price<=t["sl"]: result="sl"
                else:
                    if t["tp_hit"]==0 and price<=t["tp1"]: t["tp_hit"]=1; result="tp1"
                    elif t["tp_hit"]==1 and price<=t["tp2"]: t["tp_hit"]=2; result="tp2"
                    elif price<=t["tp3"]: result="tp3"
                    elif price>=t["sl"]: result="sl"
            if result:
                pips=round(abs((price or 0)-t["tp1"])/pip)
                dr_s="شراء" if dr=="bull" else "بيع"
                if result.startswith("tp"):
                    n=result[2:]
                    msg="TP{} وصل! +{} pips\n{} {}\nالسعر: {}\n{}\n{}".format(
                        n,pips,dr_s,t["name"],_fmt(price,d),
                        "TP الكامل!" if n=="3" else "جزئي - انتظر TP التالي",
                        datetime.now().strftime("%H:%M"))
                elif result=="sl":
                    msg="SL ضُرب - -{} pips\n{} {}\nSL:{} | السعر:{}\n{}".format(
                        pips,dr_s,t["name"],_fmt(t["sl"],d),_fmt(price,d),
                        datetime.now().strftime("%H:%M"))
                else:
                    msg="انتهت صلاحية الاشارة\n{} {}\nالسعر تحرّك {} pips.".format(dr_s,t["name"],pips)
                for cid in t["chat_ids"]: tg_send(cid,msg)
                record_result(t["sym"],t["name"],result,pips)
                closed=result in ("tp3","sl","expired")
                with _tlock: t["status"]=result if closed else "active"
                if closed: finished.append(tid)
            time.sleep(0.5)
        except Exception as e:
            log.warning("check_track %s: %s",tid,e)
    if finished:
        with _tlock:
            for tid in finished: _tracks.pop(tid,None)
            _save_json(TRACK_FILE,_tracks)

def active_summary():
    with _tlock:
        act=[v for v in _tracks.values() if v.get("status")=="active"]
    if not act: return "لا اشارات نشطة حالياً."
    lines=["الاشارات النشطة:\n"]
    for t in act:
        dr="شراء" if t["direction"]=="bull" else "بيع"
        exp=datetime.fromtimestamp(t["expires_at"]).strftime("%H:%M")
        lines.append("{} {} [{}] TP3:{} SL:{} ينتهي:{}".format(
            dr,t["name"],t["quality"],
            _fmt(t["tp3"],t["dec"]),_fmt(t["sl"],t["dec"]),exp))
    return "\n".join(lines)

# ══════════════════════════════════════════════
# Telegram Bot
# ══════════════════════════════════════════════
TG_BASE="https://api.telegram.org/bot{}".format(TELEGRAM_TOKEN)
_offset=0; _busy=set(); _block=threading.Lock()

def tg_send(chat_id,text):
    safe_get(TG_BASE+"/sendMessage",{
        "chat_id":chat_id,"text":text[:4000],
        "parse_mode":"HTML","disable_web_page_preview":True})

def get_updates():
    global _offset
    data=safe_get(TG_BASE+"/getUpdates",{"offset":_offset,"timeout":30,"limit":50})
    if not data or not data.get("ok"): return []
    return data.get("result",[])

def handle_update(upd):
    msg=upd.get("message") or upd.get("edited_message")
    if not msg: return
    uid=str(msg["from"]["id"])
    name=msg["from"].get("first_name","مستخدم")
    text=(msg.get("text") or "").strip()
    if not is_allowed(uid,name):
        tg_send(uid,"الحد الاقصى للمستخدمين بلغ."); return
    if text in ("/start","/help"): tg_send(uid,build_welcome(name)); return
    if text=="/stats": tg_send(uid,build_stats()); return
    if text=="/active": tg_send(uid,active_summary()); return
    if text=="/users" and uid==ADMIN_ID:
        with _ulock: tg_send(uid,"المستخدمون: {}".format(len(_users))); return
    if text in SYMBOLS:
        rem=cd_remaining(uid,text)
        if rem: tg_send(uid,"انتظر {} ثانية.".format(int(rem))); return
        bk="{}:{}".format(uid,text)
        with _block:
            if bk in _busy: return
            _busy.add(bk)
        def _do():
            try:
                tg_send(uid,"جاري التحليل... انتظر 10-20 ثانية.")
                info=SYMBOLS[text]; r=run_analysis(info)
                if not r: tg_send(uid,"تعذّر جلب البيانات. حاول مرة اخرى."); return
                tg_send(uid,build_msg(r,info["dec"])); set_cd(uid,text)
                dec=r["dec"]
                record_signal(info["symbol"],info["name"],dec["direction"],dec["quality"])
                if r["tpsl"] and dec["quality"] in ("A+","A","B"):
                    add_track(info,dec,r["tpsl"],[uid])
            finally:
                with _block: _busy.discard(bk)
        threading.Thread(target=_do,daemon=True).start(); return
    tg_send(uid,"ارسل رقم الاصل (1-6) او /help للمساعدة.")

# ══════════════════════════════════════════════
# حلقات الخلفية
# ══════════════════════════════════════════════
def alert_loop():
    log.info("Alert loop started")
    time.sleep(60)
    while True:
        for choice,info in SYMBOLS.items():
            try:
                sym=info["symbol"]
                last=_alert_sent.get(sym,0)
                if time.monotonic()-last<ALERT_COOLDOWN: continue
                r=run_analysis(info)
                if not r: continue
                dec=r["dec"]
                if abs(dec["score"])<SCORE_A or dec["quality"] not in ("A","A+"): continue
                _alert_sent[sym]=time.monotonic()
                msg=build_alert_msg(r,info["dec"],choice)
                log.info("Alert: %s q=%s sc=%+.0f",info["name"],dec["quality"],dec["score"])
                for uid in all_uids():
                    tg_send(uid,msg); time.sleep(0.05)
                if r["tpsl"]: add_track(info,dec,r["tpsl"],all_uids())
            except Exception as e: log.exception("alert %s: %s",info.get("name","?"),e)
            time.sleep(3)
        time.sleep(ALERT_INTERVAL)

def track_loop():
    log.info("Track loop started")
    time.sleep(30)
    while True:
        try: _check_tracks()
        except Exception as e: log.exception("track loop: %s",e)
        time.sleep(TRACK_INTERVAL)

def heartbeat_loop():
    while True:
        time.sleep(5*60)
        log.info("Heartbeat - tracks=%d users=%d",
                 len([v for v in _tracks.values() if v.get("status")=="active"]),
                 len(_users))

# ══════════════════════════════════════════════
# Termux Wake Lock
# ══════════════════════════════════════════════
def termux_wake_lock():
    try:
        import subprocess
        r=subprocess.run(["termux-wake-lock"],timeout=5,capture_output=True)
        if r.returncode==0: log.info("termux-wake-lock: مفعّل - البوت لن يوقف عند اطفاء الشاشة")
        else: log.warning("termux-wake-lock: فشل - قد يوقف البوت عند اطفاء الشاشة")
    except FileNotFoundError:
        log.info("termux-wake-lock غير متوفر (ثبّت termux-api)")
    except Exception as e:
        log.warning("termux-wake-lock: %s",e)

# ══════════════════════════════════════════════
# التشغيل الرئيسي
# ══════════════════════════════════════════════
def main():
    global _offset
    print("="*50)
    print("  Katia11 Pro - Termux Edition")
    print("="*50)
    missing=[]
    if "ضع_" in TELEGRAM_TOKEN: missing.append("TELEGRAM_TOKEN")
    if "ضع_" in ADMIN_ID:       missing.append("ADMIN_ID")
    if "ضع_" in TWELVEDATA_KEY: missing.append("TWELVEDATA_KEY")
    if missing:
        print("\nخطأ: اضف هذه المفاتيح في اعلى الملف:")
        for m in missing: print("  ->",m)
        print("\nافتح الملف بـ: nano katia11.py")
        sys.exit(1)
    load_all()
    termux_wake_lock()
    threading.Thread(target=alert_loop,daemon=True).start()
    threading.Thread(target=track_loop,daemon=True).start()
    threading.Thread(target=heartbeat_loop,daemon=True).start()
    log.info("البوت جاهز - بدء الاستقبال...")
    try:
        tg_send(ADMIN_ID,
            "Katia11 Pro - Termux Edition\n"
            "البوت يعمل الان 24/7\n"
            "ارسل /start للبدء.\n"
            + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except: pass
    consecutive_errors=0
    while True:
        try:
            updates=get_updates()
            consecutive_errors=0
            for upd in updates:
                _offset=upd["update_id"]+1
                threading.Thread(target=handle_update,args=(upd,),daemon=True).start()
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            try:
                import subprocess
                subprocess.run(["termux-wake-unlock"],timeout=3)
            except: pass
            break
        except Exception as e:
            consecutive_errors+=1
            log.exception("polling #%d: %s",consecutive_errors,e)
            wait=min(5*consecutive_errors,60)
            log.info("اعادة المحاولة خلال %d ثانية...",wait)
            time.sleep(wait)

if __name__=="__main__":
    main()
