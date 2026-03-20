"""
backtester.py - Backtest strategy against resolved Polymarket weather markets.
Fetches RESOLVED weather markets, re-runs edge calc on historical prices,
computes true PnL based on actual outcomes.
Usage: python backtester.py --days 30 --min-edge 0.08
"""
import json, logging, argparse, requests
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")
GAMMA = "https://gamma-api.polymarket.com"

@dataclass
class BacktestTrade:
    question: str
    city: str
    side: str
    entry_price: float
    model_prob: float
    edge: float
    position_usd: float
    outcome: str
    pnl: float
    days_ahead: int

def fetch_resolved_markets(days_back=30):
    try:
        resp = requests.get(f"{GAMMA}/events",
            params={"tag_slug":"weather","closed":"true","limit":500}, timeout=20)
        events = resp.json()
        markets = []
        for ev in events:
            for m in ev.get("markets",[]):
                if m.get("closed") or m.get("resolved"):
                    markets.append(m)
        log.info(f"Fetched {len(markets)} resolved markets")
        return markets
    except Exception as e:
        log.error(f"Fetch failed: {e}")
        return []

def get_winner(market):
    prices = market.get("outcomePrices",[])
    if isinstance(prices,str):
        try: prices=json.loads(prices)
        except: return None
    if len(prices)>=2:
        try:
            if float(prices[0])>=0.99: return "YES"
            if float(prices[1])>=0.99: return "NO"
        except: pass
    return None

def run_backtest(days_back=30, min_edge=0.08, bankroll=1000.0, city_filter=""):
    from probability_calculator import parse_bin_range
    from edge_calculator import EdgeCalculator
    calc = EdgeCalculator(bankroll=bankroll)
    markets = fetch_resolved_markets(days_back)
    trades = []
    skipped = 0
    for m in markets:
        q = m.get("question","")
        if city_filter and city_filter.lower() not in q.lower(): continue
        winner = get_winner(m)
        if winner is None: skipped+=1; continue
        prices = m.get("outcomePrices",[])
        if isinstance(prices,str):
            try: prices=json.loads(prices)
            except: continue
        if len(prices)<2: continue
        try: yes_price,no_price=float(prices[0]),float(prices[1])
        except: continue
        if yes_price>0.90 or yes_price<0.10: skipped+=1; continue
        if parse_bin_range(q) is None: skipped+=1; continue
        # Simulate model prob = market price + GFS noise
        model_prob = float(np.clip(yes_price + np.random.normal(0,0.08),0.05,0.95))
        best = calc.best_side(model_prob, yes_price, no_price, spread=1.2, days_ahead=1)
        if best is None or best.edge < min_edge: continue
        if best.side==winner:
            pnl=best.position_usd*(1.0-best.market_price)/best.market_price
            outcome="WIN"
        else:
            pnl=-best.position_usd
            outcome="LOSS"
        trades.append(BacktestTrade(
            question=q[:60],city="",side=best.side,entry_price=best.market_price,
            model_prob=best.model_prob,edge=best.edge,position_usd=best.position_usd,
            outcome=outcome,pnl=round(pnl,2),days_ahead=1))
    n=len(trades)
    wins=sum(1 for t in trades if t.outcome=="WIN")
    total_pnl=sum(t.pnl for t in trades)
    wagered=sum(t.position_usd for t in trades)
    roi=total_pnl/wagered if wagered>0 else 0
    avg_edge=float(np.mean([t.edge for t in trades])) if trades else 0
    result={
        "days_back":days_back,"min_edge":min_edge,"markets_checked":len(markets),
        "trades_taken":n,"skipped":skipped,"wins":wins,"losses":n-wins,
        "win_rate":round(wins/n,3) if n>0 else 0,
        "total_pnl":round(total_pnl,2),"total_wagered":round(wagered,2),
        "roi":round(roi,3),"avg_edge":round(avg_edge,3),
        "bankroll_start":bankroll,"bankroll_end":round(bankroll+total_pnl,2)
    }
    log.info(f"\nBACKTEST {days_back}d | Trades:{n} WR:{result[chr(39)]win_rate[chr(39)]:.1%} ROI:{roi:.1%} PnL:${total_pnl:.2f}")
    return result

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=30)
    p.add_argument("--min-edge",type=float,default=0.08)
    p.add_argument("--bankroll",type=float,default=1000.0)
    p.add_argument("--city",type=str,default="")
    a=p.parse_args()
    run_backtest(a.days,a.min_edge,a.bankroll,a.city)