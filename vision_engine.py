"""
VISION Engine v15
=================
Changes from v14:
- Trading window tightened: 9:30 AM - 11:30 AM ET (Ross's prime window)
  Pre-market 6:30-9:30 still scans but no trade alerts sent
- Deduplication: each ticker alerted once per day unless new bull flag forms
- Alert message includes entry price, stop loss, and profit target
"""

import pytz
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from telegram_poster import post_trade_entry, send_message
from vision_scanner import VisionRossScanner

logger = logging.getLogger("VISION_ENGINE")


class VisionEngine:
    def __init__(self):
        self.scheduler       = BackgroundScheduler(timezone="America/New_York")
        self.scanner         = VisionRossScanner()
        self.top_candidates  = []
        self._executor       = ThreadPoolExecutor(max_workers=1)
        self._scan_running   = False
        # Deduplication: {ticker: {"date": date, "alert_count": int, "last_price": float}}
        self._alerted_today  = {}

    def _is_new_setup(self, symbol, current_price, has_bull_flag):
        """
        Allow re-alert only if:
        1. Never alerted today, OR
        2. A new bull flag has formed AND price has moved significantly
        """
        today = date.today()
        if symbol not in self._alerted_today:
            return True

        entry = self._alerted_today[symbol]

        # Reset if it's a new day
        if entry["date"] != today:
            return True

        # Allow re-alert if bull flag confirmed AND price moved 5%+ from last alert
        if has_bull_flag and current_price > 0 and entry.get("last_price", 0) > 0:
            price_change = abs(current_price - entry["last_price"]) / entry["last_price"]
            if price_change >= 0.05:
                logger.info(f"  {symbol} new bull flag setup detected, re-alerting")
                return True

        return False

    def _record_alert(self, symbol, price):
        today = date.today()
        if symbol not in self._alerted_today:
            self._alerted_today[symbol] = {"date": today, "alert_count": 0, "last_price": 0}
        self._alerted_today[symbol]["date"]        = today
        self._alerted_today[symbol]["alert_count"] += 1
        self._alerted_today[symbol]["last_price"]  = price

    def hunt_momentum(self):
        """Ross Cameron 5 Pillar scan with bull flag detection."""
        if self._scan_running:
            logger.info("Scan already in progress, skipping.")
            return

        et  = pytz.timezone("America/New_York")
        now = datetime.now(et)

        # Only run Monday-Friday
        if now.weekday() >= 5:
            return

        # Scan window: 6:30 AM - 1:00 PM ET
        market_open = now.hour > 6 or (now.hour == 6 and now.minute >= 30)
        market_close = now.hour < 13

        if not (market_open and market_close):
            logger.debug("Outside scan window — no scan.")
            return

        # Alert window: 9:30 AM - 11:30 AM ET (Ross's prime window)
        # Outside this window we scan but don't send trade alerts
        prime_window = (
            (now.hour == 9 and now.minute >= 30) or
            (now.hour == 10) or
            (now.hour == 11 and now.minute <= 30)
        )

        logger.info(f"🔍 Running scan... {'[PRIME WINDOW - alerts ON]' if prime_window else '[pre-market/late - alerts OFF]'}")
        self._scan_running = True

        try:
            top_10 = self.scanner.scan_for_momentum()
            self.top_candidates = top_10

            if not top_10:
                logger.info("No qualified candidates found")
                return

            # Only send alerts during prime window
            if not prime_window:
                logger.info(f"Found {len(top_10)} candidates — outside prime window, no alerts sent")
                return

            alerts_sent = 0
            for stock in top_10[:3]:
                symbol      = stock["symbol"]
                price       = stock["price"]
                has_bull_flag = stock.get("bull_flag", False)

                if not self._is_new_setup(symbol, price, has_bull_flag):
                    logger.info(f"  {symbol} already alerted today — skipping")
                    continue

                # Calculate stop loss and profit target (Ross's 2:1 ratio)
                stop_loss      = round(price - 0.20, 2)
                profit_target  = round(price + 0.40, 2)
                signal_type    = "REVERSAL" if has_bull_flag else "MOMENTUM"

                post_trade_entry(
                    ticker=symbol,
                    side="BUY",
                    price=price,
                    signal_type=signal_type,
                    rvol=stock["rvol"],
                    gap=stock["pct_change"],
                    float_m=stock["float"],
                    stop_loss=stop_loss,
                    profit_target=profit_target
                )
                self._record_alert(symbol, price)
                alerts_sent += 1

            # Send top 10 summary
            lines = ["<b>📊 TOP 10 WARRIOR SETUPS</b>", "━━━━━━━━━━━━━━━━━━━━"]
            for i, stock in enumerate(top_10[:10]):
                bf = "🚩" if stock.get("bull_flag") else ""
                lines.append(
                    f"{i+1}. <code>${stock['symbol']}</code> {bf} | {stock['pct_change']}% | "
                    f"RVOL: {stock['rvol']}x | Score: {stock['score']}"
                )
            send_message("\n".join(lines))

        except Exception as e:
            logger.error(f"Scan error: {e}")
        finally:
            self._scan_running = False

    def _run_scan_blocking(self):
        future = self._executor.submit(self.hunt_momentum)
        future.result()

    def start(self):
        self.scheduler.add_job(
            self._run_scan_blocking,
            'interval',
            minutes=1,
            id='momentum_scan',
            max_instances=1
        )
        self.scheduler.start()
        logger.info("🔥 Vision Warrior Trading Bot Started — scanning every minute during market hours")
