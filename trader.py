"""
trader.py - Portfolio-aware trade execution with position limits.
"""
import logging, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from config import cfg
log = logging.getLogger(__name__)
DB_PATH = Path("data/trades.db")

@dataclass
class Trade:
    condition_id: str
    question: str
    city: str
    side: str
    price: float
    shares: float
    usd_size: float
    edge: float
    model_prob: float
    timestamp: str
    mode: str

class PortfolioTrader:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        DB_PATH.parent.mkdir(exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id TEXT NOT NULL,
                question TEXT, city TEXT, side TEXT,
                price REAL, shares REAL, usd_size REAL,
                edge REAL, model_prob REAL,
                timestamp TEXT, mode TEXT,
                status TEXT DEFAULT 'open',
                pnl REAL DEFAULT 0
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cid ON trades(condition_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_city ON trades(city)")

    def get_open_positions(self) -> List[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='open' ORDER BY timestamp DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_open_condition_ids(self) -> set:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT condition_id FROM trades WHERE status='open'"
            ).fetchall()
            return {r[0] for r in rows}

    def get_city_exposure(self, city: str) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(usd_size),0) FROM trades WHERE city=? AND status='open'",
                (city,)
            ).fetchone()
            return float(row[0])

    def get_total_exposure(self) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(usd_size),0) FROM trades WHERE status='open'"
            ).fetchone()
            return float(row[0])

    def can_trade(self, opp) -> tuple:
        """Check portfolio limits before entering."""
        if opp.condition_id in self.get_open_condition_ids():
            return False, "already_in_position"
        bankroll = cfg.risk.paper_bankroll_usd
        city_exp = self.get_city_exposure(opp.city)
        if city_exp + opp.position_usd > bankroll * 0.25:
            return False, "city_limit"
        if self.get_total_exposure() + opp.position_usd > bankroll * 0.80:
            return False, "portfolio_heat"
        if opp.edge < cfg.risk.min_edge:
            return False, "edge_too_low"
        if getattr(opp, "confidence", 0) > 3.5:
            return False, "low_confidence"
        return True, "ok"

    def execute(self, opp, mode: str = "PAPER") -> Optional[Trade]:
        can, reason = self.can_trade(opp)
        if not can:
            log.debug(f"Skip {opp.question[:40]} | {reason}")
            return None
        conf_scale = max(0.3, 1.0 - max(0, getattr(opp,"confidence",1.0) - 1.0) * 0.15)
        final_size = round(min(opp.position_usd * conf_scale, cfg.risk.max_order_usd), 2)
        final_size = max(1.0, final_size)
        shares = final_size / opp.market_price if opp.market_price > 0 else 0
        trade = Trade(
            condition_id=opp.condition_id, question=opp.question, city=opp.city,
            side=opp.side, price=opp.market_price, shares=round(shares,2),
            usd_size=final_size, edge=opp.edge, model_prob=opp.model_prob,
            timestamp=datetime.now(timezone.utc).isoformat(), mode=mode
        )
        if mode == "LIVE":
            if not self._place_clob_order(trade): return None
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO trades (condition_id,question,city,side,price,shares,usd_size,edge,model_prob,timestamp,mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (trade.condition_id,trade.question,trade.city,trade.side,trade.price,
                 trade.shares,trade.usd_size,trade.edge,trade.model_prob,trade.timestamp,trade.mode))
        log.info(f"[{mode}] {trade.side} {trade.question[:45]} | ${trade.usd_size:.2f} @ {trade.price:.3f} edge={trade.edge:.3f}")
        return trade

    def _place_clob_order(self, trade: Trade) -> bool:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs, OrderType, BUY
            client = ClobClient(
                host="https://clob.polymarket.com", chain_id=cfg.polymarket.chain_id,
                private_key=cfg.polymarket.private_key, funder=cfg.polymarket.funder_address,
                signature_type=2)
            order = client.create_order(OrderArgs(
                token_id=trade.condition_id, price=trade.price,
                size=trade.shares, side=BUY, order_type=OrderType.FOK))
            resp = client.post_order(order, OrderType.FOK)
            return resp.get("success", False)
        except Exception as e:
            log.error(f"CLOB order failed: {e}")
            return False

    def mark_resolved(self, condition_id: str, won: bool, pnl: float):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET status='closed', pnl=? WHERE condition_id=? AND status='open'",
                (pnl, condition_id))