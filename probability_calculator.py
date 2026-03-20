"""
probability_calculator.py - Bin probability calculator with KDE.
Fix: removed ±0.5 padding on bin boundaries — Polymarket bins are exact ranges.
Padding was inflating probability estimates and creating false edges.
"""
import re, logging
from dataclasses import dataclass
from typing import List, Tuple, Optional
import numpy as np
from scipy import stats
log = logging.getLogger(__name__)

@dataclass
class BinRange:
    lo: Optional[float]   # lower bound (None = -inf)
    hi: Optional[float]   # upper bound (None = +inf)
    unit: str = "C"

    @property
    def low(self): return self.lo
    @property
    def high(self): return self.hi

    def to_celsius(self):
        if self.unit.upper() == "C":
            return self
        def fc(v): return (v - 32.0) * 5.0 / 9.0 if v is not None else None
        return BinRange(lo=fc(self.lo), hi=fc(self.hi), unit="C")

    def contains(self, temp_c: float) -> bool:
        bc = self.to_celsius()
        if bc.lo is not None and temp_c < bc.lo: return False
        if bc.hi is not None and temp_c >= bc.hi: return False
        return True


def parse_bin_range(q: str) -> Optional[BinRange]:
    """Parse temperature bin from Polymarket question text."""
    # "between X and Y°F/C"
    m = re.search(r"betweens+(-?d+(?:.d+)?)s+ands+(-?d+(?:.d+)?)s*[°s]*(F|C)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=float(m.group(2)), unit=m.group(3).upper())
    # "X to Y°F/C"
    m = re.search(r"(-?d+(?:.d+)?)s+tos+(-?d+(?:.d+)?)s*[°s]*(F|C)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=float(m.group(2)), unit=m.group(3).upper())
    # "X-Y°F/C" with degree symbol variants
    m = re.search(r"(-?d+(?:.d+)?)[-–](d+(?:.d+)?)[°°s]*(F|C)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=float(m.group(2)), unit=m.group(3).upper())
    # "X or below °C/F"
    m = re.search(r"(-?d+(?:.d+)?)s*[°°]?s*(F|C)?s+ors+below", q, re.I)
    if m: return BinRange(lo=None, hi=float(m.group(1)), unit=(m.group(2) or "C").upper())
    # "X or higher/above °C/F"
    m = re.search(r"(-?d+(?:.d+)?)s*[°°]?s*(F|C)?s+ors+(?:higher|above)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=None, unit=(m.group(2) or "C").upper())
    # "be X°C on" (exact bin ±0.5 is correct ONLY for single-value exact bins)
    m = re.search(r"bes+(-?d+(?:.d+)?)s*[°°]?s*(F|C)?s+on", q, re.I)
    if m:
        v = float(m.group(1))
        return BinRange(lo=v, hi=v + 1.0, unit=(m.group(2) or "C").upper())
    return None


def prob_for_bin(samples_c, bin_range, use_kde=True) -> Tuple[float, float]:
    """
    Compute P(temp in bin) from ensemble samples using KDE or Beta fallback.
    NO boundary padding — bin edges are exact per Polymarket rules.
    Returns (probability, uncertainty).
    """
    if not samples_c:
        return 0.5, 1.0
    arr = np.array(samples_c, dtype=float)
    bin_c = bin_range.to_celsius()

    # Exact bin boundaries — no padding
    lo = float(bin_c.lo) if bin_c.lo is not None else float(arr.min()) - 20
    hi = float(bin_c.hi) if bin_c.hi is not None else float(arr.max()) + 20

    if lo >= hi:
        return 0.0, 1.0

    if use_kde and len(samples_c) >= 10 and arr.std() > 0.01:
        try:
            kde = stats.gaussian_kde(arr)
            prob = float(np.clip(kde.integrate_box_1d(lo, hi), 0.0, 1.0))
            # Uncertainty = KDE bandwidth * std (proxy for model spread)
            uncertainty = kde.factor * float(arr.std())
            return prob, uncertainty
        except:
            pass

    # Beta-binomial fallback
    from scipy.stats import beta as beta_dist
    k = sum(1 for t in samples_c if lo <= t < hi)
    a, b_param = 1.0 + k, 1.0 + (len(samples_c) - k)  # uniform prior (was 2,2)
    prob = float(beta_dist.mean(a, b_param))
    uncertainty = float(beta_dist.std(a, b_param))
    return prob, uncertainty


class ProbabilityCalculator:
    """Wrapper for backwards compatibility."""
    def prob_for_bin(self, samples, bin_range):
        return prob_for_bin(samples, bin_range)
