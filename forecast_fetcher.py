"""
forecast_fetcher.py - Fetches GFS 31-member ensemble forecasts.
"""
import logging, requests
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
import numpy as np
from config import cfg, get_city_coords
log = logging.getLogger(__name__)

@dataclass
class ForecastResult:
    city: str; target_date: date; source: str
    daily_max_samples: List[float] = field(default_factory=list)
    mean_max_c: float = 0.0; std_max_c: float = 0.0
    consensus_means_c: List[float] = field(default_factory=list)
    consensus_sources: List[str] = field(default_factory=list)

class ForecastFetcher:
    def __init__(self):
        self.session = requests.Session()
    def get_forecast(self, city: str, target_date: date) -> Optional[ForecastResult]:
        try:
            lat, lon = get_city_coords(city)
            url = cfg.weather.open_meteo_ensemble_url
            params = {"latitude": lat, "longitude": lon, "daily_max_2m_temperature_member00": "",
                "models": "gfs_seamless", "start_date": str(target_date), "end_date": str(target_date),
                "daily": "temperature_2m_max", "timezone": "UTC"}
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            samples = []
            daily = data.get("daily", {})
            for k in daily:
                if k.startswith("temperature_2m_max"):
                    vals = daily[k]
                    if vals and vals[0] is not None: samples.append(float(vals[0]))
            if not samples:
                vals = daily.get("temperature_2m_max", [])
                if vals and vals[0] is not None: samples = [float(vals[0])] * 10
            if not samples: return None
            arr = np.array(samples)
            return ForecastResult(city=city, target_date=target_date, source="open_meteo",
                daily_max_samples=samples, mean_max_c=float(arr.mean()), std_max_c=float(arr.std()))
        except Exception as e:
            log.warning("Forecast fetch failed for %s: %s", city, e)
            return None
    def get_past_ensemble(self, city, target_date): return self.get_forecast(city, target_date)
