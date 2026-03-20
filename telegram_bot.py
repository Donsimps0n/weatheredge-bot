"""telegram_bot.py - WeatherEdge Telegram Bot with Claude AI. 24/7 on Railway."""
import json,os,logging,threading,requests,time
from datetime import datetime,timezone
from pathlib import Path
log=logging.getLogger(__name__)
TELEGRAM_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY","")
API_BASE=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
RAILWAY_API="https://weatheredge-production.up.railway.app"
SUBSCRIBERS_FILE=Path("data/telegram_subscribers.json")
_subscribers=set()
SYSTEM_PROMPT="""You are WeatherEdge AI â expert in Polymarket weather trading. You help manage the bot, analyse markets, find edges. Keep replies short and punchy for Telegram. The user built this with Claude. Stack: Railway, Vercel, Polymarket, GFS+ECMWF+UKMO+MeteoFrance. Bot is in PAPER mode. Use emojis naturally."""

def load_subs():
    global _subscribers
    if SUBSCRIBERS_FILE.exists():
        try: _subscribers=set(json.loads(SUBSCRIBERS_FILE.read_text()))
        except: pass

def save_subs():
    SUBSCRIBERS_FILE.parent.mkdir(exist_ok=True)
    SUBSCRIBERS_FILE.write_text(json.dumps(list(_subscribers)))

def send(chat_id,text):
    try: requests.post(f"{API_BASE}/sendMessage",json={"chat_id":chat_id,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},timeout=10)
    except Exception as e: log.warning("Send failed: %s",e)

def broadcast(text):
    for cid in list(_subscribers): send(cid,text)

def ask_claude(msg,ctx=""):
    if not ANTHROPIC_KEY: return "No API key set."
    try:
        messages=[]
        if ctx:
            messages.append({"role":"user","content":"Live WeatherEdge data:\n"+ctx})
            messages.append({"role":"assistant","content":"Got it."})
        messages.append({"role":"user","content":msg})
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":"claude-sonnet-4-20250514","max_tokens":1024,"system":SYSTEM_PROMPT,"messages":messages},timeout=30)
        d=r.json()
        if "content" in d and d["content"]:
            return d["content"][0]["text"]
        elif "error" in d:
            err=d["error"]
            if err.get("type")=="authentication_error":
                return "API key issue - check Railway env vars"
            return f"API error: {err.get('message','unknown')}"
        else:
            return f"Unexpected response: {str(d)[:200]}"
    except Exception as e: return f"Claude error: {e}"

def live_ctx():
    try:
        stats=requests.get(RAILWAY_API+"/api/stats",timeout=8).json()
        scan=requests.get(RAILWAY_API+"/api/scan",timeout=20).json()
        top=sorted(scan.get("results",[]),key=lambda x:x.get("volume",0),reverse=True)[:8]
        return json.dumps({"stats":stats,"top_markets":top,"total":scan.get("markets",0)})
    except: return ""

def cmd_start(cid,name):
    _subscribers.add(cid); save_subs()
    send(cid,f"Hi {name}! WeatherEdge is live 24/7 on Railway - your Mac can be off. Just talk to me naturally or use /scan /edge /positions /stats /forecast. I am Claude!")

def cmd_scan(cid):
    send(cid,"Scanning via GFS ECMWF UKMO MeteoFrance...")
    try:
        d=requests.get(RAILWAY_API+"/api/scan",timeout=25).json()
        top=sorted(d.get("results",[]),key=lambda x:x.get("volume",0),reverse=True)[:5]
        lines=[f"Found {d.get(chr(109)+chr(97)+chr(114)+chr(107)+chr(101)+chr(116)+chr(115),0)} markets\n"]
        for m in top: lines.append(f"{m[chr(99)+chr(105)+chr(116)+chr(121)]}: YES={m[chr(121)+chr(101)+chr(115)+chr(95)+chr(112)+chr(114)+chr(105)+chr(99)+chr(101)]*100:.1f}%")
        send(cid,"\n".join(lines))
    except Exception as e: send(cid,f"Error: {e}")

def cmd_edge(cid):
    send(cid,"Finding edges...")
    try:
        d=requests.get(RAILWAY_API+"/api/scan",timeout=25).json()
        edges=[m for m in d.get("results",[]) if m["yes_price"]<0.07 or m["yes_price"]>0.93]
        if not edges: send(cid,"No strong edges right now."); return
        lines=[f"{len(edges)} edges found\n"]
        for m in edges[:5]:
            p=m["yes_price"]*100; side="NO" if p<10 else "YES"
            lines.append(f"{side} {m[chr(99)+chr(105)+chr(116)+chr(121)]} @{p:.1f}% ${m[chr(118)+chr(111)+chr(108)+chr(117)+chr(109)+chr(101)]:.0f}")
        send(cid,"\n".join(lines))
    except Exception as e: send(cid,f"Error: {e}")

def cmd_stats(cid):
    try:
        d=requests.get(RAILWAY_API+"/api/stats",timeout=10).json()
        send(cid,f"Mode: {d.get(chr(98)+chr(111)+chr(116)+chr(95)+chr(109)+chr(111)+chr(100)+chr(101),chr(63))}\nMarkets: {d.get(chr(109)+chr(97)+chr(114)+chr(107)+chr(101)+chr(116)+chr(115)+chr(95)+chr(115)+chr(99)+chr(97)+chr(110)+chr(110)+chr(101)+chr(100),0)}\nDashboard: https://iamweather.vercel.app")
    except Exception as e: send(cid,f"Error: {e}")

def cmd_forecast(cid,city):
    if not city: send(cid,"Usage: /forecast London"); return
    send(cid,f"Fetching forecast for {city}...")
    try:
        d=requests.get(RAILWAY_API+f"/api/forecast/{city}",timeout=20).json()
        if "error" in d: send(cid,d["error"]); return
        send(cid,f"{city}: {d.get(chr(109)+chr(101)+chr(97)+chr(110)+chr(95)+chr(99),chr(63))}C +/-{d.get(chr(115)+chr(116)+chr(100)+chr(95)+chr(99),chr(63))}C | UKMO:{d.get(chr(117)+chr(107)+chr(109)+chr(111),chr(63))} MF:{d.get(chr(109)+chr(101)+chr(116)+chr(101)+chr(111)+chr(102)+chr(114)+chr(97)+chr(110)+chr(99)+chr(101),chr(63))} spread:{d.get(chr(109)+chr(111)+chr(100)+chr(101)+chr(108)+chr(95)+chr(115)+chr(112)+chr(114)+chr(101)+chr(97)+chr(100),chr(63))}C")
    except Exception as e: send(cid,f"Error: {e}")

def handle_update(update):
    msg=update.get("message") or update.get("edited_message")
    if not msg: return
    cid=msg["chat"]["id"]; name=msg["chat"].get("first_name","trader")
    text=msg.get("text","").strip()
    if not text: return
    if text.startswith("/start"): cmd_start(cid,name)
    elif text.startswith("/scan"): cmd_scan(cid)
    elif text.startswith("/edge"): cmd_edge(cid)
    elif text.startswith("/stats"): cmd_stats(cid)
    elif text.startswith("/positions"):
        try:
            pos=requests.get(RAILWAY_API+"/api/positions",timeout=10).json()
            send(cid,f"{len(pos)} open positions" if pos else "No open positions. Bot in PAPER mode.")
        except Exception as e: send(cid,f"Error: {e}")
    elif text.startswith("/forecast"):
        parts=text.split(maxsplit=1)
        cmd_forecast(cid,parts[1] if len(parts)>1 else "")
    elif text.startswith("/stop"):
        _subscribers.discard(cid); save_subs(); send(cid,"Paused. /start to resume.")
    else:
        send(cid,"Thinking...")
        needs_ctx=any(w in text.lower() for w in ["market","edge","trade","forecast","weather","city","price","paris","london","tokyo","temp"])
        ctx=live_ctx() if needs_ctx else ""
        send(cid,ask_claude(text,ctx))

def run_polling():
    load_subs(); offset=0
    log.info("Telegram bot polling started")
    while True:
        try:
            r=requests.get(f"{API_BASE}/getUpdates",params={"offset":offset,"timeout":30,"allowed_updates":["message"]},timeout=35)
            for u in r.json().get("result",[]):
                offset=u["update_id"]+1
                try: handle_update(u)
                except Exception as e: log.warning("Update error: %s",e)
        except Exception as e:
            log.warning("Polling error: %s",e); time.sleep(5)

def start_telegram_bot():
    if not TELEGRAM_TOKEN: log.warning("No token"); return
    t=threading.Thread(target=run_polling,daemon=True,name="telegram-bot")
    t.start(); log.info("Telegram bot started"); return t

def alert_edge_found(city,question,yes_price,edge,side,spread):
    if not _subscribers: return
    broadcast(f"EDGE {city} {side}@{yes_price*100:.1f}% edge+{edge*100:.1f}%\n{question[:60]}\nhttps://iamweather.vercel.app")

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    run_polling()