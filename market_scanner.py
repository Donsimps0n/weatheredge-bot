"""
market_scanner.py - Discovers and parses Polymarket weather markets via /events endpoint.
"""
import logging, re, requests
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional
from config import cfg
log = logging.getLogger(__name__)

@dataclass
class BinRange:
    low: Optional[float]; high: Optional[float]; unit: str; label: str
    def to_celsius(self):
        if self.unit == "C": return self
        lo = (self.low - 32) * 5 / 9 if self.low is not None else None
        hi = (self.high - 32) * 5 / 9 if self.high is not None else None
        return BinRange(lo=lo, high=hi, unit="C", label=self.label)
    def contains(self, val):
        if self.low is not None and val < self.low: return False
        if self.high is not None and val >= self.high: return False
        return True

@dataclass
class WeatherMarket:
    condition_id: str; market_slug: str; question: str
    clob_token_ids: List[str]; tick_size: float; neg_risk: bool
    city: str; target_date: date; bin_range: BinRange; outcome_index: int
    mid_price: float; best_bid: float; best_ask: float
    volume_usd: float; liquidity_usd: float

class MarketParser:
    DATE_PATTERN = r'(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<d>\d+)(?:,\s*(?P<y>\d{4}))?'
    BIN_PATTERNS = [
        r'(?P<lo>\d+(?:\.\d+)?)\s*(?:-|\u2013)\s*(?P<hi>\d+(?:\.\d+)?)\s*\xb0\s*[FC]',
        r'(?:be\s+)?below\s+(?P<hi>\d+(?:\.\d+)?)\s*\xb0\s*[FC]',
        r'(?:be\s+)?above\s+(?P<lo>\d+(?:\.\d+)?)\s*\xb0\s*[FC]',
        r'(?P<exact>\d+(?:\.\d+)?)\s*\xb0\s*[FC]\s*(?:or\s+below|or\s+above)?',
    ]
    def is_temperature_market(self, q):
        q = q.lower()
        return any(x in q for x in ['temp', 'degree', '\u00b0', '\xb0'])
    def extract_city(self, q, cities):
        for c in sorted(cities, key=len, reverse=True):
            if c.lower() in q.lower(): return c
        return None
    def extract_date(self, q):
        import calendar
        m = re.search(self.DATE_PATTERN, q, re.IGNORECASE)
        if not m: return None
        try:
            year = int(m.group('y')) if m.group('y') else date.today().year
            return date(year, list(calendar.month_name).index(m.group('month').title()), int(m.group('d')))
        except: return None
    def extract_bin(self, q):
        unit = "C" if re.search(r'\xb0[Cc]|\u00b0[Cc]', q) else "F"
        for pat in self.BIN_PATTERNS:
            m = re.search(pat, q, re.IGNORECASE)
            if m:
                try:
                    gd = m.groupdict()
                    if 'exact' in gd and gd['exact']:
                        v = float(gd['exact'])
                        if 'or below' in q.lower(): return BinRange(lo=None, high=v+0.01, unit=unit, label=f"{v}\u00b0{unit} or below")
                        if 'or above' in q.lower(): return BinRange(lo=v, high=None, unit=unit, label=f"{v}\u00b0{unit} or above")
                        return BinRange(lo=v, high=v+1, unit=unit, label=f"{v}\u00b0{unit}")
                    lo = float(gd['lo']) if gd.get('lo') else None
                    hi = float(gd['hi']) if gd.get('hi') else None
                    label = f"{lo if lo else ''}-{hi if hi else ''}\u00b0{unit}"
                    return BinRange(lo=lo, high=hi, unit=unit, label=label)
                except: pass
        return None

class MarketScanner:
    def __init__(self):
        self.parser = MarketParser()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "polymarket-weather-bot/1.0"
    def scan(self) -> List[WeatherMarket]:
        markets = []
        try:
            resp = self.session.get(
                f"{cfg.polymarket.gamma_host}/events",
                params={"tag_slug": "weather", "active": "true", "closed": "false", "limit": 200, "order": "startDate", "ascending": "false"},
                timeout=30)
            resp.raise_for_status()
            events = resp.json()
            if not isinstance(events, list): events = events.get("events", [])
            for event in events:
                for raw in event.get("markets", []):
                    if not raw.get("active", False): continue
                    m = self._parse_market(raw)
                    if m: markets.append(m)
        except Exception as e: log.warning("Scan failed: %s", e)
        log.info("Found %d active weather markets from %d events", len(markets), len(markets))
        return markets
    def _parse_market(self, raw):
        q = raw.get("question") or raw.get("title", "")
        if not self.parser.is_temperature_market(q): return None
        city = self.parser.extract_city(q, cfg.cities)
        if not city: return None
        target_date = self.parser.extract_date(q)
        if not target_date: return None
        bin_range = self.parser.extract_bin(q)
        if not bin_range: return None
        import json as _j
        cids = raw.get("clobTokenIds", [])
        if isinstance(cids, str):
            try: cids = _j.loads(cids)
            except: cids = []
        prices = raw.get("outcomePrices", "[0.5,0.5]")
        if isinstance(prices, str):
            try: prices = _j.loads(prices)
            except: prices = [0.5, 0.5]
        mid = float(prices[0]) if prices else 0.5
        tick = float(raw.get("minimumTickSize") or 0.01)
        vol = float(raw.get("volume") or raw.get("volumeUsd") or 0.0)
        return WeatherMarket(
            condition_id=raw.get("conditionId",""), market_slug=raw.get("slug",""),
            question=q, clob_token_ids=[str(t) for t in cids],
            tick_size=tick, neg_risk=bool(raw.get("negRisk",False)),
            city=city, target_date=target_date, bin_range=bin_range,
            outcome_index=0, mid_price=mid, best_bid=mid-tick,
            best_ask=mid+tick, volume_usd=vol, liquidity_usd=vol*0.1)
