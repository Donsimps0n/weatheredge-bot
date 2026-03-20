"""
market_scanner.py - Scans ALL Polymarket weather markets across multiple days.
Inspired by ColdMath (83.7% win rate) and automatedAItradingbot strategies:
- Scans YES and NO on every bin for every city for next 7 days
- Parallel forecast fetching for speed
- Min edge threshold, liquidity check, reward token filter
- Deduplication to avoid re-entering existing positions
"""
import logging, requests, re
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional
from config import cfg, CITY_COORDS
from probability_calculator import prob_for_bin
from edge_calculator import EdgeCalculator

log = logging.getLogger(__name__)
GAMMA = "https://gamma-api.polymarket.com"

@dataclass
class MarketOpportunity:
    condition_id: str
    question: str
    city: str
    target_date: date
    side: str          # YES or NO
    model_prob: float
    market_price: float
    edge: float
    kelly_f: float
    position_usd: float
    confidence: float  # model spread (lower = more confident)
    days_ahead: int

def fetch_weather_markets(days_ahead: int = 7) -> list:
    """Fetch all active weather markets for next N days."""
    try:
        resp = requests.get(
            f"{GAMMA}/events",
            params={"tag_slug":"weather","active":"true","closed":"false","limit":500},
            timeout=15
        )
        events = resp.json()
        markets = []
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)
        for ev in events:
            for m in ev.get("markets", []):
                # Parse date from question
                q = m.get("question", "")
                date_match = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d+)', q)
                if date_match:
                    try:
                        from datetime import datetime
                        mdate = datetime.strptime(
                            f"{date_match.group(0)} {today.year}", "%b %d %Y"
                        ).date()
                        if today < mdate <= cutoff:
                            m["_target_date"] = mdate
                            m["_days_ahead"] = (mdate - today).days
                            markets.append(m)
                    except: pass
        log.info(f"Found {len(markets)} future weather markets ({days_ahead}-day window)")
        return markets
    except Exception as e:
        log.error(f"Market fetch failed: {e}")
        return []

def parse_city(question: str) -> Optional[str]:
    """Extract city name from market question."""
    m = re.search(r'temperature in ([A-Z][\w\s]+?) (?:be|on)', question)
    if m:
        city = m.group(1).strip()
        # Normalise known variants
        aliases = {
            "NYC": "New York", "New York City": "New York",
            "Buenos Aires": "Buenos Aires", "Sao Paulo": "Sao Paulo",
            "Hong Kong": "Hong Kong", "Kuala Lumpur": "Kuala Lumpur"
        }
        return aliases.get(city, city)
    return None

class MarketScanner:
    def __init__(self):
        self.calc = EdgeCalculator(cfg.risk.paper_bankroll_usd)
        self.existing_positions: set = set()  # condition_ids already held

    def update_positions(self, open_positions: list):
        """Load currently held positions to avoid re-entering."""
        self.existing_positions = {p.get("condition_id","") for p in open_positions}

    def scan(self, forecasts: dict, days_ahead: int = 7) -> List[MarketOpportunity]:
        """Full scan: all cities, all bins, YES+NO, next N days."""
        markets = fetch_weather_markets(days_ahead)
        if not markets:
            return []

        opportunities = []
        processed = set()

        for m in markets:
            cid = m.get("conditionId", m.get("condition_id",""))
            if cid in self.existing_positions:
                continue  # already in this position

            q = m.get("question","")
            city = parse_city(q)
            if not city or city not in forecasts:
                continue

            # Dedup
            key = f"{cid}"
            if key in processed:
                continue
            processed.add(key)

            fc = forecasts.get(city)
            if not fc or not fc.get("samples"):
                continue

            samples = fc["samples"]
            target_date = m.get("_target_date")
            days_ahead_val = m.get("_days_ahead", 1)

            # Parse bin from question
            from fast_pipeline import parse_bin_range
            try:
                bin_range = parse_bin_range(q)
            except:
                continue
            if bin_range is None:
                continue

            # Model probability
            try:
                model_prob, confidence = prob_for_bin(samples, bin_range)
            except:
                continue

            # Market price
            prices = m.get("outcomePrices", "")
            try:
                if isinstance(prices, str):
                    prices = [float(x) for x in prices.strip("[]").split(",")]
                yes_price = float(prices[0]) if prices else 0.5
            except:
                yes_price = 0.5

            no_price = 1 - yes_price

            # Liquidity check
            vol = float(m.get("volume", 0))
            if vol < 500:
                continue  # skip illiquid markets

            # Reward token filter (skip reward-gated markets)
            rewards = m.get("rewards", {})
            if rewards.get("rewardsDailyRate", 0) > 0 and vol < 2000:
                pass  # still trade if liquid enough

            # Calculate YES edge
            yes_edge = model_prob - yes_price - 0.02
            no_edge_val = (1 - model_prob) - no_price - 0.02

            # Size via Kelly
            self.calc.update_bankroll(cfg.risk.paper_bankroll_usd)

            for side, edge_val, prob_val, price_val in [
                ("YES", yes_edge, model_prob, yes_price),
                ("NO",  no_edge_val, 1-model_prob, no_price)
            ]:
                if edge_val < cfg.risk.min_edge:
                    continue
                # Kelly fraction
                b = (1 - price_val) / price_val
                kelly = max(0, (edge_val * (b + 1) - (1 - prob_val)) / b)
                kelly_sized = kelly * cfg.risk.kelly_fraction
                position_usd = min(
                    kelly_sized * cfg.risk.paper_bankroll_usd,
                    cfg.risk.max_order_usd,
                    cfg.risk.paper_bankroll_usd * cfg.risk.max_bankroll_pct
                )
                if position_usd < 1.0:
                    continue

                opportunities.append(MarketOpportunity(
                    condition_id=cid,
                    question=q,
                    city=city,
                    target_date=target_date,
                    side=side,
                    model_prob=prob_val,
                    market_price=price_val,
                    edge=edge_val,
                    kelly_f=kelly_sized,
                    position_usd=round(position_usd, 2),
                    confidence=fc.get("std_c", fc.get("model_spread", 1.5)),
                    days_ahead=days_ahead_val
                ))

        # Sort by edge * confidence (high edge, low spread = best)
        opportunities.sort(key=lambda x: x.edge / max(x.confidence, 0.1), reverse=True)
        log.info(f"Found {len(opportunities)} opportunities across {days_ahead} days")
        return opportunities

# Backwards compatibility alias
WeatherMarket = MarketOpportunity
