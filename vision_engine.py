"""
VISION Engine v16
=================
Discord-only alerts -- Telegram removed.

- All Telegram calls replaced with discord_poster
- post_bot_startup() fires on start -> synthetic-pulse
- Trade alerts -> paper or live channel (based on VISION_MODE)
- Top 10 summary -> mind-stone-metrics
- Errors -> synthetic-pulse via post_crash_alert()
- Trading window: 9:30 AM - 11:30 AM ET (Ross's prime window)
- Deduplication: one alert per stock per day unless new bull flag + 5% price move
"""

import pytz
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler
from discord_poster import (
    post_trade_alert,
    post_top10_summary,
    post_bot_startup,
    post_crash_alert,
)
from vision_scanner import VisionRossScanner

logger = logging.getLogger("VISION_ENGINE")


class VisionEngine:
    def __init__(self):
        self.scheduler      = BackgroundScheduler(timezone="America/New_York")
        self.scanner        = VisionRossScanner()
        self.top_candidates = []
        self._executor      = ThreadPoolExecutor(max_workers=1)
        self._scan_running  = False
        self._alerted_today = {}

    def _is_new_setup(self, symbol, current_price, has_bull_flag):
        today = date.today()
        if symbol not in self._alerted_today:
            return True
        entry = self._alerted_today[symbol]
        if entry["date"] != today:
            return True
        if has_bull_flag and current_price > 0 and entry.get("last_price", 0) > 0:
            price_change = abs(current_price - entry["last_price"]) / entry["last_price"]
            if price_change >= 0.05:
                logger.info(f"  {symbol} new bull flag setup, re-alerting")
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
        if self._scan_running:
            logger.info("Scan already in progress, skipping.")
            return

        et  = pytz.timezone("America/New_York")
        now = datetime.now(et)

        if now.weekday() >= 5:
            return

        market_open  = now.hour > 6 or (now.hour == 6 and now.minute >= 30)
        market_close = now.hour < 13
        if not (market_open and market_close):
            logger.debug("Outside scan window -- no scan.")
            return

        prime_window = (
            (now.hour == 9 and now.minute >= 30) or
            (now.hour == 10) or
            (now.hour == 11 and now.minute <= 30)
        )

        logger.info(
            f"🔍 Running scan... "
            f"{'[PRIME WINDOW - alerts ON]' if prime_window else '[pre-market/late - alerts OFF]'}"
        )
        self._scan_running = True

        try:
            top_10 = self.scanner.scan_for_momentum()
            self.top_candidates = top_10

            if not top_10:
                logger.info("No qualified candidates found")
                return

            if not prime_window:
                logger.info(f"Found {len(top_10)} candidates — outside prime window, no alerts sent")
                return

            for stock in top_10[:3]:
                symbol        = stock["symbol"]
                price         = stock["price"]
                has_bull_flag = stock.get("bull_flag", False)

                if not self._is_new_setup(symbol, price, has_bull_flag):
                    logger.info(f"  {symbol} already alerted today — skipping")
                    continue

                post_trade_alert(
                    ticker=symbol,
                    price=price,
                    gap=stock["pct_change"],
                    rvol=stock["rvol"],
                    bull_flag=has_bull_flag,
                    above_vwap=stock.get("above_vwap", False),
                    above_ema9=stock.get("above_ema9", False),
                    vwap=stock.get("vwap", 0),
                    ema9=stock.get("ema9", 0),
                    has_news=stock.get("has_news", False),
                    score=stock.get("score", 0),
                    stop_loss=round(price - 0.20, 2),
                    profit_target=round(price + 0.40, 2),
                )
                self._record_alert(symbol, price)

            post_top10_summary(top_10)

        except Exception as e:
            logger.error(f"Scan error: {e}")
            post_crash_alert(str(e), context="scan cycle")
        finally:
            self._scan_running = False

    def _run_scan_blocking(self):
        future = self._executor.submit(self.hunt_momentum)
        future.result()

    def start(self):
        self.scheduler.add_job(
            self._run_scan_blocking,
            "interval",
            minutes=1,
            id="momentum_scan",
            max_instances=1,
        )
        self.scheduler.start()
        logger.info("🔥 VISION Bot Started — scanning every minute during market hours")
        post_bot_startup()
