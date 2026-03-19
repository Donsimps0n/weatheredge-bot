"""
edge_calculator.py - Computes edge, Kelly sizing, and EV.
"""
import logging
from dataclasses import dataclass
from typing import Optional
import numpy as np
from config import cfg
log = logging.getLogger(__name__)

@dataclass
class EdgeResult:
    side: str; model_prob: float; market_price: float; edge: float
    ev_net: float; kelly_f: float; position_usd: float
    is_tradeable: bool; note: str = ""

class EdgeCalculator:
    def __init__(self, bankroll=1000.0):
        self.bankroll = bankroll
    def update_bankroll(self, bankroll): self.bankroll = bankroll
    def best_side(self, market, model_prob_result):
        if isinstance(model_prob_result, tuple):
            p, u = model_prob_result
        else:
            p, u = model_prob_result, 0.1
        fee = 0.02
        # Try YES side
        P = market.mid_price
        edge_yes = p - P - fee
        # Try NO side
        p_no = 1 - p
        P_no = 1 - P
        edge_no = p_no - P_no - fee
        # Pick best
        if edge_yes >= edge_no and edge_yes > 0:
            side, edge, prob, price = "YES", edge_yes, p, P
        elif edge_no > edge_yes and edge_no > 0:
            side, edge, prob, price = "NO", edge_no, p_no, P_no
        else:
            return EdgeResult(side="YES", model_prob=p, market_price=P, edge=edge_yes,
                ev_net=0, kelly_f=0, position_usd=0, is_tradeable=False, note="no edge")
        # Kelly
        b = (1 - price) / price
        q = 1 - prob
        kelly = max((b * prob - q) / b, 0)
        f = min(kelly * cfg.risk.kelly_fraction, cfg.risk.max_bankroll_pct)
        pos = min(self.bankroll * f, cfg.risk.max_order_usd)
        ev_net = prob * (1 - price) - q * price - fee
        return EdgeResult(side=side, model_prob=prob, market_price=price, edge=edge,
            ev_net=ev_net, kelly_f=f, position_usd=pos,
            is_tradeable=(pos >= 1.0 and edge >= cfg.risk.min_edge),
            note=f"p={prob:.3f} P={price:.3f} edge={edge:+:.3f}")
