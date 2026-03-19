"""
config.py
=========
Single source of truth for all bot configuration.
Reads from environment variables / .env file via python-dotenv.
"""
from __future__ import annotations
import logging, os, sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass

def _get(key, default=""): return os.getenv(key, default).strip()
def _get_float(key, default):
    raw = os.getenv(key, "")
    try: return float(raw) if raw.strip() else default
    except: return default
def _get_int(key, default):
    raw = os.getenv(key, "")
    try: return int(raw) if raw.strip() else default
    except: return default

class TradingMode(str, Enum):
    PAPER = "PAPER"; LIVE = "LIVE"

@dataclass(frozen=True)
class RiskConfig:
    kelly_fraction: float = 0.25
    min_edge: float = 0.05
    max_order_usd: float = 100.0
    max_bankroll_pct: float = 0.05
    paper_bankroll_usd: float = 1000.0
    daily_loss_limit_pct: float = 0.05
    min_market_liquidity: float = 500.0

@dataclass(frozen=True)
class PolymarketConfig:
    private_key: str = "0x00"
    funder_address: str = "0x00"
    chain_id: int = 137
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"

@dataclass(frozen=True)
class WeatherConfig:
    open_meteo_ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/gfs"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    tomorrow_io_api_key: Optional[str] = None
    openweather_api_key: Optional[str] = None
    min_ensemble_members: int = 10
    min_consensus_sources: int = 1

@dataclass(frozen=True)
class SchedulerConfig:
    scan_interval_seconds: int = 600
    forecast_refresh_seconds: int = 900

@dataclass(frozen=True)
class NotificationConfig:
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    discord_webhook_url: Optional[str] = None

@dataclass(frozen=True)
class StorageConfig:
    db_path: Path = Path("data/weather_bot.db")
    log_file: Path = Path("logs/bot.log")
    log_level: str = "INFO"

@dataclass(frozen=True)
class BotConfig:
    polymarket: PolymarketConfig
    risk: RiskConfig
    weather: WeatherConfig
    scheduler: SchedulerConfig
    notifications: NotificationConfig
    storage: StorageConfig
    trading_mode: TradingMode
    cities: List[str]
    @property
    def is_live(self): return self.trading_mode == TradingMode.LIVE
    @property
    def is_paper(self): return self.trading_mode == TradingMode.PAPER

CITY_COORDS = {
    "New York": (40.7769, -73.8740), "Chicago": (41.9742, -87.9073),
    "Miami": (25.7959, -80.2870), "Seattle": (47.4489, -122.3094),
    "Atlanta": (33.6407, -84.4277), "London": (51.4775, -0.4614),
    "Paris": (49.0128, 2.5500), "Tokyo": (35.5533, 139.7811),
    "Seoul": (37.4692, 126.4505), "Dubai": (25.2528, 55.3644),
    "Sydney": (-33.9399, 151.1753), "Tel Aviv": (32.0114, 34.8867),
}

def get_city_coords(city):
    if city not in CITY_COORDS: raise KeyError(f"City {city} not in CITY_COORDS")
    return CITY_COORDS[city]

def _load_config():
    mode_raw = _get("TPADI^G_MODE", "PAPER").upper()
    try: trading_mode = TradingMode(mode_raw)
    except: trading_mode = TradingMode.PAPER
    cities_raw = _get("CITIES", "New York,London,Tokyo,Seoul,Dubai,Sydney,Paris,Chicago,Miami")
    cities = [c.strip() for c in cities_raw.split(",") if c.strip()]
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    return BotConfig(
        polymarket=PolymarketConfig(
            private_key=_get("POLY_PRIVATE_KEY", "0x0000000000000000000000000000000000000000000000000000000000000000"),
            funder_address=_get("POLY_FUNDER_ADDRESS", "0x0000000000000000000000000000000000000000"),
            chain_id=_get_int("POLY_CHAIN_ID", 137),
        ),
        risk=RiskConfig(
            kelly_fraction=_get_float("KELLY_FRACTION", 0.25),
            min_edge=_get_float("MIN_EDGE", 0.05),
            max_order_usd=_get_float("MAX_ORDER_USD", 100.0),
            max_bankroll_pct=_get_float("MAX_BANKROLL_PCT", 0.05),
            paper_bankroll_usd=_get_float("PAPER_BANKROLL_USD", 1000.0),
            daily_loss_limit_pct=_get_float("DAILY_LOSS_LIMIT_PCT", 0.05),
            min_market_liquidity=_get_float("MIN_MARKET_LIQUIDITY", 500.0),
        ),
        weather=WeatherConfig(
            tomorrow_io_api_key=_get("TOMORROW_IO_API_KEY") or None,
            openweather_api_key=_get("OPENWEATHER_API_KEY") or None,
        ),
        scheduler=SchedulerConfig(
            scan_interval_seconds=_get_int("SCAN_INTERVAL_SECONDS", 600),
            forecast_refresh_seconds=_get_int("FORECAST_REFRESH_SECONDS", 900),
        ),
        notifications=NotificationConfig(
            telegram_bot_token=_get("TEMEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=_get("TELEGRAM_CHAT_ID") or None,
            discord_webhook_url=_get("DISCORD_WEBHOOK_URL") or None,
        ),
        storage=StorageConfig(),
        trading_mode=trading_mode,
        cities=cities,
    )

cfg = _load_config()
