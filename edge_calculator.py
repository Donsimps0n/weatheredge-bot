"""
edge_calculator.py - Edge calculation for YES and NO sides.
Uses model probability vs market price with Kelly sizing.
"""
import logging
from dataclasses import dataclass
from typing import Tuple
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
    MIN_EDGE  = 0.05   # 5% minimum edge to trade
    MIN_PRICE = 0.02   # ignore sub-2c markets
    MAX_PRICE = 0.98   # ignore near-certain markets

    def __init__(self, bankroll: float = 1000.0):
        self.bankroll = bankroll

    def update_bankroll(self, bankroll: float):
        self.bankroll = bankroll

    def _kelly(self, p: float, price: float) -> float:
        """Full Kelly fraction for binary market."""
        if price <= 0 or price >= 1: return 0.0
        b = (1 - price) / price   # net odds on YES
        q = 1 - p
        return max(0.0, (b * p - q) / b)

    def calc_yes(self, model_prob: float, yes_price: float,
                 spread: float = 0.0) -> EdgeResult:
        """Calculate edge buying YES."""
        if yes_price < self.MIN_PRICE or yes_price > self.MAX_PRICE:
            return EdgeResult("YES", model_prob, yes_price, 0, 0, 0, 0, False, "price_out_of_range")
        # Widen probability for uncertainty: shrink toward 0.5 by spread
        adj_prob = model_prob + (0.5 - model_prob) * min(spread / 10.0, 0.3)
        edge = adj_prob - yes_price - self.TAKER_FEE
        if edge < self.MIN_EDGE:
            return EdgeResult("YES", adj_prob, yes_price, edge, 0, 0, 0, False, "insufficient_edge")
        ev = edge * (1 - yes_price) - (1 - adj_prob) * yes_price
        kelly = self._kelly(adj_prob, yes_price)
        return EdgeResult("YES", adj_prob, yes_price, edge, ev, kelly, 0, True)

    def calc_no(self, model_prob: float, yes_price: float,
                spread: float = 0.0) -> EdgeResult:
        """Calculate edge buying NO (= buying 1 - yes_price)."""
        no_price = 1.0 - yes_price
        no_prob = 1.0 - model_prob
        if no_price < self.MIN_PRICE or no_price > self.MAX_PRICE:
            return EdgeResult("NO", no_prob, no_price, 0, 0, 0, 0, False, "price_out_of_range")
        adj_prob = no_prob + (0.5 - no_prob) * min(spread / 10.0, 0.3)
        edge = adj_prob - no_price - self.TAKER_FEE
        if edge < self.MIN_EDGE:
            return EdgeResult("NO", adj_prob, no_price, edge, 0, 0, 0, False, "insufficient_edge")
        ev = edge * (1 - no_price) - (1 - adj_prob) * no_price
        kelly = self._kelly(adj_prob, no_price)
        return EdgeResult("NO", adj_prob, no_price, edge, ev, kelly, 0, True)

    def best_side(self, yes_price: float, model_prob: float,
                  spread: float = 0.0, bankroll: float = None) -> EdgeResult:
        """Return the better of YES or NO, sized by Kelly."""
        if bankroll: self.bankroll = bankroll
        yes_r = self.calc_yes(model_prob, yes_price, spread)
        no_r  = self.calc_no(model_prob, yes_price, spread)
        # Pick higher edge
        if yes_r.is_tradeable and no_r.is_tradeable:
            best = yes_r if yes_r.edge >= no_r.edge else no_r
        elif yes_r.is_tradeable:
            best = yes_r
        elif no_r.is_tradeable:
            best = no_r
        else:
            # Return the one with highest edge for logging
            best = yes_r if yes_r.edge >= no_r.edge else no_r
            return best
        # Size position
        from config import cfg
        kelly_fraction = cfg.risk.kelly_fraction
        sized_kelly = best.kelly_f * kelly_fraction
        position = min(
            sized_kelly * self.bankroll,
            cfg.risk.max_order_usd,
            self.bankroll * cfg.risk.max_bankroll_pct
        )
        best.position_usd = round(max(1.0, position), 2)
        return best