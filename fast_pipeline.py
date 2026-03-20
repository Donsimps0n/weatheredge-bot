"""
fast_pipeline.py - Scan pipeline that wires all components together.
"""
import logging
from datetime import date
from typing import Dict, List
from config import cfg
from market_scanner import MarketScanner, WeatherMarket
from forecast_fetcher import ForecastFetcher
from probability_calculator import prob_for_bin, parse_bin_range, ProbabilityCalculator
from edge_calculator import EdgeCalculator
from trader import PortfolioTrader as Trader
log = logging.getLogger(__name__)


class FastPipeline:
    def __init__(self, trader: Trader):
        self.scanner = MarketScanner()
        self.fetcher = ForecastFetcher()
        self.calc = ProbabilityCalculator()
        self.edge_calc = EdgeCalculator(bankroll=cfg.risk.paper_bankroll_usd)
        self.trader = trader

    def run_scan(self) -> Dict:
        """Run full scan: fetch forecasts, scan markets, return opportunities."""
        from config import CITY_COORDS
        cities = list(CITY_COORDS.keys())

        # Fetch forecasts for all cities
        forecasts = {}
        for city in cities:
            try:
                fc = self.fetcher.fetch(city, date.today())
                if fc and fc.samples:
                    forecasts[city] = {"samples": fc.samples, "std_c": fc.std_c}
            except Exception as e:
                log.debug(f"Forecast failed for {city}: {e}")

        # Scan markets using all forecast data
        open_positions = self.trader.get_open_positions()
        self.scanner.update_positions(open_positions)
        opportunities = self.scanner.scan(forecasts)

        return {
            "opportunities": [
                {
                    "condition_id": o.condition_id,
                    "question": o.question,
                    "city": o.city,
                    "side": o.side,
                    "model_prob": round(o.model_prob, 4),
                    "market_price": round(o.market_price, 4),
                    "edge": round(o.edge, 4),
                    "kelly_f": round(o.kelly_f, 4),
                    "position_usd": o.position_usd,
                    "days_ahead": o.days_ahead,
                    "confidence": round(o.confidence, 2),
                }
                for o in opportunities
            ],
            "cities_fetched": len(forecasts),
            "total_opportunities": len(opportunities),
        }
