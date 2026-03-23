"""model_calibrator.py - Seasonal GFS bias corrections and confidence scoring.
GFS runs warm in summer (+0.8C coastal), cold in winter (-1.0C continental).
Corrects for this systematically to improve probability accuracy.
"""
import json, logging
from datetime import date
from pathlib import Path
from typing import Optional, Dict
log = logging.getLogger(__name__)

SEASONAL_BIAS = {
    "summer_nh": {"coastal": +0.8, "continental": +0.5, "tropical": +0.3, "default": +0.6},
    "winter_nh": {"coastal": -0.5, "continental": -1.0, "tropical": -0.1, "default": -0.6},
    "shoulder":  {"default": 0.0}
}

CITY_REGIONS = {
    "London":"coastal","New York":"coastal","Los Angeles":"coastal","Sydney":"coastal",
    "Tokyo":"coastal","Hong Kong":"coastal","Miami":"coastal","Boston":"coastal",
    "Seattle":"coastal","Taipei":"coastal","Singapore":"tropical","Jakarta":"tropical",
    "Lagos":"tropical","Nairobi":"tropical","Mumbai":"tropical","Bangkok":"tropical",
    "Ho Chi Minh City":"tropical","Yangon":"tropical","Kuala Lumpur":"tropical",
    "Manila":"tropical","Colombo":"tropical","Dubai":"tropical","Riyadh":"tropical",
    "Cairo":"tropical","Moscow":"continental","Warsaw":"continental","Prague":"continental",
    "Budapest":"continental","Berlin":"continental","Vienna":"continental",
    "Chicago":"continental","Denver":"continental","Dallas":"continental",
    "Atlanta":"continental","Bucharest":"continental",
}
SOUTHERN_CITIES = {"Sydney","Melbourne","Buenos Aires","Sao Paulo","Johannesburg"}

def get_season(d: date, hemisphere="north") -> str:
    m = d.month
    if hemisphere == "north":
        return "winter_nh" if m in [12,1,2] else ("summer_nh" if m in [6,7,8] else "shoulder")
    return "summer_nh" if m in [12,1,2] else ("winter_nh" if m in [6,7,8] else "shoulder")

def get_seasonal_correction(city: str, target_date: date) -> float:
    hemisphere = "south" if city in SOUTHERN_CITIES else "north"
    season = get_season(target_date, hemisphere)
    region = CITY_REGIONS.get(city, "default")
    bias_table = SEASONAL_BIAS.get(season, SEASONAL_BIAS["shoulder"])
    return bias_table.get(region, bias_table.get("default", 0.0))

def apply_corrections(samples, city, target_date, learned_bias=0.0):
    """Apply seasonal + learned bias to GFS ensemble samples."""
    seasonal = get_seasonal_correction(city, target_date)
    total = -seasonal - learned_bias
    if abs(total) < 0.05:
        return samples
    corrected = [s + total for s in samples]
    log.info("City correction: %s %+.2fC", city, total)
    return corrected

class ModelCalibrator:
    """Applies seasonal and learned bias corrections to forecast samples."""
    BIAS_FILE = Path("data/calibration.json")

    def __init__(self):
        self._city_bias: Dict[str,float] = {}
        self._load()

    def _load(self):
        if self.BIAS_FILE.exists():
            try:
                data = json.loads(self.BIAS_FILE.read_text())
                self._city_bias = data.get("city_bias", {})
            except Exception:
                pass

    def correct(self, samples, city, target_date):
        """Apply all corrections to raw ensemble samples."""
        if not samples: return samples
        learned = self._city_bias.get(city, 0.0)
        return apply_corrections(samples, city, target_date, learned_bias=learned)

    def expected_error(self, days_ahead: int) -> float:
        """Expected GFS RMSE in Celsius. Day 0=0.5, Day 7=4.0."""
        errors = {0:0.5, 1:1.0, 2:1.4, 3:1.8, 4:2.3, 5:2.8, 6:3.4, 7:4.0}
        return errors.get(min(days_ahead, 7), 4.0)

    def confidence_multiplier(self, days_ahead: int, model_agreement=1.0) -> float:
        """Combined confidence 0.3-1.0. Used to scale Kelly fraction."""
        day_conf = max(0.3, 1.0 - days_ahead * 0.10)
        agreement_conf = 0.5 + 0.5 * model_agreement
        return round(day_conf * agreement_conf, 3)

_calibrator = None
def get_model_calibrator() -> ModelCalibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = ModelCalibrator()
    return _calibrator