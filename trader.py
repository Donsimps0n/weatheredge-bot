"""
trader.py - Paper + live execution via py-clob-client, SQLite ledger.
"""
import logging, os, sqlite3, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from config import cfg
log = logging.getLogger(__name__)

@dataclass
class TradeRecord:
    trade_id: str; condition_id: str; city: str; target_date: str
    bin_label: str; side: str; size_usd: float; fill_price: float
    model_prob: float; edge: float; status: str = "open"
    opened_at: str = ""; notes: str = ""

class Trader:
    def __init__(self, mode="mode"):
        self.mode = mode or cfg.trading_mode.value
        self.db_path = cfg.storage.db_path
        self._bankroll = cfg.risk.paper_bankroll_usd
        self._init_db()
    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY, condition_id TEXT, city TEXT, target_date TEXT,
            bin_label TEXT, side TEXT, size_usd REAL, fill_price REAL,
            model_prob REAL, edge REAL, status TEXT, opened_at TEXT, notes TEXT)""")
        con.execute("CREATE TABLE IF NOT EXISTS bankroll (id INTEGER PRIMARY KEY, ts TEXT, balance_usd REAL)")
        con.commit(); con.close()
    def get_bankroll(self): return self._bankroll
    def place_order(self, market, side, size_usd, edge_result):
        trade_id = str(uuid.uuid4())[:8]
        ts = datetime.now(timezone.utc).isoformat()
        rec = TradeRecord(trade_id=trade_id, condition_id=market.condition_id,
            city=market.city, target_date=str(market.target_date),
            bin_label=market.bin_range.label, side=side, size_usd=size_usd,
            fill_price=edge_result.market_price, model_prob=edge_result.model_prob,
            edge=edge_result.edge, opened_at=ts, notes=f"[PAPER] {edge_result.note[:100]}")
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.trade_id, rec.condition_id, rec.city, rec.target_date, rec.bin_label,
             rec.side, rec.size_usd, rec.fill_price, rec.model_prob, rec.edge,
             rec.status, rec.opened_at, rec.notes))
        self._bankroll -= size_usd
        con.execute("INSERT INTO bankroll(ts,balance_usd) VALUES(?,?)", (ts, self._bankroll))
        con.commit(); con.close()
        log.info("[PAPER] %s %s %s %.2f %s edge=%.3f", market.city, market.bin_range.label, side, size_usd, market.target_date, edge_result.edge)
        return rec
