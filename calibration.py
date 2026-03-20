"""
calibration.py - Model probability calibration using historical outcomes.

After enough trades resolve, fits isotonic regression to correct systematic
over/under-confidence in the GFS ensemble predictions.

Usage:
    from calibration import Calibrator
    cal = Calibrator()
    calibrated_prob = cal.calibrate(raw_prob, city=city)
"""
import logging, sqlite3, json
from pathlib import Path
from typing import Optional
import numpy as np
log = logging.getLogger(__name__)

DB_PATH = Path("data/trades.db")
CAL_PATH = Path("data/calibration.json")
MIN_SAMPLES = 30   # minimum resolved trades before calibration kicks in

class Calibrator:
    def __init__(self):
        self._bins = None
        self._city_bias = {}
        self._load()

    def _load(self):
        """Load calibration curves from disk."""
        if CAL_PATH.exists():
            try:
                data = json.loads(CAL_PATH.read_text())
                self._bins = data.get("bins")        # global isotonic bins
                self._city_bias = data.get("city_bias", {})
                log.info(f"Calibration loaded: {len(self._city_bias)} cities")
            except Exception as e:
                log.warning(f"Calibration load failed: {e}")

    def calibrate(self, raw_prob: float, city: str = "") -> float:
        """
        Apply calibration to a raw model probability.
        Falls back to raw_prob if not enough data.
        """
        p = raw_prob

        # Step 1: Global isotonic correction
        if self._bins:
            p = self._isotonic_correct(p, self._bins)

        # Step 2: City-specific bias correction
        if city and city in self._city_bias:
            bias = self._city_bias[city]
            p = max(0.01, min(0.99, p - bias))
            log.debug(f"City bias for {city}: {bias:+.3f}")

        return round(p, 4)

    def _isotonic_correct(self, p: float, bins: list) -> float:
        """Piecewise linear interpolation through isotonic calibration bins."""
        if len(bins) < 2:
            return p
        xs = [b[0] for b in bins]
        ys = [b[1] for b in bins]
        return float(np.interp(p, xs, ys))

    def fit(self):
        """
        Refit calibration curves from resolved trades in SQLite.
        Call this periodically (e.g. after every 10 new resolutions).
        """
        if not DB_PATH.exists():
            log.info("No DB yet, skipping calibration fit")
            return

        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT model_prob, outcome, city FROM trades "
                "WHERE outcome IN ('WIN','LOSS') ORDER BY resolved_at"
            ).fetchall()

        if len(rows) < MIN_SAMPLES:
            log.info(f"Not enough data for calibration: {len(rows)}/{MIN_SAMPLES}")
            return

        probs = np.array([r[0] for r in rows])
        outcomes = np.array([1.0 if r[1]=='WIN' else 0.0 for r in rows])
        cities = [r[2] for r in rows]

        # --- Global isotonic regression ---
        try:
            from sklearn.isotonic import IsotonicRegression
            ir = IsotonicRegression(out_of_bounds='clip')
            ir.fit(probs, outcomes)
            # Sample 20 bins for storage
            xs = np.linspace(0.02, 0.98, 20)
            ys = ir.predict(xs)
            bins = [[round(float(x),3), round(float(y),3)] for x,y in zip(xs,ys)]
        except ImportError:
            # Fallback: simple binning without sklearn
            bins = self._simple_bins(probs, outcomes)

        # --- Per-city bias ---
        city_bias = {}
        from collections import defaultdict
        city_data = defaultdict(list)
        for p, o, c in zip(probs, outcomes, cities):
            if c: city_data[c].append((p, o))

        for city, data in city_data.items():
            if len(data) < 10:
                continue
            cp = np.array([d[0] for d in data])
            co = np.array([d[1] for d in data])
            # Bias = mean(model_prob) - mean(actual_outcome)
            bias = float(np.mean(cp) - np.mean(co))
            if abs(bias) > 0.02:  # only store meaningful bias
                city_bias[city] = round(bias, 4)

        # Save
        CAL_PATH.parent.mkdir(exist_ok=True)
        CAL_PATH.write_text(json.dumps({
            "bins": bins,
            "city_bias": city_bias,
            "n_samples": len(rows),
            "fitted_at": __import__('datetime').datetime.utcnow().isoformat()
        }, indent=2))
        self._bins = bins
        self._city_bias = city_bias
        log.info(f"Calibration fitted: {len(rows)} samples, {len(city_bias)} city biases")

    def _simple_bins(self, probs, outcomes, n_bins=10):
        """Fallback binning without sklearn."""
        bins = []
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        for lo, hi in zip(edges[:-1], edges[1:]):
            mask = (probs >= lo) & (probs < hi)
            if mask.sum() > 0:
                x = float(np.mean(probs[mask]))
                y = float(np.mean(outcomes[mask]))
                bins.append([round(x,3), round(y,3)])
        return bins

    def summary(self) -> dict:
        return {
            "has_calibration": self._bins is not None,
            "n_city_biases": len(self._city_bias),
            "top_biases": sorted(
                self._city_bias.items(), key=lambda x: abs(x[1]), reverse=True
            )[:10]
        }

# Global singleton
_calibrator = None
def get_calibrator() -> Calibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = Calibrator()
    return _calibrator
