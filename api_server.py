"""
api_server.py — Lightweight Flask API
Exposes bot state so the iamweather.vercel.app dashboard can show live data.
Also acts as Railway's web process to keep the dyno alive.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allow iamweather.vercel.app to fetch

DB_PATH = Path(os.getenv("DB_PATH", "trades.db"))
BOT_START = datetime.now(timezone.utc).isoformat()
_scan_log: list[dict] = []
_positions: list[dict] = []
_stats = {
    "markets_scanned": 0,
    "edges_found": 0,
    "live_edges": 0,
    "gfs_members": 30,
    "last_scan": None,
    "bot_mode": os.getenv("TRADING_MODE", "PAPER"),
    "started_at": BOT_START,
}

def _db_positions() -> list[dict]:
    """Pull open positions from SQLite if it exists."""
    if not DH_PATH.exists():
        return []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY opened_at DESC LIMIT 20"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _db_history() -> list[dict]:
    """Pull closed trades from SQLite."""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades WHERE status='closed' ORDER BY closed_at DESC LIMIT 50"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "WeatherEdge Bot API"})

@app.route("/api/stats")
def stats():
    return jsonify(_stats)

@app.route("/api/positions")
def positions():
    return jsonify(_db_positions() or _positions)

@app.route("/api/history")
def history():
    return jsonify(_db_history())

@app.route("/api/log")
def log():
    return jsonify(_scan_log[-100:])

@app.route("/api/health")
def health():
    return jsonify({"status": "healthy", "mode": _stats["bot_mode"], "last_scan": _stats["last_scan"]})

def update_stats(markets: int, edges: int, live: int):
    _stats["markets_scanned"] = markets
    _stats["edges_found"] = edges
    _stats["live_edges"] = live
    _stats["last_scan"] = datetime.now(timezone.utc).isoformat()

def add_log(msg: str, level: str = "info"):
    _scan_log.append({"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "msg": msg, "level": level})
    if len(_scan_log) > 500: _scan_log.pop(0)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
