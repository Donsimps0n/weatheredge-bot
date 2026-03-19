"""
api_server.py - Lightweight Flask API for WeatherEdge bot.
"""
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.getenv("DB_PATH", "data/trades.db"))
BOT_START = datetime.now(timezone.utc).isoformat()
_scan_log = []
_stats = {
    "markets_scanned": 0, "edges_found": 0, "live_edges": 0,
    "gfs_members": 31, "last_scan": None,
    "bot_mode": os.getenv("TRADING_MODE", "PAPER"),
    "started_at": BOT_START,
}

def _db_trades(status="open", limit=20):
    if not DB_PATH.exists(): return []
    try:
        con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
        rows = con.execute(f"SELECT * FROM trades WHERE status=? ORDER BY opened_at DESC LIMIT ?", (status, limit)).fetchall()
        con.close(); return [dict(r) for r in rows]
    except: return []

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

def update_stats(markets, edges, live):
    _stats["markets_scanned"] = markets; _stats["edges_found"] = edges
    _stats["live_edges"] = live; _stats["last_scan"] = datetime.now(timezone.utc).isoformat()

def add_log(msg, level="info"):
    _scan_log.append({"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "msg": msg, "level": level})
    if len(_scan_log) > 500: _scan_log.pop(0)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"WeatherEdge API starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
