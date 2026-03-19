"""
api_server.py - WeatherEdge Flask API with live market scanning.
"""
import json, os, sqlite3, requests
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.getenv("DB_PATH", "data/trades.db"))
Path("data").mkdir(exist_ok=True)
BOT_START = datetime.now(timezone.utc).isoformat()
_stats = {
    "markets_scanned": 0, "edges_found": 0, "live_edges": 0,
    "gfs_members": 31, "last_scan": None,
    "bot_mode": os.getenv("TRADING_MODE", "PAPER"),
    "started_at": BOT_START,
}
_scan_log = []

CITIES = ["London","New York","Chicago","Miami","Atlanta","Seattle","Tokyo","Seoul","Dubai","Sydney","Paris","Tel Aviv","Singapore","Hong Kong"]

def _db_trades(status="open", limit=20):
    if not DB_PATH.exists(): return []
    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM trades WHERE status=? ORDER BY opened_at DESC LIMIT ?", (status, limit)).fetchall()
        con.close(); return [dict(r) for r in rows]
    except: return []

def _add_log(msg, level="info"):
    _scan_log.append({"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "msg": msg, "level": level})
    if len(_scan_log) > 500: _scan_log.pop(0)

@app.route("/")
def index(): return jsonify({"status": "ok", "service": "WeatherEdge Bot API", "uptime": BOT_START})

@app.route("/api/stats")
def stats(): return jsonify(_stats)

@app.route("/api/positions")
def positions(): return jsonify(_db_trades("open"))

@app.route("/api/history")
def history(): return jsonify(_db_trades("closed", 50))

@app.route("/api/log")
def log_view(): return jsonify(_scan_log[-100:])

@app.route("/api/health")
def health(): return jsonify({"status": "healthy", "mode": _stats["bot_mode"], "last_scan": _stats["last_scan"]})

@app.route("/api/scan")
def scan():
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={"tag_slug":"weather","active":"true","closed":"false","limit":"200","order":"startDate","ascending":"false"},
            timeout=20, headers={"User-Agent":"polymarket-weather-bot/1.0"}
        )
        events = resp.json()
        if not isinstance(events, list): events = events.get("events", [])
        total_markets = 0
        found = []
        for event in events:
            title = event.get("title","")
            city = next((c for c in CITIES if c.lower() in title.lower()), None)
            if not city: continue
            for mkt in event.get("markets", []):
                if not mkt.get("active"): continue
                total_markets += 1
                prices = mkt.get("outcomePrices","[0.5,0.5]")
                if isinstance(prices, str):
                    try: prices = json.loads(prices)
                    except: prices = [0.5, 0.5]
                yes_price = float(prices[0]) if prices else 0.5
                found.append({
                    "city": city,
                    "question": mkt.get("question",""),
                    "yes_price": yes_price,
                    "condition_id": mkt.get("conditionId",""),
                    "volume": float(mkt.get("volume") or 0)
                })
        _stats["markets_scanned"] = total_markets
        _stats["last_scan"] = datetime.now(timezone.utc).isoformat()
        _add_log(f"Scanned {total_markets} markets across {len(events)} events")
        return jsonify({"markets": total_markets, "events": len(events), "results": found[:50]})
    except Exception as e:
        return jsonify({"error": str(e), "markets": 0}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"WeatherEdge API starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
