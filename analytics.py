"""
analytics.py -- VISION Performance Analytics
=============================================
Pure functions — no DB calls. Pass in trade list from get_all_trades().

Computes:
  - Overall win rate, P&L, EV per trade
  - By hour of day (best trading hours)
  - By bull flag confirmed vs not (does the pattern work?)
  - By RVOL band (does higher RVOL = better outcomes?)
  - By news catalyst (does news matter?)
  - By exit reason (stops vs T1 vs red candle)
  - Max drawdown, Sharpe estimate
  - Best/worst symbols
"""

from __future__ import annotations
import math
import logging
from collections import defaultdict
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _completed(trades: List[dict]) -> List[dict]:
    return [t for t in trades if t.get("result") in ("win", "loss", "breakeven")]

def _is_win(t: dict) -> bool:
    return t.get("result") == "win"

def _pnl(t: dict) -> float:
    v = t.get("pnl_usd")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0

def _group_stats(items: List[Tuple[bool, float]]) -> dict:
    if not items:
        return {"n": 0, "win_rate": None, "total_pnl": 0.0, "avg_pnl": None}
    n     = len(items)
    wins  = sum(1 for w, _ in items if w)
    pnls  = [p for _, p in items]
    total = round(sum(pnls), 2)
    return {
        "n":         n,
        "win_rate":  round(wins / n * 100, 1),
        "total_pnl": total,
        "avg_pnl":   round(total / n, 2),
    }

def _rvol_band(rvol) -> str:
    try:
        r = float(rvol or 0)
    except (TypeError, ValueError):
        return "Unknown"
    if r < 5:   return "<5x"
    if r < 10:  return "5-10x"
    if r < 20:  return "10-20x"
    if r < 50:  return "20-50x"
    return "50x+"

def _sharpe(pnl_list: List[float]) -> Optional[float]:
    n = len(pnl_list)
    if n < 5:
        return None
    mean = sum(pnl_list) / n
    var  = sum((x - mean) ** 2 for x in pnl_list) / (n - 1)
    std  = math.sqrt(var)
    if std == 0:
        return None
    # Assume ~3 trades/day * 252 days = 756 trades/year
    return round(mean / std * math.sqrt(756), 2)

def _max_drawdown(pnl_list: List[float]) -> Tuple[float, float]:
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for pnl in pnl_list:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    pct = round(max_dd / peak * 100, 1) if peak > 0 else 0.0
    return round(max_dd, 2), pct


def compute_analytics(trades: List[dict]) -> dict:
    done   = _completed(trades)
    n_done = len(done)

    overall_pairs = [(_is_win(t), _pnl(t)) for t in done]
    overall       = _group_stats(overall_pairs)
    pnl_series    = [p for _, p in overall_pairs]

    sharpe_val      = _sharpe(pnl_series)
    dd_usd, dd_pct  = _max_drawdown(pnl_series)

    # By hour of day (ET)
    hour_map: Dict[str, List] = defaultdict(list)
    for t in done:
        h = t.get("hour_of_day")
        key = f"{h:02d}:00 ET" if h is not None else "Unknown"
        hour_map[key].append((_is_win(t), _pnl(t)))
    by_hour = {k: _group_stats(v) for k, v in sorted(hour_map.items())}

    # Bull flag confirmed vs not
    bf_map: Dict[str, List] = defaultdict(list)
    for t in done:
        key = "Bull Flag ✅" if t.get("bull_flag") else "No Flag ❌"
        bf_map[key].append((_is_win(t), _pnl(t)))
    by_bull_flag = {k: _group_stats(v) for k, v in bf_map.items()}

    # By RVOL band
    rvol_map: Dict[str, List] = defaultdict(list)
    for t in done:
        key = _rvol_band(t.get("rvol"))
        rvol_map[key].append((_is_win(t), _pnl(t)))
    by_rvol = {k: _group_stats(v) for k, v in sorted(rvol_map.items())}

    # News catalyst
    news_map: Dict[str, List] = defaultdict(list)
    for t in done:
        key = "News ✅" if t.get("has_news") else "No News ❌"
        news_map[key].append((_is_win(t), _pnl(t)))
    by_news = {k: _group_stats(v) for k, v in news_map.items()}

    # VWAP
    vwap_map: Dict[str, List] = defaultdict(list)
    for t in done:
        key = "Above VWAP ✅" if t.get("above_vwap") else "Below VWAP ❌"
        vwap_map[key].append((_is_win(t), _pnl(t)))
    by_vwap = {k: _group_stats(v) for k, v in vwap_map.items()}

    # Exit reason breakdown
    exit_map: Dict[str, List] = defaultdict(list)
    for t in done:
        key = t.get("exit_reason") or "Unknown"
        exit_map[key].append((_is_win(t), _pnl(t)))
    by_exit = {k: _group_stats(v) for k, v in exit_map.items()}

    # Best/worst symbols (min 2 trades)
    sym_map: Dict[str, List] = defaultdict(list)
    for t in done:
        sym = t.get("symbol", "?")
        sym_map[sym].append((_is_win(t), _pnl(t)))
    sym_stats = [
        {"symbol": k, **_group_stats(v)}
        for k, v in sym_map.items()
        if len(v) >= 2
    ]
    best_symbols  = sorted(sym_stats, key=lambda x: x["win_rate"] or 0, reverse=True)[:5]
    worst_symbols = sorted(sym_stats, key=lambda x: x["win_rate"] or 100)[:5]

    return {
        "total_trades":     len(trades),
        "completed_trades": n_done,
        "open_trades":      len(trades) - n_done,
        "overall":          overall,
        "by_hour":          by_hour,
        "by_bull_flag":     by_bull_flag,
        "by_rvol":          by_rvol,
        "by_news":          by_news,
        "by_vwap":          by_vwap,
        "by_exit":          by_exit,
        "best_symbols":     best_symbols,
        "worst_symbols":    worst_symbols,
        "drawdown":         {"usd": dd_usd, "pct": dd_pct},
        "sharpe":           sharpe_val,
    }


def summary_text(report: dict) -> str:
    n   = report.get("completed_trades", 0)
    ov  = report.get("overall", {})
    dd  = report.get("drawdown", {})
    sh  = report.get("sharpe")

    if n == 0:
        return "No completed trades yet — keep scanning!"

    wr  = ov.get("win_rate", 0)
    pnl = ov.get("total_pnl", 0)
    ev  = ov.get("avg_pnl", 0)

    lines = [
        f"VISION Analytics ({n} completed trades)",
        f"  Win rate: {wr:.1f}% | Total PnL: ${pnl:+.2f} | EV/trade: ${ev:+.2f}",
    ]
    if sh is not None:
        lines.append(f"  Sharpe: {sh:.2f} | Max DD: ${dd.get('usd',0):.2f} ({dd.get('pct',0):.1f}%)")

    bf = report.get("by_bull_flag", {})
    if "Bull Flag ✅" in bf and "No Flag ❌" in bf:
        bfr = bf["Bull Flag ✅"]
        nfr = bf["No Flag ❌"]
        lines.append(
            f"  Bull Flag: {bfr['win_rate']:.0f}% ({bfr['n']} trades) | "
            f"No Flag: {nfr['win_rate']:.0f}% ({nfr['n']} trades)"
        )

    best = report.get("best_symbols", [])
    if best:
        b = best[0]
        lines.append(f"  Best symbol: ${b['symbol']} {b['win_rate']:.0f}% ({b['n']} trades)")

    return "\n".join(lines)
