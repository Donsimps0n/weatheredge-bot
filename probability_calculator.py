"""
probability_calculator.py - Converts GFS ensemble samples into bin probabilities.
"""
import logging
from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
from scipy import stats
log = logging.getLogger(__name__)

def prob_for_bin(samples_c, bin_range, use_kde=True):
    if not samples_c: return 0.5, 1.0
    arr = np.array(samples_c, dtype=float)
    bin_c = bin_range.to_celsius()
    lo = float(bin_c.low) if bin_c.low is not None else float(arr.min()) - 10
    hi = float(bin_c.high) if bin_c.high is not None else float(arr.max()) + 10
    if use_kde and len(samples_c) >= 10 and arr.std() > 0.01:
        try:
            kde = stats.gaussian_kde(arr)
            prob = float(np.clip(kde.integrate_box_1d(lo, hi), 0.0, 1.0))
            return prob, kde.factor * float(arr.std())
        except: pass
    from scipy.stats import beta as beta_dist
    k = sum(1 for t in samples_c if bin_c.contains(t))
    a, b = 2.0 + k, 2.0 + (len(samples_c) - k)
    d = beta_dist(a, b)
    return float(d.mean()), float(d.std())

class ProbabilityCalculator:
    def __init__(self, use_kde=True): self.use_kde = use_kde
    def market_probability(self, market, forecast):
        p, u = prob_for_bin(forecast.daily_max_samples, market.bin_range, self.use_kde)
        return p, u
