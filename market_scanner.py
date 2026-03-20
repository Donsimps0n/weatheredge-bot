"""
market_scanner.py - Scans ALL Polymarket weather markets across multiple days.
Strategy based on top traders (ColdMath 83.7% WR, Hans323, aenews2):
- Uses EdgeCalculator with day-of Kelly boost + NO side boost
- Passes spread/confidence into edge calc for proper uncertainty discounting
- Min $1000 volume filter (removes garbage low-liquidity markets)
- Sorts by pure edge (spread already baked into edge via EdgeCalculator)
- Skips markets closing in <2 hours (can't move price in time)
"""
import logging, requests, re
from datetime import date, timedelta, datetime, timezone
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
    side: str
    model_prob: float
    market_price: float
    edge: float
    kelly_f: float
    position_usd: float
    confidence: float
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
                q = m.get("question", "")
                date_match = re.search(
                    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d+)', q
                )
                if date_match:
                    try:
                        mdate = datetime.strptime(
                            f"{date_match.group(1)[:3]} {date_match.group(2)} {today.year}",
                            "%b %d %Y"
                        ).date()
                        # Handle year rollover
                        if mdate < today - timedelta(days=30):
                            mdate = mdate.replace(year=today.year + 1)
                        if today <= mdate <= cutoff:
                            m["_target_date"] = mdate
                            m["_days_ahead"] = (mdate - today).days
                            markets.append(m)
                    except:
                        pass
        return markets
    except Exception as e:
        log.error(f"Market fetch failed: {e}")
        return []

def parse_city(question: str) -> Optional[str]:
    """Extract city name from market question."""
    m = re.search(r'temperature in ([A-Z][\w\s]+?) (?:be|on)', question)
    if m:
        city = m.group(1).strip()
        aliases = {
            "NYC": "New York", "New York City": "New York",
            "Buenos Aires": "Buenos Aires", "Sao Paulo": "Sao Paulo",
            "Hong Kong": "Hong Kong", "Kuala Lumpur": "Kuala Lumpur",
            "Ho Chi Minh City": "Ho Chi Minh City"
        }
        return aliases.get(city, city)
    return None

class MarketScanner:
    MIN_VOLUME_USD = 1000   # ignore markets with <$1k volume (was $500)
    MIN_HOURS_TO_CLOSE = 2  # skip markets closing in <2 hours

    def __init__(self):
        self.calc = EdgeCalculator(cfg.risk.paper_bankroll_usd)
        self.existing_positions: set = set()

    def update_positions(self, open_positions: list):
        self.existing_positions = {p.get("condition_id","") for p in open_positions}

    def _is_closing_soon(self, market: dict) -> bool:
        """True if market closes within MIN_HOURS_TO_CLOSE hours."""
        end_date = market.get("endDate") or market.get("end_date_iso")
        if not end_date:
            return False
        try:
            closes = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            hours_left = (closes - datetime.now(timezone.utc)).total_seconds() / 3600
            return hours_left < self.MIN_HOURS_TO_CLOSE
        except:
            return False

    def scan(self, forecasts: dict, markets: Optional[list] = None) -> List[MarketOpportunity]:
        """
        Main scan loop.
        forecasts: {city: {"samples": [...], "std_c": float}}
        markets: pre-fetched markets list (or None to fetch fresh)
        """
        if markets is None:
            markets = fetch_weather_markets()

        self.calc.update_bankroll(cfg.risk.paper_bankroll_usd)
        opportunities = []
        processed = set()

        for m in markets:
            cid = m.get("conditionId") or m.get("condition_id","")
            if not cid or cid in self.existing_positions:
                continue

            q = m.get("question","")
            city = parse_city(q)
            if not city or city not in forecasts:
                continue

            key = cid
            if key in processed:
                continue
            processed.add(key)

            # Skip closing-soon markets
            if self._is_closing_soon(m):
                continue

            # Liquidity filter — higher bar means better markets
            vol = float(m.get("volume", m.get("volumeNum", 0)) or 0)
            if vol < self.MIN_VOLUME_USD:
                continue

            fc = forecasts.get(city)
            if not fc or not fc.get("samples"):
                continue

            samples = fc["samples"]
            target_date = m.get("_target_date")
            days_ahead_val = m.get("_days_ahead", 1)
            spread = float(fc.get("std_c", fc.get("model_spread", 1.5)))

            # Parse bin range
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

            # Get prices
            outcomes = m.get("outcomes", [])
            outprices = m.get("outcomePrices", m.get("prices", []))
            if isinstance(outprices, str):
                import json
                try: outprices = json.loads(outprices)
                except: continue
            yes_price, no_price = 0.5, 0.5
            if len(outprices) >= 2:
                try:
                    yes_price = float(outprices[0])
                    no_price  = float(outprices[1])
                except:
                    continue

            # ── Use EdgeCalculator with all improvements ──
            best = self.calc.best_side(
                model_prob=model_prob,
                yes_price=yes_price,
                no_price=no_price,
                spread=spread,
                days_ahead=days_ahead_val
            )
            if best is None:
                continue

            opportunities.append(MarketOpportunity(
                condition_id=cid,
                question=q,
                city=city,
                target_date=target_date,
                side=best.side,
                model_prob=best.model_prob,
                market_price=best.market_price,
                edge=best.edge,
                kelly_f=best.kelly_f,
                position_usd=round(best.position_usd, 2),
                confidence=spread,
                days_ahead=days_ahead_val
            ))

        # Sort by edge descending — best opportunities first
        opportunities.sort(key=lambda x: x.edge, reverse=True)
        log.info(f"Found {len(opportunities)} opportunities from {len(markets)} markets")
        return opportunities

# Alias for backwards compatibility
WeatherMarket = MarketOpportunity
