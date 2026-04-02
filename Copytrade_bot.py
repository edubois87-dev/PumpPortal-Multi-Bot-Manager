"""
PumpPortal Multi-Bot Manager v11
Single shared WebSocket — all bots subscribe on one connection.
Dashboard: http://localhost:8888
"""
import json,time,threading,requests,websocket,os
from datetime import datetime
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse

API_KEY=""
WALLET_PRIVATE_KEY=""
MASTER_MODE="simulate"
MASTER_ON=False
MASTER_MAX_CONN=20
PORT=8888
SIM_BAL=1.0
LOG=[]

# Shared WebSocket
shared_ws=None
shared_ws_connected=False
shared_ws_lock=threading.Lock()

def alog(m,bid=None):
    ts=datetime.now().strftime("%H:%M:%S")
    p=f"[Bot {bid}]" if bid else "[SYS]"
    e={"t":datetime.now().isoformat(),"ts":ts,"m":f"{p} {m}","bid":bid}
    LOG.append(e)
    if len(LOG)>500:LOG.pop(0)
    print(f"\033[96m[{ts}] {p} {m}\033[0m")

class Bot:
    def __init__(self,bid):
        self.id=bid;self.on=False;self.mode="simulate";self.wallet=""
        self.conc=1;self.ba=0.05;self.sp="100%";self.slip=5;self.pf=0.0005
        self.pool="auto";self.cd=0;self.sl=20;self.asec=180;self.mh=30
        self.mcl=5;self.mb=0.05;self.mdl=0.3;self.mop=3
        self.bought={};self.burned=set();self.hist=[]
        self.st={"w":0,"l":0,"pnl":0.0,"cl":0,"dl":0.0}
        self.subscribed=False

    @property
    def eff(self):return "simulate" if MASTER_MODE=="simulate" else self.mode

    def state(self):
        pos=[]
        for m,i in self.bought.items():
            try:h=(datetime.now()-datetime.fromisoformat(i["bt"])).total_seconds()
            except:h=0
            pos.append({"mint":m,"amt":i["amt"],"bt":i["bt"],"held":round(h)})
        w,l=self.st["w"],self.st["l"];t=w+l
        return dict(id=self.id,on=self.on,mode=self.mode,eff=self.eff,wallet=self.wallet,
            wsc=self.subscribed and shared_ws_connected,conc=self.conc,
            ba=self.ba,sp=self.sp,slip=self.slip,pf=self.pf,
            pool=self.pool,cd=self.cd,sl=self.sl,asec=self.asec,mh=self.mh,mcl=self.mcl,
            mb=self.mb,mdl=self.mdl,mop=self.mop,w=w,l=l,
            wr=round((w/t*100)if t>0 else 0,1),pnl=round(self.st["pnl"],6),
            dl=round(self.st["dl"],6),pos=pos,hist=self.hist[-30:],bl=len(self.burned))

    def trade(self,act,mint,amt):
        global SIM_BAL
        if not API_KEY:return None
        url=f"https://pumpportal.fun/api/trade?api-key={API_KEY}"
        pl={"action":act,"mint":mint,"amount":amt,"denominatedInSol":"true"if act=="buy"else"false",
            "slippage":self.slip,"priorityFee":self.pf,"pool":self.pool}
        if self.eff=="simulate":
            alog(f"[SIM] {act} {amt} SOL → {mint[:20]}...",self.id)
            if act=="buy":SIM_BAL-=float(amt)
            return {"status":"sim","signature":"sim-"+mint[:8]}
        try:
            alog(f"[LIVE] {act} {amt} SOL → {mint[:20]}...",self.id)
            r=requests.post(url,json=pl,timeout=30);return r.json()
        except Exception as e:alog(f"ERROR:{e}",self.id);return None

    def do_sell(self,mint,reason="sell"):
        global SIM_BAL
        info=self.bought.pop(mint,None)
        if not info:return
        tm=info.get("tmr");
        if tm:tm.cancel()
        alog(f"SELL({reason}) {mint[:20]}...",self.id)
        r=self.trade("sell",mint,"100%");tx=r.get("signature","")if r else""
        loss=info.get("amt",self.ba)*0.15
        self.st["l"]+=1;self.st["cl"]+=1;self.st["pnl"]-=loss;self.st["dl"]+=loss
        self.burned.add(mint)
        self.hist.append({"mint":mint,"act":"sell","pp":-15,"ps":round(-loss,6),"tx":tx,"t":datetime.now().isoformat()})
        if len(self.hist)>60:self.hist.pop(0)
        if self.eff=="simulate":SIM_BAL+=info.get("amt",self.ba)*0.85

    def sell_all(self):
        for m in list(self.bought):self.do_sell(m,"sell all")

    def auto_sell_timer(self,mint):
        if self.asec<=0:return
        def fn():
            if mint in self.bought:self.do_sell(mint,f"auto {self.asec}s")
        t=threading.Timer(self.asec,fn);t.daemon=True;t.start()
        if mint in self.bought:self.bought[mint]["tmr"]=t

    def handle_trade(self,data):
        global SIM_BAL
        if not self.on or not MASTER_ON:return
        tt=data.get("txType","");mint=data.get("mint","");trader=data.get("traderPublicKey","")
        try:sa=float(data.get("solAmount",0))
        except:sa=0
        if trader!=self.wallet or sa<=0:return
        if tt=="buy":
            if mint in self.burned or mint in self.bought:return
            if len(self.bought)>=self.mop or sa<self.mb:return
            if self.st["cl"]>=self.mcl or self.st["dl"]>=self.mdl:return
            if self.cd>0:time.sleep(self.cd)
            alog(f"COPY BUY {self.ba} SOL → {mint[:20]}...",self.id)
            r=self.trade("buy",mint,self.ba)
            if r:
                tx=r.get("signature","")
                self.bought[mint]={"bt":datetime.now().isoformat(),"amt":self.ba,"tsol":sa,"tmr":None}
                self.hist.append({"mint":mint,"act":"buy","pp":0,"ps":round(-self.ba,6),"tx":tx,"t":datetime.now().isoformat()})
                if len(self.hist)>60:self.hist.pop(0)
                self.auto_sell_timer(mint)
        elif tt=="sell":
            if mint not in self.bought:return
            info=self.bought[mint]
            if self.mh>0:
                try:
                    held=(datetime.now()-datetime.fromisoformat(info["bt"])).total_seconds()
                    if held<self.mh:
                        def dl(m=mint):
                            if m in self.bought:self.do_sell(m,"delayed")
                        threading.Timer(self.mh-held,dl).start();return
                except:pass
            info=self.bought.pop(mint);tm=info.get("tmr")
            if tm:tm.cancel()
            tb=info.get("tsol",0);pp=((sa-tb)/tb*100)if tb>0 else 0;ps=self.ba*(pp/100)
            if pp>=0:
                self.st["w"]+=1;self.st["cl"]=0;self.st["pnl"]+=ps
                if self.eff=="simulate":SIM_BAL+=self.ba+ps
            else:
                self.st["l"]+=1;self.st["cl"]+=1;self.st["pnl"]-=abs(ps);self.st["dl"]+=abs(ps)
                if abs(pp)>=self.sl:self.burned.add(mint)
                if self.eff=="simulate":SIM_BAL+=self.ba-abs(ps)
            alog(f"SELL {mint[:20]}... {pp:+.1f}%",self.id)
            r=self.trade("sell",mint,"100%");tx=r.get("signature","")if r else""
            self.hist.append({"mint":mint,"act":"sell","pp":round(pp,2),"ps":round(ps,6),"tx":tx,"t":datetime.now().isoformat()})
            if len(self.hist)>60:self.hist.pop(0)

    def subscribe(self):
        if not self.wallet or self.subscribed:return
        with shared_ws_lock:
            ws=shared_ws
        if ws and shared_ws_connected:
            try:
                ws.send(json.dumps({"method":"subscribeAccountTrade","keys":[self.wallet]}))
                self.subscribed=True
                alog(f"SUBSCRIBED → {self.wallet[:20]}...",self.id)
            except Exception as e:
                alog(f"Subscribe error: {e}",self.id)

    def unsubscribe(self):
        if not self.wallet or not self.subscribed:return
        with shared_ws_lock:
            ws=shared_ws
        if ws and shared_ws_connected:
            try:
                ws.send(json.dumps({"method":"unsubscribeAccountTrade","keys":[self.wallet]}))
            except:pass
        self.subscribed=False

    def enable(self):
        self.on=True
        alog(f"Enabling → {self.wallet[:20] if self.wallet else 'no wallet'}...",self.id)
        self.subscribe()

    def disable(self):
        alog("Disabling...",self.id)
        self.unsubscribe()
        self.sell_all()
        self.on=False

    def sw(self,w):
        old=self.wallet
        if old and self.subscribed:self.unsubscribe()
        self.wallet=w
        alog(f"Wallet → {w[:20]}...",self.id)
        if self.on:self.subscribe()

    def upd(self,d):
        m={"ba":"ba","sp":"sp","slip":"slip","pf":"pf","pool":"pool","cd":"cd",
           "sl":"sl","asec":"asec","mh":"mh","mcl":"mcl","mb":"mb","mdl":"mdl","mop":"mop",
           "conc":"conc","mode":"mode"}
        for k,a in m.items():
            if k in d:
                v=d[k]
                if k in("ba","pf","mb","mdl"):v=float(v)
                elif k in("slip","cd","sl","asec","mh","mcl","mop","conc"):v=int(v)
                setattr(self,a,v)
        if"wallet"in d:
            w=str(d["wallet"]).strip()
            if w!=self.wallet and len(w)>=32:self.sw(w)

bots={i:Bot(i)for i in range(1,21)}

# Map wallets to bots for fast lookup
def get_wallet_bots():
    """Returns dict of wallet -> [bot] for quick message routing."""
    wm={}
    for b in bots.values():
        if b.on and b.wallet:
            wm.setdefault(b.wallet,[]).append(b)
    return wm

# ---- Shared WebSocket ----
def ws_on_message(ws,msg):
    try:d=json.loads(msg)
    except:return
    if"mint"not in d:return
    trader=d.get("traderPublicKey","")
    if not trader:return
    # Route to matching bots
    for b in bots.values():
        if b.on and b.wallet==trader:
            try:b.handle_trade(d)
            except Exception as e:alog(f"Trade error: {e}",b.id)

def ws_on_open(ws):
    global shared_ws,shared_ws_connected
    with shared_ws_lock:
        shared_ws=ws
        shared_ws_connected=True
    alog("Shared WebSocket CONNECTED")
    # Subscribe all enabled bots
    for b in bots.values():
        if b.on and b.wallet:
            b.subscribed=False  # Reset so subscribe() sends the message
            b.subscribe()

def ws_on_close(ws,code,msg):
    global shared_ws_connected
    shared_ws_connected=False
    for b in bots.values():b.subscribed=False
    alog(f"Shared WebSocket closed (code:{code})")

def ws_on_error(ws,err):
    if err:alog(f"WS error: {err}")

def run_shared_ws():
    """Single WebSocket connection, auto-reconnects."""
    global shared_ws,shared_ws_connected
    while True:
        try:
            alog("Connecting shared WebSocket...")
            ws=websocket.WebSocketApp("wss://pumpportal.fun/api/data",
                on_open=ws_on_open,on_message=ws_on_message,
                on_close=ws_on_close,on_error=ws_on_error)
            ws.run_forever(ping_interval=20,ping_timeout=10,reconnect=0)
            shared_ws_connected=False
            for b in bots.values():b.subscribed=False
            alog("WS disconnected, reconnecting in 5s...")
            time.sleep(5)
        except Exception as e:
            alog(f"WS fatal: {e}")
            time.sleep(5)

def total_conn():return sum(1 for b in bots.values()if b.on and b.subscribed)

# ---- HTTP ----
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def _c(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
    def do_OPTIONS(self):self.send_response(200);self._c();self.end_headers()
    def _j(self,d,c=200):
        self.send_response(c);self.send_header("Content-Type","application/json");self._c();self.end_headers()
        self.wfile.write(json.dumps(d).encode())
    def do_GET(self):
        p=urlparse(self.path).path
        if p=="/api/state":
            all_pos=[];all_hist=[];total_pnl=0
            for b in bots.values():
                total_pnl+=b.st["pnl"]
                for m,i in b.bought.items():
                    try:h=(datetime.now()-datetime.fromisoformat(i["bt"])).total_seconds()
                    except:h=0
                    all_pos.append({"mint":m,"amt":i["amt"],"bt":i["bt"],"held":round(h),"bid":b.id})
                for h in b.hist[-10:]:
                    hh=dict(h);hh["bid"]=b.id;all_hist.append(hh)
            all_hist.sort(key=lambda x:x.get("t",""),reverse=True)
            self._j({"ak":bool(API_KEY),"pk":bool(WALLET_PRIVATE_KEY),
                "mm":MASTER_MODE,"mo":MASTER_ON,"mc":MASTER_MAX_CONN,
                "tc":total_conn(),"wsc":shared_ws_connected,
                "sim":round(SIM_BAL,6),"tpnl":round(total_pnl,6),
                "bots":{str(i):b.state()for i,b in bots.items()},
                "all_pos":all_pos[:50],"all_hist":all_hist[:50],
                "log":LOG[-100:],"ts":datetime.now().isoformat()})
            return
        if p=="/api/export_log":
            lines=[];tw=tl=tpnl=0
            for b in bots.values():tw+=b.st["w"];tl+=b.st["l"];tpnl+=b.st["pnl"]
            lines.append("=== Simulation Summary ===")
            lines.append(f"Time: {datetime.now().isoformat()}")
            lines.append(f"Mode: {MASTER_MODE.upper()}")
            lines.append(f"Sim Balance: {SIM_BAL:.6f} SOL")
            lines.append(f"Total P&L: {tpnl:+.6f} SOL")
            lines.append(f"Wins: {tw} | Losses: {tl} | WR: {(tw/(tw+tl)*100)if(tw+tl)>0 else 0:.1f}%")
            lines.append("")
            for b in bots.values():
                if b.wallet:lines.append(f"Bot {b.id}: {b.wallet}");lines.append(f"  P&L:{b.st['pnl']:+.6f} W/L:{b.st['w']}/{b.st['l']} Mode:{b.eff}")
            lines.append(f"\n=== Log ({len(LOG)}) ===")
            for e in LOG:lines.append(f"[{e['ts']}] {e['m']}")
            self.send_response(200);self.send_header("Content-Type","text/plain")
            self.send_header("Content-Disposition","attachment;filename=sim_log.txt")
            self._c();self.end_headers();self.wfile.write("\n".join(lines).encode())
            return
        if p=="/":
            hp=os.path.join(os.path.dirname(os.path.abspath(__file__)),"dashboard.html")
            try:
                with open(hp,"r",encoding="utf-8")as f:html=f.read()
                self.send_response(200);self.send_header("Content-Type","text/html");self._c();self.end_headers()
                self.wfile.write(html.encode())
            except:self._j({"error":"dashboard.html not found"},404)
            return
        self._j({"error":"404"},404)
    def do_POST(self):
        global API_KEY,WALLET_PRIVATE_KEY,MASTER_MODE,MASTER_ON,MASTER_MAX_CONN,SIM_BAL
        p=urlparse(self.path).path;cl=int(self.headers.get("Content-Length",0))
        body=self.rfile.read(cl).decode()if cl>0 else"{}"
        try:d=json.loads(body)
        except:d={}
        if p=="/api/config":
            if"ak"in d:API_KEY=d["ak"].strip()
            if"pk"in d:WALLET_PRIVATE_KEY=d["pk"].strip()
            if"mm"in d:MASTER_MODE=d["mm"]
            if"mo"in d:MASTER_ON=bool(d["mo"])
            if"mc"in d:MASTER_MAX_CONN=max(1,min(90,int(d["mc"])))
            if"reset_sim"in d:SIM_BAL=1.0;alog("Sim balance reset to 1 SOL")
            self._j({"ok":1});return
        if p=="/api/bot/upd":
            bid=d.pop("bid",-1)
            if bid in bots:bots[bid].upd(d);self._j({"ok":1})
            else:self._j({"e":"bad"},400)
            return
        if p=="/api/bot/upd_all":
            s=d.get("s",{})
            for b in bots.values():b.upd(s)
            self._j({"ok":1});return
        if p=="/api/bot/on":
            bid=d.get("bid",-1)
            if bid in bots:
                if total_conn()<MASTER_MAX_CONN:bots[bid].enable();self._j({"ok":1})
                else:self._j({"e":"max conn"},400)
            else:self._j({"e":"bad"},400)
            return
        if p=="/api/bot/off":
            bid=d.get("bid",-1)
            if bid in bots:bots[bid].disable();self._j({"ok":1})
            else:self._j({"e":"bad"},400)
            return
        if p=="/api/bot/sell":
            bid=d.get("bid",-1);mint=d.get("mint","")
            if bid in bots and mint in bots[bid].bought:bots[bid].do_sell(mint,"manual");self._j({"ok":1})
            else:self._j({"e":"nf"},400)
            return
        if p=="/api/bot/sellall":
            bid=d.get("bid",-1)
            if bid in bots:bots[bid].sell_all();self._j({"ok":1})
            else:self._j({"e":"bad"},400)
            return
        if p=="/api/bot/buy":
            bid=d.get("bid",-1);mint=d.get("mint","").strip()
            if bid not in bots or len(mint)<20:self._j({"e":"bad"},400);return
            b=bots[bid];amt=float(d.get("amt",b.ba))
            r=b.trade("buy",mint,amt)
            if r and"errors"not in r:
                tx=r.get("signature","")
                b.bought[mint]={"bt":datetime.now().isoformat(),"amt":amt,"tsol":amt,"tmr":None}
                b.hist.append({"mint":mint,"act":"buy","pp":0,"ps":round(-amt,6),"tx":tx,"t":datetime.now().isoformat()})
                b.auto_sell_timer(mint);self._j({"ok":1,"tx":tx})
            else:self._j({"e":"fail"},400)
            return
        if p=="/api/mass":
            ws=d.get("ws",[]);ae=d.get("ae",True);n=0
            for i,w in enumerate(ws[:20]):
                bid=i+1;w=w.strip()
                if len(w)>=32 and bid in bots:
                    bots[bid].sw(w)
                    if ae:bots[bid].enable()
                    n+=1
            alog(f"Mass imported {n} wallets")
            self._j({"ok":1,"n":n});return
        self._j({"e":"404"},404)

def main():
    print("\n\033[95m  PumpPortal Multi-Bot Manager v11\033[0m")
    print(f"\033[92m  Dashboard: http://localhost:{PORT}\033[0m")
    print("\033[37m  Using single shared WebSocket\033[0m\n")

    # Start shared WebSocket in background
    ws_thread=threading.Thread(target=run_shared_ws,daemon=True)
    ws_thread.start()

    # Start HTTP server
    srv=HTTPServer(("127.0.0.1",PORT),H);srv.daemon_threads=True
    try:srv.serve_forever()
    except KeyboardInterrupt:
        for b in bots.values():
            if b.on:b.disable()

if __name__=="__main__":main()