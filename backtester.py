"""
backtester.py
=============
Replays the bot's strategy over historical Polymarket weather markets.
"""
import json, logging, time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import requests
from config import cfg, get_city_coords
log = logging.getLogger(__name__)
GAMMA_BASE = cfg.polymarket.gamma_host

@dataclass
class BacktestTrade:
    condition_id: str; city: str; target_date: str; bin_label: str
    side: str; model_prob: float; market_price: float; edge: float
    ev_net: float; simulated_size: float; fill_price: float
    outcome: Optional[bool] = None; pnl_usd: Optional[float] = None; notes: str = ""

@dataclass
class BacktestReport:
    days: int; start_date: str; end_date: str; n_markets_found: int
    n_parseable: int; n_forecast_ok: int; n_tradeable: int; n_simulated: int
    total_wagered: float; total_pnl: float; roi: float; win_rate: float
    avg_edge: float; avg_ev_net: float; sharpe: float; max_drawdown: float
    calibration: Dict; trades: List[BacktestTrade]

class Backtester:
    def __init__(self, days=60, city_filter=None, min_edge=None, bankroll=1000.0):
        self.days = days; self.city_filter = city_filter
        self.min_edge = min_edge or cfg.risk.min_edge; self.bankroll = bankroll
        self.session = requests.Session()
    def run(self):
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=self.days)
        log.info("Backtest: %s -> %s", start_date, end_date)
        return BacktestReport(days=self.days, start_date=str(start_date),
            end_date=str(end_date), n_markets_found=0, n_parseable=0,
            n_forecast_ok=0, n_tradeable=0, n_simulated=0,
            total_wagered=0.0, total_pnl=0.0, roi=0.0, win_rate=0.0,
            avg_edge=0.0, avg_ev_net=0.0, sharpe=0.0, max_drawdown=0.0,
            calibration={}, trades=[])
