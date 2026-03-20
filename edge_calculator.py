"""
edge_calculator.py - Edge calculation for YES and NO sides.
Improvements:
  1. Day-of weighting: larger Kelly when days_ahead <= 1 (model most accurate)
  2. Boosted NO Kelly: when NO edge >30% and price >50c, use 1.5x sizing
  3. Tighter min edge: 8% default (was 5%) — ColdMath-style high-conviction only
  4. Spread-aware: shrink edge estimate when model uncertainty (spread) is high
"""
import logging
from dataclasses import dataclass, field
from typing import Tuple, Optional
log = logging.getLogger(__name__)

@dataclass
class EdgeResult:
    side: str
    model_prob: float
    market_price: float
    edge: float
    ev_net: float
    kelly_f: float
    position_usd: float
    is_tradeable: bool
    note: str = ""

class EdgeCalculator:
    TAKER_FEE = 0.02   # 2% Polymarket taker fee
    MIN_EDGE  = 0.08   # 8% minimum edge — higher bar = fewer but better trades
    MIN_PRICE = 0.02   # ignore sub-2c markets
    MAX_PRICE = 0.98   # ignore near-certain markets
    KELLY_FRAC = 0.25  # quarter-Kelly base

    def __init__(self, bankroll: float = 1000.0):
        self.bankroll = bankroll

    def update_bankroll(self, bankroll: float):
        self.bankroll = bankroll

    def _kelly(self, p: float, price: float) -> float:
        """Full Kelly fraction for binary market."""
        if price <= 0 or price >= 1: return 0.0
        b = (1.0 - price) / price   # net odds
        f = (b * p - (1.0 - p)) / b
        return max(0.0, f)

    def calculate(
        self,
        model_prob: float,
        yes_price: float,
        no_price: float,
        spread: float = 0.0,
        days_ahead: int = 3,
    ) -> Tuple[EdgeResult, EdgeResult]:
        """
        Returns (yes_result, no_result).
        spread: model uncertainty in degrees C (higher = less confident)
        days_ahead: 0-1 = most accurate (day-of), 5-7 = least accurate
        """
        # --- Spread adjustment: shrink probability toward 0.5 by uncertainty ---
        # Each degree of spread shrinks by 5% toward 50/50
        shrink = min(spread * 0.05, 0.40)
        adj_prob = model_prob + shrink * (0.5 - model_prob)

        # --- Day-of Kelly multiplier ---
        # Day 0-1: model blends NWS obs, most accurate → 1.5x Kelly
        # Day 2-3: standard → 1.0x Kelly
        # Day 4-7: forecast only, less reliable → 0.7x Kelly
        if days_ahead <= 1:
            day_mult = 1.5
        elif days_ahead <= 3:
            day_mult = 1.0
        else:
            day_mult = 0.7

        fee = self.TAKER_FEE

        # --- YES side ---
        yes_edge = adj_prob - yes_price - fee * yes_price
        yes_kelly = self._kelly(adj_prob, yes_price) * self.KELLY_FRAC * day_mult
        yes_usd = min(yes_kelly * self.bankroll, self.bankroll * 0.10)
        yes_ev = adj_prob * (1.0 - yes_price) - (1.0 - adj_prob) * yes_price - fee * yes_price
        yes_tradeable = (
            yes_edge >= self.MIN_EDGE
            and self.MIN_PRICE <= yes_price <= self.MAX_PRICE
            and yes_usd >= 0.50
        )

        # --- NO side ---
        no_prob = 1.0 - adj_prob
        no_edge = no_prob - no_price - fee * no_price
        # Boost NO Kelly when high-confidence cheap NO (near-certain event won't happen)
        no_boost = 1.5 if (no_edge >= 0.30 and no_price >= 0.50) else 1.0
        no_kelly = self._kelly(no_prob, no_price) * self.KELLY_FRAC * day_mult * no_boost
        no_usd = min(no_kelly * self.bankroll, self.bankroll * 0.10)
        no_ev = no_prob * (1.0 - no_price) - (1.0 - no_prob) * no_price - fee * no_price
        no_tradeable = (
            no_edge >= self.MIN_EDGE
            and self.MIN_PRICE <= no_price <= self.MAX_PRICE
            and no_usd >= 0.50
        )

        yes_result = EdgeResult(
            side="YES", model_prob=adj_prob, market_price=yes_price,
            edge=round(yes_edge, 4), ev_net=round(yes_ev, 4),
            kelly_f=round(yes_kelly, 4), position_usd=round(yes_usd, 2),
            is_tradeable=yes_tradeable,
            note=f"days={days_ahead} spread={spread:.1f} day_mult={day_mult}"
        )
        no_result = EdgeResult(
            side="NO", model_prob=no_prob, market_price=no_price,
            edge=round(no_edge, 4), ev_net=round(no_ev, 4),
            kelly_f=round(no_kelly, 4), position_usd=round(no_usd, 2),
            is_tradeable=no_tradeable,
            note=f"days={days_ahead} no_boost={no_boost} day_mult={day_mult}"
        )
        return yes_result, no_result

    def best_side(
        self,
        model_prob: float,
        yes_price: float,
        no_price: float,
        spread: float = 0.0,
        days_ahead: int = 3,
    ) -> Optional[EdgeResult]:
        """Return the better tradeable side, or None if neither qualifies."""
        yes, no = self.calculate(model_prob, yes_price, no_price, spread, days_ahead)
        candidates = [r for r in [yes, no] if r.is_tradeable]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.edge)
