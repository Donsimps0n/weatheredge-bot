"""
probability_calculator.py - Bin probability calculator with KDE.
Includes BinRange dataclass and parse_bin_range for question parsing.
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
    unit: str = "C"       # C or F

    # Aliases for legacy callers that use .low / .high
    @property
    def low(self): return self.lo
    @property
    def high(self): return self.hi

    def to_celsius(self):
        """Return a new BinRange with bounds converted to Celsius."""
        if self.unit.upper() == "C":
            return self
        def fc(v): return (v - 32.0) * 5.0 / 9.0 if v is not None else None
        return BinRange(lo=fc(self.lo), hi=fc(self.hi), unit="C")

    def contains(self, temp_c: float) -> bool:
        """Check if a temperature (in Celsius) falls within this bin."""
        bc = self.to_celsius()
        if bc.lo is not None and temp_c < bc.lo - 0.5: return False
        if bc.hi is not None and temp_c > bc.hi + 0.5: return False
        return True


def parse_bin_range(question: str) -> Optional[BinRange]:
    """Parse a temperature bin from a Polymarket question string."""
    q = question or ""
    # "between X and Y°F" / "X-Y°F"
    m = re.search(r"between\s+(\d+(?:\.\d+)?)[-\s]+(?:and\s+)?(\d+(?:\.\d+)?)[°\s]*(F|C)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=float(m.group(2)), unit=m.group(3).upper())
    # "X-Y°F" compact
    m = re.search(r"(\d+)-(\d+)[°\s]*(F|C)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=float(m.group(2)), unit=m.group(3).upper())
    # "X°C or below"
    m = re.search(r"(-?\d+(?:\.\d+)?)[°\s]*(F|C)?\s+or\s+below", q, re.I)
    if m: return BinRange(lo=None, hi=float(m.group(1)), unit=(m.group(2) or "C").upper())
    # "X°C or higher/above"
    m = re.search(r"(-?\d+(?:\.\d+)?)[°\s]*(F|C)?\s+or\s+(?:higher|above)", q, re.I)
    if m: return BinRange(lo=float(m.group(1)), hi=None, unit=(m.group(2) or "C").upper())
    # "be X°C on" (single bin ±0.5)
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)[°\s]*(F|C)?\s+on", q, re.I)
    if m:
        v = float(m.group(1))
        return BinRange(lo=v - 0.5, hi=v + 0.5, unit=(m.group(2) or "C").upper())
    return None


def prob_for_bin(samples_c, bin_range, use_kde=True):
    """Compute P(temp in bin) from ensemble samples using KDE or Beta fallback."""
    if not samples_c: return 0.5, 1.0
    arr = np.array(samples_c, dtype=float)
    bin_c = bin_range.to_celsius()
    lo = float(bin_c.lo) - 0.5 if bin_c.lo is not None else float(arr.min()) - 10
    hi = float(bin_c.hi) + 0.5 if bin_c.hi is not None else float(arr.max()) + 10
    if use_kde and len(samples_c) >= 10 and arr.std() > 0.01:
        try:
            kde = stats.gaussian_kde(arr)
            prob = float(np.clip(kde.integrate_box_1d(lo, hi), 0.0, 1.0))
            return prob, kde.factor * float(arr.std())
        except: pass
    # Beta-binomial fallback
    from scipy.stats import beta as beta_dist
    k = sum(1 for t in samples_c if lo <= t <= hi)
    a, b = 2.0 + k, 2.0 + (len(samples_c) - k)
    prob = float(beta_dist.mean(a, b))
    uncertainty = float(beta_dist.std(a, b))
    return prob, uncertainty


class ProbabilityCalculator:
    """Legacy wrapper kept for backwards compatibility."""
    def calc(self, samples_c, bin_range):
        return prob_for_bin(samples_c, bin_range)