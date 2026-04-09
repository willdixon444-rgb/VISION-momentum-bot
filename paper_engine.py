"""
paper_engine.py -- VISION Internal Paper Trading Engine
========================================================
Simulates Ross Cameron's exact trade management on every VISION alert.
No broker connection needed — tracks positions in memory, monitors
every 60s via Finnhub candles, and posts results to Discord.

Ross Cameron exit rules implemented:
  1. Sell HALF at T1 (+40¢ from entry) → move stop to breakeven
  2. Hard stop at -20¢ from entry (full exit)
  3. First red 1-min candle after entry (if no T1 hit) → full exit
  4. Extension bar detected → sell remainder aggressively
  5. Price breaks below 9 EMA → sell remainder
  6. End of prime window (11:30 AM ET) → close all positions

Position states:
  OPEN       — entered, watching for T1 or stop
  HALF_OUT   — sold half at T1, stop moved to breakeven, watching remainder
  CLOSED     — fully exited, P&L finalized

All trades logged to self.trade_log list.
Daily summary posted to Discord at end of prime window.
"""

import logging
import time
import requests
import os
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional
import pytz

logger = logging.getLogger("PAPER_ENGINE")

FINNHUB_KEY = lambda: os.environ.get("FINNHUB_API_KEY", "")


@dataclass
class PaperPosition:
    symbol:        str
    entry_price:   float
    shares:        int
    entry_time:    datetime
    stop_loss:     float          # starts at entry - 0.20
    target1:       float          # entry + 0.40
    target2:       float          # entry + 0.80
    state:         str = "OPEN"   # OPEN | HALF_OUT | CLOSED
    shares_remaining: int = 0
    exit_price:    float = 0.0
    exit_time:     Optional[datetime] = None
    exit_reason:   str = ""
    realized_pnl:  float = 0.0    # from half-out
    total_pnl:     float = 0.0    # final including remainder
    peak_price:    float = 0.0    # for extension bar detection

    def __post_init__(self):
        self.shares_remaining = self.shares
        self.peak_price       = self.entry_price


class PaperEngine:
    """
    Manages all simulated paper positions.
    Called by VisionEngine every scan cycle to monitor open positions.
    """

    MAX_POSITIONS = 2      # Ross rarely holds more than 2 at once
    SHARES_PER_TRADE = 100 # Fixed share size for simulation

    def __init__(self):
        self.positions: dict[str, PaperPosition] = {}  # symbol → position
        self.trade_log: list[dict] = []
        self.daily_stats = self._reset_daily_stats()

    def _reset_daily_stats(self):
        return {
            "date":          date.today(),
            "total_trades":  0,
            "wins":          0,
            "losses":        0,
            "gross_pnl":     0.0,
            "t1_hits":       0,
            "stop_hits":     0,
            "red_candle_exits": 0,
            "extension_exits":  0,
        }

    def _check_new_day(self):
        if self.daily_stats["date"] != date.today():
            self.daily_stats = self._reset_daily_stats()

    # ── Position entry ─────────────────────────────────────────────────

    def open_position(self, symbol: str, price: float, shares: int = None) -> Optional[PaperPosition]:
        """
        Open a new paper position when VISION fires an alert.
        Share count comes from scanner's volume-scaled calc, or falls back to default.
        Returns None if already in this symbol or at max positions.
        """
        self._check_new_day()

        if symbol in self.positions:
            logger.info(f"  📄 {symbol} already in paper positions — skipping")
            return None

        open_count = sum(1 for p in self.positions.values() if p.state != "CLOSED")
        if open_count >= self.MAX_POSITIONS:
            logger.info(f"  📄 Max positions ({self.MAX_POSITIONS}) reached — skipping {symbol}")
            return None

        et         = pytz.timezone("America/New_York")
        now        = datetime.now(et)
        share_qty  = shares if shares and shares > 0 else self.SHARES_PER_TRADE

        pos = PaperPosition(
            symbol=symbol,
            entry_price=price,
            shares=share_qty,
            entry_time=now,
            stop_loss=round(price - 0.20, 2),
            target1=round(price + 0.40, 2),
            target2=round(price + 0.80, 2),
        )
        self.positions[symbol] = pos
        self.daily_stats["total_trades"] += 1

        logger.info(
            f"  📄 PAPER OPEN: {symbol} @ ${price} | "
            f"Stop: ${pos.stop_loss} | T1: ${pos.target1} | T2: ${pos.target2}"
        )
        return pos

    # ── Candle fetching ────────────────────────────────────────────────

    def _get_latest_candles(self, symbol: str, count: int = 10):
        """Get last N 1-min candles from Finnhub for position monitoring."""
        key = FINNHUB_KEY()
        if not key:
            return None
        try:
            now      = int(time.time())
            lookback = count * 60 * 3
            url      = "https://finnhub.io/api/v1/stock/candle"
            params   = {
                "symbol":     symbol,
                "resolution": "1",
                "from":       now - lookback,
                "to":         now,
                "token":      key,
            }
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200 and r.json().get("s") == "ok":
                d = r.json()
                candles = []
                for i in range(len(d["c"])):
                    candles.append({
                        "open":   d["o"][i],
                        "high":   d["h"][i],
                        "low":    d["l"][i],
                        "close":  d["c"][i],
                        "volume": d["v"][i],
                        "time":   d["t"][i],
                    })
                return candles[-count:] if len(candles) >= count else candles
        except Exception as e:
            logger.debug(f"Candle fetch error [{symbol}]: {e}")
        return None

    def _current_price(self, symbol: str) -> float:
        """Get current price from Finnhub quote."""
        key = FINNHUB_KEY()
        if not key:
            return 0
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": key},
                timeout=8
            )
            if r.status_code == 200:
                return float(r.json().get("c", 0))
        except Exception:
            pass
        return 0

    # ── Exit logic ─────────────────────────────────────────────────────

    def _detect_extension_bar(self, pos: PaperPosition, candles: list) -> bool:
        """
        Extension bar = current candle body is 2x+ average of last 5 candle bodies.
        Ross sells into this aggressively.
        """
        if not candles or len(candles) < 6:
            return False
        bodies = [abs(c["close"] - c["open"]) for c in candles[-6:-1]]
        avg_body = sum(bodies) / len(bodies) if bodies else 0
        current_body = abs(candles[-1]["close"] - candles[-1]["open"])
        return avg_body > 0 and current_body > avg_body * 2.0

    def _is_red_candle(self, candle: dict) -> bool:
        return candle["close"] < candle["open"]

    def _check_9ema(self, candles: list, current_price: float) -> bool:
        """True if price is above 9 EMA — hold signal."""
        if not candles or len(candles) < 9:
            return True  # not enough data, assume holding
        closes = [c["close"] for c in candles]
        k = 2 / (9 + 1)
        ema = closes[0]
        for price in closes[1:]:
            ema = price * k + ema * (1 - k)
        return current_price > ema

    def _close_half(self, pos: PaperPosition, price: float, reason: str):
        """Sell half the position at T1."""
        half_shares = pos.shares // 2
        pnl = (price - pos.entry_price) * half_shares
        pos.realized_pnl  = round(pnl, 2)
        pos.shares_remaining = pos.shares - half_shares
        pos.state     = "HALF_OUT"
        pos.stop_loss = pos.entry_price  # move stop to breakeven
        logger.info(
            f"  📄 PAPER HALF EXIT: {pos.symbol} sold {half_shares} shares @ ${price} "
            f"| PnL so far: ${pos.realized_pnl:+.2f} | Stop → breakeven ${pos.stop_loss}"
        )
        self.daily_stats["t1_hits"] += 1

    def _close_full(self, pos: PaperPosition, price: float, reason: str):
        """Close remaining position."""
        remainder_pnl = (price - pos.entry_price) * pos.shares_remaining
        pos.total_pnl  = round(pos.realized_pnl + remainder_pnl, 2)
        pos.exit_price  = price
        pos.exit_time   = datetime.now(pytz.timezone("America/New_York"))
        pos.exit_reason = reason
        pos.state       = "CLOSED"

        won = pos.total_pnl > 0
        if won:
            self.daily_stats["wins"] += 1
        else:
            self.daily_stats["losses"] += 1
        self.daily_stats["gross_pnl"] = round(
            self.daily_stats["gross_pnl"] + pos.total_pnl, 2
        )

        # Track exit type
        if "stop" in reason.lower():
            self.daily_stats["stop_hits"] += 1
        elif "red candle" in reason.lower():
            self.daily_stats["red_candle_exits"] += 1
        elif "extension" in reason.lower():
            self.daily_stats["extension_exits"] += 1

        logger.info(
            f"  📄 PAPER CLOSED: {pos.symbol} @ ${price} | "
            f"Reason: {reason} | Total PnL: ${pos.total_pnl:+.2f}"
        )

        self.trade_log.append({
            "symbol":      pos.symbol,
            "entry":       pos.entry_price,
            "exit":        pos.exit_price,
            "shares":      pos.shares,
            "pnl":         pos.total_pnl,
            "reason":      pos.exit_reason,
            "entry_time":  pos.entry_time.strftime("%H:%M ET"),
            "exit_time":   pos.exit_time.strftime("%H:%M ET"),
            "won":         won,
        })

    # ── Main monitor loop ──────────────────────────────────────────────

    def monitor_positions(self) -> list[dict]:
        """
        Called every scan cycle. Checks all open positions against
        Ross Cameron's exit rules. Returns list of Discord alerts to send.
        """
        self._check_new_day()
        alerts = []

        for symbol, pos in list(self.positions.items()):
            if pos.state == "CLOSED":
                continue

            candles = self._get_latest_candles(symbol, count=12)
            price   = self._current_price(symbol)
            time.sleep(0.2)  # Finnhub rate limit

            if not price or price == 0:
                logger.debug(f"  No price for {symbol}, skipping monitor")
                continue

            # Update peak price for extension bar tracking
            pos.peak_price = max(pos.peak_price, price)

            last_candle = candles[-1] if candles else None

            # ── OPEN state: watching for T1 or stop ───────────────────
            if pos.state == "OPEN":

                # Stop hit — full exit
                if price <= pos.stop_loss:
                    self._close_full(pos, price, "Stop loss hit")
                    alerts.append({
                        "type": "stop",
                        "symbol": symbol,
                        "pos": pos,
                    })

                # T1 hit — sell half, move stop to breakeven
                elif price >= pos.target1:
                    self._close_half(pos, price, "T1 hit")
                    alerts.append({
                        "type": "half_exit",
                        "symbol": symbol,
                        "pos": pos,
                    })

                # First red candle = exit signal (Ross's rule when no T1 yet)
                elif last_candle and self._is_red_candle(last_candle):
                    self._close_full(pos, price, "First red candle exit")
                    alerts.append({
                        "type": "red_candle",
                        "symbol": symbol,
                        "pos": pos,
                    })

            # ── HALF_OUT state: watching remainder ────────────────────
            elif pos.state == "HALF_OUT":

                # Breakeven stop hit
                if price <= pos.stop_loss:
                    self._close_full(pos, price, "Breakeven stop hit")
                    alerts.append({
                        "type": "breakeven_stop",
                        "symbol": symbol,
                        "pos": pos,
                    })

                # T2 hit — sell remainder
                elif price >= pos.target2:
                    self._close_full(pos, price, "T2 hit — full exit")
                    alerts.append({
                        "type": "t2_exit",
                        "symbol": symbol,
                        "pos": pos,
                    })

                # Extension bar — sell into strength
                elif candles and self._detect_extension_bar(pos, candles):
                    self._close_full(pos, price, "Extension bar — sold into strength")
                    alerts.append({
                        "type": "extension",
                        "symbol": symbol,
                        "pos": pos,
                    })

                # Price broke below 9 EMA — sell remainder
                elif candles and not self._check_9ema(candles, price):
                    self._close_full(pos, price, "Price broke 9 EMA")
                    alerts.append({
                        "type": "ema_break",
                        "symbol": symbol,
                        "pos": pos,
                    })

        return alerts

    def close_all_eod(self):
        """
        Called at end of prime window (11:30 AM ET).
        Force-close any remaining open positions at current price.
        """
        alerts = []
        for symbol, pos in self.positions.items():
            if pos.state == "CLOSED":
                continue
            price = self._current_price(symbol)
            time.sleep(0.2)
            if not price:
                price = pos.entry_price  # fallback
            self._close_full(pos, price, "End of prime window")
            alerts.append({
                "type": "eod",
                "symbol": symbol,
                "pos": pos,
            })
        return alerts

    def get_daily_summary(self) -> dict:
        return self.daily_stats.copy()

    def open_positions_count(self) -> int:
        return sum(1 for p in self.positions.values() if p.state != "CLOSED")
