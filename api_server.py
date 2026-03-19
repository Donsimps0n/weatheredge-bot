"""
api_server.py - WeatherEdge Flask API with 4-model consensus forecasting.
Models: GFS (31-member ensemble) + ECMWF IFS025 (51-member) + UKMO + MeteoFrance
"""
import json, os, sqlite3, requests, math
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = Path(os.getenv("DB_PATH", "data/trades.db"))
Path("data").mkdir(exist_ok=True)
BOT_START = datetime.now(timezone.utc).isoformat()
_stats = {
    "markets_scanned": 0, "edges_found": 0, "live_edges": 0,
    "gfs_members": 31, "ecmwf_members": 51, "last_scan": None,
    "bot_mode": os.getenv("TRADING_MODE", "PAPER"),
    "started_at": BOT_START,
    "models": ["GFS-31", "ECMWF-IFS025-51", "UKMO", "MeteoFrance"],
}
_scan_log = []
_forecast_cache = {}

CITIES = ["London","New York","Chicago","Miami","Atlanta","Seattle","Tokyo","Seoul",
          "Dubai","Sydney","Paris","Tel Aviv","Singapore","Hong Kong","Berlin","Toronto"]

CITY_COORDS = {
    "London":    (51.4775, -0.4614), "Paris":     (49.0128,  2.5500),
    "New York":  (40.7769,-73.8740), "Chicago":   (41.9742,-87.9073),
    "Miami":     (25.7959,-80.2870), "Atlanta":   (33.6407,-84.4277),
    "Seattle":   (47.4489,-122.3094),"Tokyo":     (35.5533,139.7811),
    "Seoul":     (37.4692,126.4505), "Dubai":     (25.2528, 55.3644),
    "Sydney":    (-33.9399,151.1753),"Tel Aviv":  (32.0114, 34.8867),
    "Singapore": (1.3502, 103.9940), "Hong Kong": (22.3080,113.9185),
    "Berlin":    (52.3667, 13.5033), "Toronto":   (43.6772,-79.6306),
}

def get_gfs_ensemble(lat, lon, target_date):
    """GFS 31-member ensemble from Open-Meteo."""
    try:
        r = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble",
            params={"latitude":lat,"longitude":lon,"models":"gfs_seamless",
                    "daily":"temperature_2m_max","start_date":str(target_date),
                    "end_date":str(target_date),"timezone":"UTC"}, timeout=15)
        d = r.json(); daily = d.get("daily",{})
        vals = [daily[k][0] for k in daily if k.startswith("temperature_2m_max") and daily[k] and daily[k][0] is not None]
        return vals if len(vals) >= 5 else []
    except: return []

def get_ecmwf_ensemble(lat, lon, target_date):
    """ECMWF IFS025 51-member ensemble from Open-Meteo."""
    try:
        r = requests.get("https://ensemble-api.open-meteo.com/v1/ensemble",
            params={"latitude":lat,"longitude":lon,"models":"ecmwf_ifs025",
                    "daily":"temperature_2m_max","start_date":str(target_date),
                    "end_date":str(target_date),"timezone":"UTC"}, timeout=15)
        d = r.json(); daily = d.get("daily",{})
        vals = [daily[k][0] for k in daily if k.startswith("temperature_2m_max") and daily[k] and daily[k][0] is not None]
        return vals if len(vals) >= 5 else []
    except: return []

def get_deterministic(lat, lon, target_date, model):
    """Single deterministic forecast from UKMO, MeteoFrance, ICON."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
            params={"latitude":lat,"longitude":lon,"models":model,
                    "daily":"temperature_2m_max","start_date":str(target_date),
                    "end_date":str(target_date),"timezone":"UTC"}, timeout=10)
        d = r.json()
        val = d.get("daily",{}).get("temperature_2m_max",[None])[0]
        return float(val) if val is not None else None
    except: return None

def get_nws_observation(city, target_date):
    """NWS real station observations for US cities (intraday blending)."""
    NWS_STATIONS = {
        "New York":"KLGA","Chicago":"KORD","Miami":"KMIA",
        "Atlanta":"KATL","Seattle":"KSEA",
    }
    station = NWS_STATIONS.get(city)
    if not station: return None
    try:
        r = requests.get(f"https://api.weather.gov/stations/{station}/observations?limit=24",
            headers={"User-Agent":"polymarket-weather-bot/1.0"}, timeout=10)
        features = r.json().get("features",[])
        today_str = str(target_date)
        temps = []
        for obs in features:
            props = obs.get("properties",{})
            ts = str(props.get("timestamp",""))[:10]
            temp_c = props.get("temperature",{}).get("value")
            if ts == today_str and isinstance(temp_c,(int,float)):
                temps.append(float(temp_c))
        return max(temps) if temps else None
    except: return None

def consensus_forecast(city, target_date):
    """4-model consensus: GFS ensemble + ECMWF ensemble + UKMO + MeteoFrance."""
    cache_key = f"{city}_{target_date}"
    if cache_key in _forecast_cache:
        return _forecast_cache[cache_key]

    coords = CITY_COORDS.get(city)
    if not coords: return None
    lat, lon = coords

    # Fetch all models in parallel-ish
    gfs_vals  = get_gfs_ensemble(lat, lon, target_date)
    ecmwf_vals= get_ecmwf_ensemble(lat, lon, target_date)
    ukmo      = get_deterministic(lat, lon, target_date, "ukmo_seamless")
    mf        = get_deterministic(lat, lon, target_date, "meteofrance_seamless")
    nws_obs   = get_nws_observation(city, target_date)

    # Build combined sample pool (weight ensembles more heavily)
    all_samples = gfs_vals + ecmwf_vals
    det_points = [x for x in [ukmo, mf] if x is not None]

    # Add deterministic models as pseudo-members (3x weight each for signal)
    for v in det_points:
        all_samples.extend([v, v, v])

    if not all_samples: return None

    # Intraday NWS blending: if we have real obs for today, blend them in
    now_utc = datetime.now(timezone.utc)
    if nws_obs is not None and str(date.today()) == str(target_date):
        obs_weight = min(now_utc.hour / 24.0, 0.90)
        ens_weight = 1.0 - obs_weight
        blended_samples = [nws_obs] * int(len(all_samples)*obs_weight*2) + all_samples
        all_samples = blended_samples

    mean = sum(all_samples) / len(all_samples)
    variance = sum((x-mean)**2 for x in all_samples) / len(all_samples)
    std = math.sqrt(variance)

    result = {
        "mean_c": round(mean, 2),
        "std_c":  round(std, 2),
        "samples": all_samples,
        "n_gfs": len(gfs_vals),
        "n_ecmwf": len(ecmwf_vals),
        "ukmo": ukmo,
        "meteofrance": mf,
        "nws_obs": nws_obs,
        "model_spread": round(max(([ukmo,mf,mean] if ukmo and mf else [mean])) - min(([ukmo,mf,mean] if ukmo and mf else [mean])),1),
    }
    _forecast_cache[cache_key] = result
    return result

def prob_for_bin(samples, lo, hi):
    """Probability a temperature falls in [lo, hi) using KDE-ish Bayesian estimate."""
    if not samples: return 0.5, 1.0
    import math
    n = len(samples)
    k = sum(1 for s in samples if (lo is None or s >= lo) and (hi is None or s < hi))
    # Beta-Binomial posterior with weak prior
    a, b = 2.0 + k, 2.0 + (n - k)
    mean = a / (a + b)
    std  = math.sqrt(a*b / ((a+b)**2 * (a+b+1)))
    return round(mean, 4), round(std, 4)

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
def index(): return jsonify({"status": "ok", "service": "WeatherEdge Bot API", "models": _stats["models"], "uptime": BOT_START})

@app.route("/api/stats")
def stats(): return jsonify(_stats)

@app.route("/api/positions")
def positions(): return jsonify(_db_trades("open"))

@app.route("/api/history")
def history(): return jsonify(_db_trades("closed", 50))

@app.route("/api/log")
def log_view(): return jsonify(_scan_log[-100:])

@app.route("/api/health")
def health(): return jsonify({"status": "healthy", "mode": _stats["bot_mode"], "last_scan": _stats["last_scan"], "models": _stats["models"]})

@app.route("/api/forecast/<city>")
def forecast_city(city):
    """Get 4-model consensus forecast for a city."""
    days_ahead = int(request.args.get("days", 1))
    target = date.today() + timedelta(days=days_ahead)
    fc = consensus_forecast(city, target)
    if not fc: return jsonify({"error": f"No forecast for {city}"}), 404
    return jsonify({"city": city, "date": str(target), **fc})

@app.route("/api/scan")
def scan():
    """Full scan: fetch markets + 4-model consensus forecast + edge detection."""
    _forecast_cache.clear()  # Fresh forecasts each scan
    try:
        resp = requests.get("https://gamma-api.polymarket.com/events",
            params={"tag_slug":"weather","active":"true","closed":"false","limit":"200",
                    "order":"startDate","ascending":"false"},
            timeout=20, headers={"User-Agent":"polymarket-weather-bot/1.0"})
        events = resp.json()
        if not isinstance(events, list): events = events.get("events", [])

        total_markets = 0
        results = []
        edges = []

        for event in events:
            title = event.get("title","")
            city = next((c for c in CITIES if c.lower() in title.lower()), None)
            if not city: continue

            # Parse date from event title
            target_date = None
            import re
            m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d+)', title, re.IGNORECASE)
            if m:
                months = ["january","february","march","april","may","june","july","august","september","october","november","december"]
                month_num = months.index(m.group(1).lower()) + 1
                day_num = int(m.group(2))
                try:
                    year = date.today().year
                    target_date = date(year, month_num, day_num)
                    if target_date < date.today() - timedelta(days=1):
                        target_date = date(year+1, month_num, day_num)
                except: target_date = None

            for mkt in event.get("markets", []):
                if not mkt.get("active"): continue
                total_markets += 1
                prices = mkt.get("outcomePrices","[0.5,0.5]")
                if isinstance(prices, str):
                    try: prices = json.loads(prices)
                    except: prices = [0.5, 0.5]
                yes_price = float(prices[0]) if prices else 0.5
                vol = float(mkt.get("volume") or 0)

                result = {
                    "city": city,
                    "question": mkt.get("question",""),
                    "yes_price": round(yes_price, 4),
                    "condition_id": mkt.get("conditionId",""),
                    "volume": round(vol, 2),
                    "target_date": str(target_date) if target_date else None,
                }
                results.append(result)

        _stats["markets_scanned"] = total_markets
        _stats["last_scan"] = datetime.now(timezone.utc).isoformat()
        _add_log(f"Scanned {total_markets} markets across {len(events)} events (4-model consensus ready)")

        return jsonify({
            "markets": total_markets,
            "events": len(events),
            "results": sorted(results, key=lambda x: x["volume"], reverse=True)[:50],
            "models_active": _stats["models"],
        })
    except Exception as e:
        return jsonify({"error": str(e), "markets": 0}), 500

@app.route("/api/edge/<condition_id>")
def edge_for_market(condition_id):
    """Get full 4-model edge analysis for a specific market condition ID."""
    city = request.args.get("city","")
    target_date_str = request.args.get("date","")
    lo = request.args.get("lo", type=float)
    hi = request.args.get("hi", type=float)

    if not city or not target_date_str:
        return jsonify({"error": "city and date params required"}), 400

    try:
        target_date = date.fromisoformat(target_date_str)
    except:
        return jsonify({"error": "invalid date"}), 400

    fc = consensus_forecast(city, target_date)
    if not fc: return jsonify({"error": "no forecast available"}), 404

    model_prob, uncertainty = prob_for_bin(fc["samples"], lo, hi)
    yes_price = float(request.args.get("yes_price", 0.5))
    fee = 0.02

    edge_yes = model_prob - yes_price - fee
    edge_no  = (1 - model_prob) - (1 - yes_price) - fee
    best_side = "YES" if edge_yes > edge_no else "NO"
    best_edge = max(edge_yes, edge_no)

    return jsonify({
        "condition_id": condition_id,
        "city": city,
        "date": target_date_str,
        "model_prob": model_prob,
        "uncertainty": uncertainty,
        "yes_price": yes_price,
        "edge_yes": round(edge_yes, 4),
        "edge_no": round(edge_no, 4),
        "best_side": best_side,
        "best_edge": round(best_edge, 4),
        "tradeable": best_edge >= 0.05,
        "forecast": {
            "mean_c": fc["mean_c"], "std_c": fc["std_c"],
            "n_gfs": fc["n_gfs"], "n_ecmwf": fc["n_ecmwf"],
            "ukmo": fc["ukmo"], "meteofrance": fc["meteofrance"],
            "nws_obs": fc["nws_obs"], "model_spread": fc["model_spread"],
            "total_samples": len(fc["samples"]),
        }
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"WeatherEdge API starting on port {port} with 4-model consensus")
    app.run(host="0.0.0.0", port=port, debug=False)
