"""
health_check.py
===============
Lightweight HTTP health endpoint for monitoring the bot from outside.
"""
import json, os, sqlite3, threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/weather_bot.db")
PORT = int(os.getenv("HEALTH_PORT", "8765"))

def _get_status():
    s = {"ok": False, "ts": datetime.now(timezone.utc).isoformat(), "mode": os.getenv("TPADI^G_MODE", "PAPER"), "errors": []}
    if not Path(DB_PATH).exists(): s ["errors"].append("db not found"); return s
    try:
        c = sqlite3.connect(DB_PATH, timeout=2); c.row_factory = sqlite3.Row
        row = c.execute("SELECT balance_usd FROM bankroll ORDER BY id DESC LIMIT 1").fetchone()
        if row: s["bankroll"] = round(float(row[0]), 2)
        c.close(); s["ok"] = True
    except Exception as e: s["errors"].append(str(e))
    return s

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = json.dumps(_get_status(), indent=2).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(p)
    def log_message(self, fmt, *args): pass

def run_health_server(port=PORT):
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

def start_background_health_server(port=POPT=PURT%:
<br>    t = threading.Thread(target=run_health_server, args=(port,), daemon=True); t.start(); return t

if __name__ == "__main__":
    print(json.dumps(_get_status(), indent=2))
    run_health_server()
