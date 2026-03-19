"""
nws_fetcher.py
==============
Fetches real NWS (National Weather Service) observations + hourly forecast
for US cities, combining them to get the most accurate daily maximum.

Why this matters
----------------
The GFS ensemble forecasts what *will* happen. But once the day is partially
over, the highest temperature may have already occurred. NWS stations report
real observations every hour. Blending past observations with the remaining
forecast gives a tighter estimate of the final daily maximum.
For a market resolving at midnight:
  - At 8am:  forecast contribution = 100%
  - At 2pm:  obs contribution = 50%, forecast = 50%  
  - At 8pm:  obs contribution = 85%, forecast = 15%
"""
from __future__ import annotations
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Dict, Optional
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
log = logging.getLogger(__name__)
NWS_US_CITIES = {
    "New York": {"station_id": "KLGA", "forecast_url": "https://api.weather.gov/gridpoints/OKX/37,39/forecast/hourly"},
    "Chicago": {"station_id": "KORD", "forecast_url": "https://api.weather.gov/gridpoints/LOT/66,77/forecast/hourly"},
    "Miami": {"station_id": "KMIA", "forecast_url": "https://api.weather.gov/gridpoints/MFL/106,51/forecast/hourly"},
    "Seattle": {"station_id": "KSEA", "forecast_url": "https://api.weather.gov/gridpoints/SEW/124,61/forecast/hourly"},
    "Atlanta": {"station_id": "KATL", "forecast_url": "https://api.weather.gov/gridpoints/FFC/50,82/forecast/hourly"},
}
class NWSFetcher:
    def __init__(self): self.session = requests.Session()
    def is_us_city(self, city): return city in NWS_US_CITIES
    def get_daily_max_f(self, city, target_date):
        city_data = NWS_US_CITIES.get(city);
        if not city_data: return None
        daily_max = {}
        try:
            obs_url = f"https://api.weather.gov/stations/{city_data['station_id']}/observations?limit=48"
            obs_data = self.session.get(obs_url, timeout=12).json()
            for obs in obs_data.get("features", []):
                props = obs.get("properties", {})
                ts = str(props.get("timestamp", ""))[:10]
                temp_c = props.get("temperature", {}).get("value")
                if isinstance(temp_c, (int, float)):
                    temp_f = temp_c * 9 / 5 + 32
                    if ts not in daily_max or temp_f > daily_max[ts]: daily_max[ts] = temp_f
        except Exception: pass
        try:
            fc_data = self.session.get(city_data["forecast_url"], timeout=12).json()
            for period in fc_data.get("properties", {}).get("periods", []):
                ts = str(period.get("startTime", ""))[:10]
                temp = period.get("temperature")
                if isinstance(temp, (int, float)):
                    temp_f = float(temp)
                    if ts not in daily_max or temp_f > daily_max[ts]: daily_max[ts] = temp_f
        except Exception: pass
        return daily_max.get(str(target_date))
    def blend_with_ensemble(self, city, target_date, mean_c, std_c):
        nws_c = self.get_daily_max_f(city, target_date)
        if nws_c is None: return mean_c, std_c
        nws_c = (nws_c - 32) * 5 / 9
        now_utc = datetime.now(timezone.utc)
        obs_w = min(now_utc.hour / 24.0, 0.95)
        return obs_w * nws_c + (1 - obs_w) * mean_c, (1 - obs_w) * std_c
