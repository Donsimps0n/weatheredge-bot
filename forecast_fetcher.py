"""
forecast_fetcher.py - Multi-model ensemble forecast fetcher.
Models: GFS (seamless), ECMWF IFS (025), ICON-EU, GEM-Global.
Consensus = pooled samples from all available models.
Inter-model disagreement increases std_c -> edge calc shrinks position size.
"""
import logging, requests
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
import numpy as np
from config import cfg, get_city_coords
log = logging.getLogger(__name__)

# Open-Meteo models ranked by reliability for daily Tmax
# Each returns temperature_2m_max member ensembles
MODELS = [
    "gfs_seamless",       # GFS 31-member, best global coverage
    "ecmwf_ifs025",       # ECMWF 51-member, most accurate globally
    "icon_seamless",      # ICON EU+Global, excellent Europe/Middle East
    "gem_seamless",       # Canadian GEM, good Americas
]

@dataclass
class ForecastResult:
    city: str
    target_date: date
    samples: List[float] = field(default_factory=list)
    std_c: float = 1.5       # inter-model + ensemble spread
    temp_c: float = 0.0      # consensus mean
    models_used: int = 0     # how many models contributed
    model_agreement: float = 1.0  # 1.0=perfect, 0.0=chaos
    source: str = "multi-model"

class ForecastFetcher:
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self):
        self.session = requests.Session()

    def _fetch_model(self, lat: float, lon: float, model: str, target_date: date) -> List[float]:
        """Fetch Tmax samples from one model. Returns list of member temps."""
        try:
            resp = self.session.get(
                self.BASE_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "models": model,
                    "daily": "temperature_2m_max",
                    "start_date": str(target_date),
                    "end_date": str(target_date),
                    "timezone": "UTC",
                },
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            daily = data.get("daily", {})
            samples = []
            for k, vals in daily.items():
                if k.startswith("temperature_2m_max") and vals and vals[0] is not None:
                    samples.append(float(vals[0]))
            return samples if samples else []
        except Exception as e:
            log.debug(f"Model {model} failed for {lat},{lon}: {e}")
            return []

    def fetch(self, city: str, target_date: date) -> Optional[ForecastResult]:
        """Fetch multi-model consensus forecast for a city."""
        try:
            lat, lon = get_city_coords(city)
        except Exception:
            log.warning(f"No coords for city: {city}")
            return None

        all_samples = []
        model_means = []
        models_used = 0

        for model in MODELS:
            samples = self._fetch_model(lat, lon, model, target_date)
            if samples:
                all_samples.extend(samples)
                model_means.append(float(np.mean(samples)))
                models_used += 1

        if not all_samples:
            log.warning(f"No forecast data for {city} on {target_date}")
            return None

        arr = np.array(all_samples)
        mean_temp = float(np.mean(arr))
        # std_c = ensemble spread (within-model) + inter-model spread
        ensemble_std = float(np.std(arr))
        inter_model_std = float(np.std(model_means)) if len(model_means) > 1 else 0.0
        # Combined uncertainty — inter-model disagreement is most important
        combined_std = float(np.sqrt(ensemble_std**2 + inter_model_std**2))
        # Agreement score: 0=total disagreement (>4C spread), 1=perfect
        agreement = float(max(0.0, 1.0 - inter_model_std / 4.0))

        log.info(
            f"{city} {target_date}: mean={mean_temp:.1f}C std={combined_std:.2f} "
            f"models={models_used} agreement={agreement:.2f} n_samples={len(all_samples)}"
        )
        return ForecastResult(
            city=city, target_date=target_date,
            samples=all_samples, std_c=round(combined_std, 2),
            temp_c=round(mean_temp, 2), models_used=models_used,
            model_agreement=round(agreement, 2),
            source=f"multi-model({models_used})",
        )

    # Legacy alias used by api_server.py
    def get_forecast(self, city: str, target_date: date) -> Optional[ForecastResult]:
        return self.fetch(city, target_date)
    def get_past_ensemble(self, city, target_date):
        return self.fetch(city, target_date)