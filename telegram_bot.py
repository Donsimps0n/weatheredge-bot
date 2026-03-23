"""telegram_bot.py - WeatherEdge Telegram Bot.
New: /pnl /positions /calibrate /backtest, rich alerts with edge%+size+confidence, daily 9am summary.
Alert threshold 15% min edge. Max 10 alerts/day.
"""
import json,os,logging,threading,requests,time
from datetime import datetime,timezone,date
from pathlib import Path
log=logging.getLogger(__name__)
TELEGRAM_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY","")
API_BASE=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
RAILWAY_API="https://weatheredge-production.up.railway.app"
DASHBOARD_URL="https://iamweather.vercel.app/dashboard.html"
SUBSCRIBERS_FILE=Path("data/telegram_subscribers.json")
_subscribers=set()
ALERT_MIN_EDGE=0.15
ALERT_MAX_DAY=10
_alert_count={"date":str(date.today()),"count":0}
SYSTEM_PROMPT="You are WeatherEdge AI - expert in Polymarket weather trading. Be concise and specific."

def load_subs():
    global _subscribers
    try:
        if SUBSCRIBERS_FILE.exists(): _subscribers=set(json.loads(SUBSCRIBERS_FILE.read_text()))
    except: _subscribers=set()

def save_subs():
    SUBSCRIBERS_FILE.parent.mkdir(exist_ok=True)
    SUBSCRIBERS_FILE.write_text(json.dumps(list(_subscribers)))

def send(chat_id,text,parse_mode="Markdown"):
    if not TELEGRAM_TOKEN: return
    try:
        requests.post(API_BASE+"/sendMessage",json={
            "chat_id":chat_id,"text":text[:4096],
            "parse_mode":parse_mode,"disable_web_page_preview":True
        },timeout=10)
    except Exception as e: log.warning("Send failed: %s",e)

def broadcast(text):
    for cid in list(_subscribers): send(cid,text)

def alert_edge_found(city,question,market_price,edge,side,
                     spread=1.0,model_prob=0.5,position_usd=0.0,
                     days_ahead=1,condition_id=""):
    global _alert_count
    today=str(date.today())
    if _alert_count["date"]!=today: _alert_count={"date":today,"count":0}
    if _alert_count["count"]>=ALERT_MAX_DAY or edge<ALERT_MIN_EDGE: return
    _alert_count["count"]+=1
    conf="HIGH" if spread<1.0 else ("MED" if spread<2.0 else "LOW")
    day_str="TODAY" if days_ahead==0 else ("TOMORROW" if days_ahead==1 else f"+{days_ahead}d")
    pm_link=f"https://polymarket.com/event/{condition_id}" if condition_id else "https://polymarket.com"
    broadcast(
        f"*EDGE FOUND* - {city.upper()}\n"
        f"Side: *{side}* @ {market_price*100:.1f}c\n"
        f"Edge: *+{edge*100:.1f}%* | {day_str}\n"
        f"Model: {model_prob*100:.1f}% prob | Size: *${position_usd:.2f}*\n"
        f"Confidence: {conf} (spread={spread:.1f}C)\n"
        f"_{question[:80]}_\n"
        f"[Open Market]({pm_link})"
    )

def alert_trade_resolved(city,side,pnl,outcome):
    emoji="WIN" if outcome=="WIN" else "LOSS"
    broadcast(f"{emoji} RESOLVED - {city.upper()} {side} P&L: ${pnl:+.2f}")

def send_daily_summary():
    try:
        ts=requests.get(RAILWAY_API+"/api/trader/stats",timeout=10).json()
        scan=requests.get(RAILWAY_API+"/api/scan",timeout=30).json()
        edges=scan.get("edges",[])[:5]
        lines="".join(f"- {e.get(chr(99)+chr(105)+chr(116)+chr(121),chr(63))} {e.get(chr(98)+chr(101)+chr(115)+chr(116)+chr(95)+chr(115)+chr(105)+chr(100)+chr(101),chr(63))} +{e.get(chr(98)+chr(101)+chr(115)+chr(116)+chr(95)+chr(101)+chr(100)+chr(103)+chr(101),0)*100:.0f}%\n" for e in edges)
        broadcast(
            f"*DAILY SUMMARY* - {date.today()}\n"
            f"Today P&L: ${ts.get(chr(100)+chr(97)+chr(105)+chr(108)+chr(121)+chr(95)+chr(112)+chr(110)+chr(108),0):+.2f}\n"
            f"WR: {ts.get(chr(119)+chr(105)+chr(110)+chr(95)+chr(114)+chr(97)+chr(116)+chr(101),0)*100:.1f}%\n"
            f"\nTop Edges:\n{lines}[Dashboard]({DASHBOARD_URL})"
        )
    except Exception as e: log.warning("Daily summary failed: %s",e)

def cmd_start(cid):
    _subscribers.add(cid); save_subs()
    send(cid,
        "*WeatherEdge Bot*\n"
        "/scan - Top edges now\n"
        "/stats - Bot stats\n"
        "/pnl - P&L + city leaderboard\n"
        "/positions - Open trades\n"
        "/forecast [city] - Temperature\n"
        "/calibrate - Refit model\n"
        "/backtest - 7-day test\n"
        f"[Dashboard]({DASHBOARD_URL})"
    )

def cmd_stats(cid):
    try:
        d=requests.get(RAILWAY_API+"/api/stats",timeout=10).json()
        send(cid,f"*Bot Stats*\nMode: {d.get(chr(98)+chr(111)+chr(116)+chr(95)+chr(109)+chr(111)+chr(100)+chr(101),chr(63))}\nScanned: {d.get(chr(109)+chr(97)+chr(114)+chr(107)+chr(101)+chr(116)+chr(115)+chr(95)+chr(115)+chr(99)+chr(97)+chr(110)+chr(110)+chr(101)+chr(100),0)}\nEdges: {d.get(chr(101)+chr(100)+chr(103)+chr(101)+chr(115)+chr(95)+chr(102)+chr(111)+chr(117)+chr(110)+chr(100),0)}\n[Dashboard]({DASHBOARD_URL})")
    except Exception as e: send(cid,f"Stats failed: {e}")

def cmd_pnl(cid):
    try:
        d=requests.get(RAILWAY_API+"/api/trader/stats",timeout=10).json()
        cities=d.get("city_leaderboard",[])[:5]
        city_lines="".join(f"- {c[chr(99)+chr(105)+chr(116)+chr(121)]}: {c.get(chr(119)+chr(105)+chr(110)+chr(95)+chr(114)+chr(97)+chr(116)+chr(101),0)*100:.0f}% WR | ${c.get(chr(112)+chr(110)+chr(108),0):+.2f}\n" for c in cities)
        send(cid,
            f"*P&L Report*\n"
            f"Today: ${d.get(chr(100)+chr(97)+chr(105)+chr(108)+chr(121)+chr(95)+chr(112)+chr(110)+chr(108),0):+.2f}\n"
            f"All-time: ${d.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(112)+chr(110)+chr(108),0):+.2f}\n"
            f"Record: {d.get(chr(119)+chr(105)+chr(110)+chr(115),0)}W/{d.get(chr(108)+chr(111)+chr(115)+chr(115)+chr(101)+chr(115),0)}L ({d.get(chr(119)+chr(105)+chr(110)+chr(95)+chr(114)+chr(97)+chr(116)+chr(101),0)*100:.1f}%)\n"
            f"\n*Top Cities:*\n{city_lines or chr(78)+chr(111)+chr(32)+chr(100)+chr(97)+chr(116)+chr(97)}"
        )
    except Exception as e: send(cid,f"P&L failed: {e}")

def cmd_positions(cid):
    try:
        d=requests.get(RAILWAY_API+"/api/trader/positions",timeout=10).json()
        positions=d.get("positions",[])
        if not positions: send(cid,"No open positions."); return
        lines="".join(f"- *{p.get(chr(99)+chr(105)+chr(116)+chr(121),chr(63))}* {p.get(chr(115)+chr(105)+chr(100)+chr(101),chr(63))} ${p.get(chr(117)+chr(115)+chr(100)+chr(95)+chr(115)+chr(105)+chr(122)+chr(101),0):.2f} @ {p.get(chr(112)+chr(114)+chr(105)+chr(99)+chr(101),0)*100:.0f}c\n" for p in positions[:10])
        send(cid,f"*Open Positions* ({len(positions)})\n{lines}Deployed: ${d.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(100)+chr(101)+chr(112)+chr(108)+chr(111)+chr(121)+chr(101)+chr(100)+chr(95)+chr(117)+chr(115)+chr(100),0):.2f}")
    except Exception as e: send(cid,f"Positions failed: {e}")

def cmd_edge(cid):
    try:
        send(cid,"Scanning... (15-30 sec)")
        d=requests.get(RAILWAY_API+"/api/scan",timeout=45).json()
        edges=d.get("edges",[])[:8]
        if not edges: send(cid,"No edges found above threshold."); return
        lines="".join(f"- *{e.get(chr(99)+chr(105)+chr(116)+chr(121),chr(63))}* {e.get(chr(98)+chr(101)+chr(115)+chr(116)+chr(95)+chr(115)+chr(105)+chr(100)+chr(101),chr(63))} +{e.get(chr(98)+chr(101)+chr(115)+chr(116)+chr(95)+chr(101)+chr(100)+chr(103)+chr(101),0)*100:.0f}% @ {e.get(chr(121)+chr(101)+chr(115)+chr(95)+chr(112)+chr(114)+chr(105)+chr(99)+chr(101),0.5)*100:.0f}c\n" for e in edges)
        send(cid,f"*Live Edges* ({len(edges)})\n{lines}[Dashboard]({DASHBOARD_URL})")
    except Exception as e: send(cid,f"Scan failed: {e}")

def cmd_scan(cid): cmd_edge(cid)

def cmd_forecast(cid,city="london"):
    try:
        d=requests.get(RAILWAY_API+"/api/forecast/"+city.lower().replace(" ","-"),timeout=15).json()
        if "error" in d: send(cid,f"No forecast for {city}"); return
        send(cid,f"*{city.title()} Forecast*\n{d.get(chr(116)+chr(101)+chr(109)+chr(112)+chr(95)+chr(99),chr(63))}C on {d.get(chr(100)+chr(97)+chr(116)+chr(101),chr(63))}")
    except Exception as e: send(cid,f"Forecast failed: {e}")

def cmd_calibrate(cid):
    try:
        send(cid,"Running calibration refit...")
        d=requests.get(RAILWAY_API+"/api/calibration?action=fit",timeout=30).json()
        biases=d.get("top_biases",[])[:5]
        bias_lines="".join(f"- {c}: {b:+.3f}\n" for c,b in biases)
        send(cid,f"*Calibration*\nFitted: {d.get(chr(104)+chr(97)+chr(115)+chr(95)+chr(99)+chr(97)+chr(108)+chr(105)+chr(98)+chr(114)+chr(97)+chr(116)+chr(105)+chr(111)+chr(110),False)}\nCities: {d.get(chr(110)+chr(95)+chr(99)+chr(105)+chr(116)+chr(121)+chr(95)+chr(98)+chr(105)+chr(97)+chr(115)+chr(101)+chr(115),0)}\n{bias_lines or chr(78)+chr(111)+chr(110)+chr(101)+chr(32)+chr(121)+chr(101)+chr(116)}")
    except Exception as e: send(cid,f"Calibration failed: {e}")

def cmd_backtest(cid):
    try:
        send(cid,"Running 7-day backtest...")
        d=requests.get(RAILWAY_API+"/api/backtest?days=7",timeout=45).json()
        send(cid,f"*Backtest (7d)*\nTrades: {d.get(chr(116)+chr(114)+chr(97)+chr(100)+chr(101)+chr(115)+chr(95)+chr(116)+chr(97)+chr(107)+chr(101)+chr(110),0)} WR: {d.get(chr(119)+chr(105)+chr(110)+chr(95)+chr(114)+chr(97)+chr(116)+chr(101),0)*100:.1f}%\nROI: {d.get(chr(114)+chr(111)+chr(105),0)*100:.1f}% P&L: ${d.get(chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(112)+chr(110)+chr(108),0):+.2f}")
    except Exception as e: send(cid,f"Backtest failed: {e}")

def ask_claude(text,history):
    if not ANTHROPIC_KEY: return "No AI key."
    try:
        msgs=history[-6:]+[{"role":"user","content":text}]
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":400,"system":SYSTEM_PROMPT,"messages":msgs},
            timeout=20).json()
        return r.get("content",[{}])[0].get("text","No response")
    except Exception as e: return f"AI error: {e}"

_histories={}

def handle_update(update):
    msg=update.get("message",{})
    cid=msg.get("chat",{}).get("id")
    text=msg.get("text","").strip()
    if not cid or not text: return
    if text.startswith("/start"): cmd_start(cid)
    elif text.startswith("/stats"): cmd_stats(cid)
    elif text.startswith("/pnl"): cmd_pnl(cid)
    elif text.startswith("/positions"): cmd_positions(cid)
    elif text.startswith("/edge") or text.startswith("/scan"): cmd_edge(cid)
    elif text.startswith("/forecast"):
        parts=text.split(maxsplit=1)
        cmd_forecast(cid,parts[1] if len(parts)>1 else "london")
    elif text.startswith("/calibrate"): cmd_calibrate(cid)
    elif text.startswith("/backtest"): cmd_backtest(cid)
    else:
        h=_histories.get(cid,[])
        reply=ask_claude(text,h)
        h.append({"role":"user","content":text})
        h.append({"role":"assistant","content":reply})
        _histories[cid]=h[-10:]
        send(cid,reply)

def _daily_loop():
    while True:
        now=datetime.now(timezone.utc)
        if now.hour==9 and now.minute==0: send_daily_summary(); time.sleep(3600)
        time.sleep(55)

def run_polling():
    load_subs()
    threading.Thread(target=_daily_loop,daemon=True).start()
    log.info("Telegram bot started, %d subscribers",len(_subscribers))
    offset=0
    while True:
        try:
            resp=requests.get(API_BASE+"/getUpdates",
                params={"offset":offset,"timeout":30},timeout=35).json()
            for upd in resp.get("result",[]):
                offset=upd["update_id"]+1
                threading.Thread(target=handle_update,args=(upd,),daemon=True).start()
        except Exception as e: log.warning("Polling: %s",e); time.sleep(5)

def start_telegram_bot():
    threading.Thread(target=run_polling,daemon=True).start()

if __name__=="__main__":
    logging.basicConfig(level=logging.INFO)
    run_polling()