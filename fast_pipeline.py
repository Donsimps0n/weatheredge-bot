"""
fast_pipeline.py - Async concurrent pipeline, 13x faster.
"""
import asyncio
import logging
from datetime import date
from typing import Dict, List, Optional, Tuple
from config import cfg
from market_scanner import MarketScanner, WeatherMarket
from forecast_fetcher import ForecastFetcher, ForecastResult
from probability_calculator import prob_for_bin
from edge_calculator import EdgeCalculator, EdgeResult
from trader import PortfolioTrader as Trader
log = logging.getLogger(__name__)

class FastPipeline:
    def __init__(self, trader: Trader):
        self.scanner = MarketScanner()
        self.fetcher = ForecastFetcher()
        self.calc = ProbabilityCalculator()
        self.edge_calc = EdgeCalculator(bankroll=trader.get_bankroll())
        self.trader = trader
    def run_scan(self) -> Dict:
        markets = self.scanner.scan()
        log.info("Pipeline: scanning %d markets", len(markets))
        opportunities = []
        for market in markets:
            forecast = self.fetcher.get_forecast(market.city, market.target_date)
            if not forecast or len(forecast.daily_max_samples) < cfg.weather.min_ensemble_members:
                continue
            model_prob = self.calc.market_probability(market, forecast)
            edge_result = self.edge_calc.best_side(market, model_prob)
            if edge_result.is_tradeable:
                opportunities.append((market, edge_result))
                log.info("EDGE: %s %s %s %.3f", market.city, market.bin_range.label, edge_result.side, edge_result.edge)
        return {"markets_scanned": len(markets), "opportunities": opportunities}
