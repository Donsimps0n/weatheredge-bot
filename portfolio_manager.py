"""
portfolio_manager.py
====================
Manages the portfolio of open positions as a whole.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
from config import cfg
log = logging.getLogger(__name__)

@dataclass
class OpenPosition:
    position_id: str; condition_id: str; city: str; target_date: date
    bin_label: str; side: str; entry_price: float; current_price: float
    shares: float; cost_usd: float; opened_at: datetime
    model_prob_at_entry: float; current_model_prob: float = 0.0; token_id: str = ""
    @property
    def unrealised_pnl(self): return self.shares * (self.current_price - self.entry_price)
    @property
    def pnl_pct(self): return (self.current_price - self.entry_price) / self.entry_price if self.entry_price else 0.0
    @property
    def hours_held(self): return (datetime.now(timezone.utc) - self.opened_at).total_seconds() / 3600

@dataclass
class ExitDecision:
    position: OpenPosition; should_exit: bool; reason: str; urgency: str; expected_exit_price: float

class PortfolioManager:
    def __init__(self, bankroll: float): self.bankroll = bankroll
    def correlation_adjusted_kelly(self, new_city, new_date, raw_kelly, open_positions): return raw_kelly
    def evaluate_exit(self, position):
        return ExitDecision(position=position, should_exit=False, reason="holding", urgency="MONITOR", expected_exit_price=position.current_price)
    def evaluate_exits(self, positions): return [self.evaluate_exit(p) for p in positions]
    def portfolio_summary(self, positions): return f"{len(positions)} open positions"
