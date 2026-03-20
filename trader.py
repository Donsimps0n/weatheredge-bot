"""
trader.py - Portfolio-aware trade execution with position limits and outcome tracking.
Improvements:
  1. Outcome tracking: log win/loss per trade when resolved
  2. City performance stats: track win rate per city, reduce sizing for underperforming cities
  3. Daily loss limit: stop trading if down >20% in a day
  4. Anti-tilt: skip city if last 3 trades were losses
"""
import logging, sqlite3
from datetime import datetime, timezone, date
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
    MAX_CITY_EXPOSURE = 0.25   # max 25% of bankroll in one city
    MAX_HEAT = 0.80            # max 80% of bankroll deployed
    DAILY_LOSS_LIMIT = 0.20    # stop if down 20% today
    ANTI_TILT_LOSSES = 3       # skip city after 3 consecutive losses

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
                outcome TEXT DEFAULT 'OPEN',
                pnl REAL DEFAULT 0,
                timestamp TEXT, resolved_at TEXT,
                mode TEXT DEFAULT 'PAPER'
            )""")
            # Add outcome columns to existing DBs if missing
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN outcome TEXT DEFAULT 'OPEN'")
            except: pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN pnl REAL DEFAULT 0")
            except: pass
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN resolved_at TEXT")
            except: pass
            conn.commit()

    def get_open_positions(self) -> List[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE outcome='OPEN' ORDER BY timestamp DESC"
            ).fetchall()
        cols = ['id','condition_id','question','city','side','price','shares',
                'usd_size','edge','model_prob','outcome','pnl','timestamp','resolved_at','mode']
        return [dict(zip(cols, r)) for r in rows]

    def get_deployed_usd(self) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(usd_size),0) FROM trades WHERE outcome='OPEN'"
            ).fetchone()
        return float(row[0])

    def get_city_exposure(self, city: str) -> float:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(usd_size),0) FROM trades WHERE city=? AND outcome='OPEN'",
                (city,)
            ).fetchone()
        return float(row[0])

    def already_traded(self, condition_id: str) -> bool:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE condition_id=?", (condition_id,)
            ).fetchone()
        return row[0] > 0

    def city_win_rate(self, city: str, min_trades: int = 5) -> Optional[float]:
        """Return win rate for a city (None if not enough data)."""
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) "
                "FROM trades WHERE city=? AND outcome IN ('WIN','LOSS')",
                (city,)
            ).fetchone()
        total, wins = row[0], row[1] or 0
        if total < min_trades:
            return None
        return wins / total

    def city_on_tilt(self, city: str) -> bool:
        """Return True if last ANTI_TILT_LOSSES trades for this city were all losses."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT outcome FROM trades WHERE city=? AND outcome IN ('WIN','LOSS') "
                "ORDER BY timestamp DESC LIMIT ?",
                (city, self.ANTI_TILT_LOSSES)
            ).fetchall()
        if len(rows) < self.ANTI_TILT_LOSSES:
            return False
        return all(r[0] == 'LOSS' for r in rows)

    def daily_pnl(self) -> float:
        """Return today's realised PnL."""
        today = date.today().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE resolved_at LIKE ?",
                (today + '%',)
            ).fetchone()
        return float(row[0])

    def should_stop_trading(self, bankroll: float) -> bool:
        """True if daily loss limit hit."""
        pnl = self.daily_pnl()
        if pnl < -bankroll * self.DAILY_LOSS_LIMIT:
            log.warning(f"Daily loss limit hit: {pnl:.2f} (limit={bankroll*self.DAILY_LOSS_LIMIT:.2f})")
            return True
        return False

    def city_size_multiplier(self, city: str) -> float:
        """Scale position down for underperforming cities, up for strong ones."""
        wr = self.city_win_rate(city)
        if wr is None:
            return 1.0          # not enough data, normal sizing
        if wr >= 0.70:
            return 1.3          # hot city — press the edge
        if wr >= 0.50:
            return 1.0          # normal
        if wr >= 0.35:
            return 0.6          # underperforming — reduce
        return 0.3              # cold city — minimal sizing

    def record_trade(self, trade: Trade) -> bool:
        """Record a trade. Returns False if rejected by risk checks."""
        bankroll = cfg.bankroll

        # Risk checks
        if self.should_stop_trading(bankroll):
            log.warning("Trade rejected: daily loss limit hit")
            return False
        if self.already_traded(trade.condition_id):
            log.info(f"Skip duplicate: {trade.condition_id[:12]}")
            return False
        if self.city_on_tilt(trade.city):
            log.warning(f"Skip city on tilt: {trade.city}")
            return False

        deployed = self.get_deployed_usd()
        if deployed / bankroll >= self.MAX_HEAT:
            log.warning(f"Portfolio heat cap: {deployed:.1f}/{bankroll:.1f}")
            return False

        city_exp = self.get_city_exposure(trade.city)
        if city_exp / bankroll >= self.MAX_CITY_EXPOSURE:
            log.warning(f"City cap for {trade.city}: {city_exp:.1f}")
            return False

        # Apply city performance multiplier
        size_mult = self.city_size_multiplier(trade.city)
        adjusted_size = round(trade.usd_size * size_mult, 2)
        if adjusted_size < 0.50:
            log.info(f"Position too small after city adj: {adjusted_size:.2f}")
            return False

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO trades
                  (condition_id,question,city,side,price,shares,usd_size,edge,model_prob,timestamp,mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade.condition_id, trade.question, trade.city, trade.side,
                trade.price, trade.shares, adjusted_size,
                trade.edge, trade.model_prob,
                datetime.now(timezone.utc).isoformat(), trade.mode
            ))
            conn.commit()
        log.info(f"TRADE: {trade.city} {trade.side} {adjusted_size:.2f} edge={trade.edge:.2%} city_mult={size_mult:.1f}")
        return True

    def mark_resolved(self, condition_id: str, outcome: str, pnl: float):
        """Mark trade as WIN/LOSS with PnL when market resolves."""
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET outcome=?, pnl=?, resolved_at=? WHERE condition_id=?",
                (outcome.upper(), pnl, datetime.now(timezone.utc).isoformat(), condition_id)
            )
            conn.commit()
        log.info(f"Resolved {condition_id[:12]}: {outcome} pnl={pnl:+.2f}")

    def get_stats(self) -> dict:
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            open_pos = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='OPEN'").fetchone()[0]
            wins = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='WIN'").fetchone()[0]
            losses = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='LOSS'").fetchone()[0]
            total_pnl = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM trades").fetchone()[0]
            # City leaderboard
            city_stats = conn.execute(
                "SELECT city, COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as w, "
                "COALESCE(SUM(pnl),0) as pnl "
                "FROM trades WHERE outcome IN ('WIN','LOSS') "
                "GROUP BY city ORDER BY pnl DESC LIMIT 10"
            ).fetchall()
        resolved = wins + losses
        win_rate = wins / resolved if resolved > 0 else 0
        return {
            "total_trades": total,
            "open_positions": open_pos,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 3),
            "total_pnl": round(float(total_pnl), 2),
            "daily_pnl": round(self.daily_pnl(), 2),
            "city_leaderboard": [
                {"city": r[0], "trades": r[1], "wins": r[2],
                 "win_rate": round(r[2]/r[1],3) if r[1]>0 else 0,
                 "pnl": round(r[3],2)}
                for r in city_stats
            ]
        }

# Backward compat alias
Trader = PortfolioTrader
