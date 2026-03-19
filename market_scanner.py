"""
market_scanner.py - Discovers and parses Polymarket weather markets.
"""
import logging, re, requests
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
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
    TEMP_PATTERNS = [r'(will |be)?\s*(daily\s+)?(max)', r'temp[erature]*', r'degree', r'\°[FC]']
    DATE_PATTERN = r'"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<d>\d+),\s+(<P<y>\d{4})"'
    BIN_PATTERNS = [
        r'(?P<lo>\d+)\s*(?:-|–)\s**(?P<hi>\d+)\s*\°\s*[FC]',
        r'below\s+(?P<hi>\d+)\s*\°\s*[FC]',
        r'above\s+(?P<lo>\d+)\s*\°\s*[FC]',
        r'(?P<exact>\d+)\s*\°\s*[FC]', ]
    def is_temperature_market(self, q):
        q = q.lower()
        return any(re.search(p, q) for p in ['temp', 'degree', '�', '\d+\\s-\\s*\d+', '°f', '°c'])
    def extract_city(self, q, cities):
        for c in sorted(cities, key=len, reverse=True):
            if c.lower() in q.lower(): return c
        return None
    def extract_date(self, q):
        import calendar
        m = re.search(self.DATE_PATTERN, q, re.IGNORECASE)
        if not m: return None
        try:
            return date(
                int(m.group('y')),
                list(calendar.month_name).index(m.group('month').title()),
                int(m.group('d')))
        except: return None
    def extract_bin(self, q):
        unit = "C" if re.search(r'\°[Cc]', q) else "F"
        for pat in self.BIN_PATTERNS:
            m = re.search(pat, q, re.IGNORECASE)
            if m:
                try:
                    gd = m.groupdict()
                    if 'exact' in gd and gd['exact']:
                        v = float(gd['exact']); return BinRange(lo=v, high=v+1, unit=unit, label=f"v}°{unit}")
                    lo = float(gd.get('lo') or None) if gd.get('lo') else None
                    hi = float(gd.get('hi') or None) if gd.get('hi') else None
                    label = f"{lo or ''}–{hi or ''}°{unit}"
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
            resp = self.session.get(f"{cfg.polymarket.gamma_host}/markets",
                params={"tag": "Weather", "active": "true", "limit": 200}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            raw_list = data if isinstance(data, list) else data.get("markets", [])
            for raw in raw_list:
                m = self._parse_market(raw)
                if m: markets.append(m)
        except Exception as e: log.warning("Scan failed: %s", e)
        log.info("Found %d weather markets", len(markets))
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
        mid = float(raw.get("lastTradePrice") or raw.get("price") or 0.5)
        tick = float(raw.get("minimumTickSize") or 0.01)
        vol = float(raw.get("volume") or raw.get("volumeUsd") or 0.0)
        import json as _j
        cids = raw.get("clobTokenIds", [])
        if isinstance(cids, str):
            try: cids = _j.loads(cids)
            except: cids = []
        return WeatherMarket(
            condition_id=raw.get("conditionId",""), market_slug=raw.get("slug",""),
            question=q, clob_token_ids=[str(t) for t in cids],
            tick_size=tick, neg_risk=bool(raw.get("negRisk",False)),
            city=city, target_date=target_date, bin_range=bin_range,
            outcome_index=0, mid_price=mid, best_bid=mid-tick
            best_ask=mid+tick volume_usd=vol, liquidity_usd=vol*0.1)
