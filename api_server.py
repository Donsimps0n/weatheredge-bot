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
_cache_warming = False

def _warm_forecast_cache():
    """Background thread: pre-fetch forecasts for all cities/dates every 30 min."""
    import time
    global _cache_warming
    while True:
        _cache_warming = True
        warmed = 0
        today = date.today()
        if not CITIES: time.sleep(5); continue
        for _days in range(0, 8):
            _tdate = today + timedelta(days=_days)
            for _city in (CITIES if CITIES else []):
                _key = f"{_city}_{_tdate}"
                if _key not in _forecast_cache:
                    try:
                        _fc = consensus_forecast(_city, _tdate)
                        if _fc:
                            _forecast_cache[_key] = _fc
                            warmed += 1
                    except:
                        pass
        _cache_warming = False
        _add_log(f"Cache warm complete: {warmed} new forecasts ({len(_forecast_cache)} total)")
        time.sleep(1800)
            _calibrator.fit()  # refit calibration every 30 min  # re-warm every 30 minutes

# Start background cache warmer thread on import
import threading
# Thread started after first request to ensure CITIES is populated
_warmer_started = False

@app.before_request
def start_warmer_once():
    global _warmer_started
    if not _warmer_started and CITIES:
        _warmer_started = True
        t = threading.Thread(target=_warm_forecast_cache, daemon=True)
        t.start()

CITIES = [
    # North America
    "New York","Chicago","Miami","Atlanta","Seattle","Los Angeles","Dallas","Toronto","Mexico City",
    # South America
    "Sao Paulo","Buenos Aires",
    # Europe
    "London","Paris","Milan","Madrid","Berlin","Munich","Amsterdam","Warsaw","Vienna","Zurich",
    "Rome","Stockholm","Oslo","Copenhagen","Helsinki","Dublin","Lisbon","Athens","Istanbul","Ankara",
    # Middle East / Africa
    "Dubai","Tel Aviv","Cairo","Lagos","Nairobi","Johannesburg",
    # South / SE Asia
    "Mumbai","Delhi","Lucknow","Bangkok","Kuala Lumpur","Singapore","Jakarta",
    # East Asia
    "Tokyo","Seoul","Beijing","Shanghai","Hong Kong","Shenzhen","Taipei","Chongqing","Chengdu","Wuhan",
    # Oceania
    "Sydney","Wellington",
]

CITY_COORDS = {
    # North America
    "New York":     (40.6413, -73.7781),  # KJFK
    "Chicago":      (41.9742, -87.9073),  # KORD
    "Miami":        (25.7959, -80.2870),  # KMIA
    "Atlanta":      (33.6407, -84.4277),  # KATL
    "Seattle":      (47.4489,-122.3094),  # KSEA
    "Los Angeles":  (33.9425,-118.4081),  # KLAX
    "Dallas":       (32.8998, -97.0403),  # KDFW
    "Toronto":      (43.6777, -79.6248),  # CYYZ
    "Mexico City":  (19.4363, -99.0721),  # MMMX
    # South America
    "Sao Paulo":    (-23.4356,-46.4731),  # SBGR
    "Buenos Aires": (-34.8222,-58.5358),  # SAEZ
    # Europe
    "London":       (51.4775,  -0.4614),  # EGLL
    "Paris":        (49.0128,   2.5500),  # LFPG
    "Milan":        (45.6306,   8.7231),  # LIMC
    "Madrid":       (40.4936,  -3.5668),  # LEMD
    "Berlin":       (52.3667,  13.5033),  # EDDB
    "Munich":       (48.3537,  11.7750),  # EDDM
    "Amsterdam":    (52.3086,   4.7639),  # EHAM
    "Warsaw":       (52.1657,  20.9671),  # EPWA
    "Vienna":       (48.1103,  16.5697),  # LOWW
    "Zurich":       (47.4582,   8.5555),  # LSZH
    "Rome":         (41.8003,  12.2389),  # LIRF
    "Stockholm":    (59.6519,  17.9186),  # ESSA
    "Oslo":         (60.1939,  11.1004),  # ENGM
    "Copenhagen":   (55.6180,  12.6560),  # EKCH
    "Helsinki":     (60.3183,  24.9497),  # EFHK
    "Dublin":       (53.4213,  -6.2700),  # EIDW
    "Lisbon":       (38.7756,  -9.1354),  # LPPT
    "Athens":       (37.9364,  23.9445),  # LGAV
    "Istanbul":     (41.2753,  28.7519),  # LTFM
    "Ankara":       (40.1281,  32.9951),  # LTAC
    # Middle East / Africa
    "Dubai":        (25.2528,  55.3644),  # OMDB
    "Tel Aviv":     (32.0114,  34.8867),  # LLBG
    "Cairo":        (30.1219,  31.4056),  # HECA
    "Lagos":        ( 6.5774,   3.3212),  # DNMM
    "Nairobi":      (-1.3192,  36.9275),  # HKJK
    "Johannesburg": (-26.1392, 28.2460),  # FAOR
    # South / SE Asia
    "Mumbai":       (19.0896,  72.8656),  # VABB
    "Delhi":        (28.5562,  77.1000),  # VIDP
    "Lucknow":      (26.7606,  80.8893),  # VILK
    "Bangkok":      (13.6811, 100.7472),  # VTBS
    "Kuala Lumpur": ( 2.7456, 101.7099),  # WMKK
    "Singapore":    ( 1.3644, 103.9915),  # WSSS
    "Jakarta":      (-6.1256, 106.6559),  # WIII
    # East Asia
    "Tokyo":        (35.5533, 139.7811),  # RJTT
    "Seoul":        (37.4692, 126.4505),  # RKSI
    "Beijing":      (40.0799, 116.5846),  # ZBAA
    "Shanghai":     (31.1443, 121.8083),  # ZSPD
    "Hong Kong":    (22.3080, 113.9185),  # VHHH
    "Shenzhen":     (22.6396, 113.8107),  # ZGSZ
    "Taipei":       (25.0777, 121.2328),  # RCTP
    "Chongqing":    (29.7192, 106.6419),  # ZUCK
    "Chengdu":      (30.5785, 103.9473),  # ZUUU
    "Wuhan":        (30.7838, 114.2081),  # ZHHH
    # Oceania
    "Sydney":       (-33.9399, 151.1753), # YSSY
    "Wellington":   (-41.3272, 174.8051), # NZWN
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

# Start Telegram bot in background thread at module load time (works with Flask)
try:
    from telegram_bot import start_telegram_bot
    start_telegram_bot()
    print("Telegram bot started ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ¢ÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂÃÂ t.me/Aidolf_bot")
except Exception as e:
    print(f"Telegram bot failed: {e}")

@app.route("/landing")
def landing():
    try:
        from flask import send_file, Response
        import os
        lpath = os.path.join(os.path.dirname(__file__), 'landing.html')
        if os.path.exists(lpath):
            return Response(open(lpath).read(), mimetype='text/html')
        return Response('<h1>Landing page not found</h1>', mimetype='text/html')
    except Exception as e:
        return str(e), 500

@app.route("/")
def index(): return jsonify({"status": "ok", "service": "WeatherEdge Bot API", "models": _stats["models"],
        "cache_size": len(_forecast_cache),
        "cache_warming": _cache_warming, "uptime": BOT_START})

@app.route("/api/stats")
def stats(): return jsonify(_stats)

@app.route("/api/positions")
def positions(): return jsonify(_db_trades("open"))

@app.route("/api/history")
def history(): return jsonify(_db_trades("closed", 50))


@app.route("/api/calibration")
def calibration_status():
    """Calibration summary and manual refit trigger."""
    action = request.args.get("action","status")
    if action == "fit":
        _calibrator.fit()
    return jsonify(_calibrator.summary())

@app.route("/api/traders")
def traders():
    """Top weather traders leaderboard - live with static fallback."""
    STATIC_TRADERS = [{"rank":1,"trader_name":"ColdMath","trader":"0x594edb9112f526fa6a80b8f858a6379c8a2c1c11","overall_gain":78345,"win_rate":0.837,"event_ct":1116,"win_amount":103184,"loss_amount":-24839},{"rank":2,"trader_name":"Poligarch","trader":"0xb40e8967e2a7cc9","overall_gain":74146,"win_rate":1,"event_ct":200,"win_amount":74146,"loss_amount":0},{"rank":3,"trader_name":"aenews2","trader":"0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1","overall_gain":75176,"win_rate":0.768,"event_ct":69,"win_amount":105925,"loss_amount":-30749},{"rank":4,"trader_name":"automatedAItradingbot","trader":"0xd8f8c13644ea84d62e1ec88c5d1215e436eb0f11","overall_gain":62165,"win_rate":0.35,"event_ct":634,"win_amount":73457,"loss_amount":-11291},{"rank":5,"trader_name":"Handsanitizer23","trader":"0x05e70727a2e2dcd079baa2ef1c0b88af06bb9641","overall_gain":58814,"win_rate":0.522,"event_ct":23,"win_amount":75087,"loss_amount":-16273},{"rank":6,"trader_name":"Hans323","trader":"0x0f37cb80dee49d55b5f6d9e595d52591d6371410","overall_gain":80156,"win_rate":0.609,"event_ct":665,"win_amount":91477,"loss_amount":-11321},{"rank":7,"trader_name":"NoonienSoong","trader":"0xnoonien","overall_gain":25095,"win_rate":0.963,"event_ct":50,"win_amount":25095,"loss_amount":0},{"rank":8,"trader_name":"JoeTheMeteorologist","trader":"0xjoemeteor","overall_gain":22131,"win_rate":0.338,"event_ct":34,"win_amount":29113,"loss_amount":-6982},{"rank":9,"trader_name":"CoffeeLover","trader":"0xc1b399b26020a4f689108e9640fd3dc8ffc8501c","overall_gain":51516,"win_rate":0.889,"event_ct":19,"win_amount":51902,"loss_amount":-386},{"rank":10,"trader_name":"HondaCivic","trader":"0x15ceffed7bf820cd2d90f90ea24ae9909f5cd5fa","overall_gain":42904,"win_rate":0.908,"event_ct":199,"win_amount":56247,"loss_amount":-13343}]
    try:
        import urllib.request, urllib.parse
        params = urllib.parse.urlencode({
            "tag":"Weather","sortDirection":"ASC","limit":"10","offset":"0",
            "sortColumn":"rank","minPnL":"-59949","maxPnL":"227556",
            "minWinRate":"0","maxWinRate":"97","minTotalPositions":"1","maxTotalPositions":"19850"
        })
        url = "https://polymarketanalytics.com/api/traders-tag-performance?" + params
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://polymarketanalytics.com/traders",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        import json as _json
        data = _json.loads(raw)
        live = data.get("data", data) if isinstance(data, dict) else data
        if live and len(live) > 0:
            return jsonify({"traders": live[:10], "source": "live", "total": len(live)})
    except Exception as e:
        log.warning(f"Traders live fetch failed: {e}, using static fallback")
    return jsonify({"traders": STATIC_TRADERS, "source": "static", "total": len(STATIC_TRADERS)})
