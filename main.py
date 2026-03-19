"""
main.py
=======
Bot entrypoint. Runs two scheduled jobs:
  1. market_scan_job  — every SCAN_INTERVAL_SECONDS (default 10 min)
     Scans Polymarket for active weather markets, fetches forecasts,
     computes edge, executes qualifying trades.

  2. daily_report_job — every day at 07:00 UTC
     Sends a summary of yesterday's P&L, open positions, and bot status
     to Telegram/Discord.
"""
import logging
import signal
import sys
import time
from datetime import datetime, timezone

import click
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import cfg, TradingMode
from market_scanner import MarketScanner
from forecast_fetcher import ForecastFetcher
from probability_calculator import ProbabilityCalculator
from edge_calculator import EdgeCalculator
from trader import Trader

log = logging.getLogger(__name__)

@click.command()
@click.option("--mode", type=click.Choice(["paper", "live"], case_sensitive=False), default="paper")
@click.option("--once", is_flag=True, default=False)
def main(mode, once):
    trader = Trader()
    scanner = MarketScanner()
    forecaster = ForecastFetcher()
    calc = ProbabilityCalculator(use_kde=True)
    edge_calc = EdgeCalculator(bankroll=trader.bankroll)

    def run_scan():
        markets = scanner.get_active_weather_markets()
        log.info(f"Scan: {len(markets)} markets")
        for m in markets:
            if not m.is_tradeable(): continue
            fc = forecaster.get(city=m.city, target_date=m.target_date)
            if fc is None: continue
            p, u = calc.market_probability(m, fc)
            er = edge_calc.best_side(m, p, u)
            if er.is_tradeable:
                trader.execute(m, er)

    if once:
        run_scan()
        return

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_scan, IntervalTrigger(seconds=cfg.scheduler.scan_interval_seconds), max_instances=1)
    scheduler.start()

if __name__ == "__main__":
    main()
