import pytz
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram_poster import post_trade_entry, send_message
from vision_scanner import VisionRossScanner

logger = logging.getLogger("VISION_ENGINE")

class VisionEngine:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="America/New_York")
        self.scanner = VisionRossScanner()
        self.top_candidates = []
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._scan_running = False

    def hunt_momentum(self):
        """Warrior Trading: Scan 100+ stocks, find top 10, send alerts"""
        if self._scan_running:
            logger.info("Scan already in progress, skipping this cycle.")
            return

        now = datetime.now(pytz.timezone("America/New_York"))

        # Market Hours: Monday-Friday, 6:30 AM - 1:00 PM ET
        if now.weekday() < 5 and (now.hour > 6 or (now.hour == 6 and now.minute >= 30)) and now.hour < 13:
            logger.info("🔍 Running Warrior Trading scan...")
            self._scan_running = True
            try:
                top_10 = self.scanner.scan_for_momentum()
                self.top_candidates = top_10

                if top_10:
                    # Send top 3 as immediate alerts
                    for stock in top_10[:3]:
                        signal_type = "REVERSAL" if stock['reversal'] else "MOMENTUM"
                        post_trade_entry(
                            ticker=stock['symbol'],
                            side="BUY",
                            price=stock['price'],
                            signal_type=signal_type,
                            rvol=stock['rvol'],
                            gap=stock['pct_change'],
                            float_m=stock['float']
                        )

                    # Send summary of top 10
                    lines = ["<b>📊 TOP 10 WARRIOR SETUPS</b>", "━━━━━━━━━━━━━━━━━━━━"]
                    for i, stock in enumerate(top_10[:10]):
                        lines.append(
                            f"{i+1}. <code>${stock['symbol']}</code> | {stock['pct_change']}% | "
                            f"RVOL: {stock['rvol']}x | Score: {stock['score']}"
                        )
                    send_message("\n".join(lines))
                else:
                    logger.info("No qualified candidates found in this scan")
            except Exception as e:
                logger.error(f"Scan error: {e}")
            finally:
                self._scan_running = False
        else:
            logger.debug("Outside market hours — no scan.")

    def _run_scan_blocking(self):
        """Run scan in executor so scheduler thread never blocks"""
        future = self._executor.submit(self.hunt_momentum)
        future.result()  # Block until complete — prevents scheduler pileup

    def start(self):
        """Start the scanner on 1-minute intervals"""
        self.scheduler.add_job(
            self._run_scan_blocking,
            'interval',
            minutes=1,
            id='momentum_scan',
            max_instances=1
        )
        self.scheduler.start()
        logger.info("🔥 Vision Warrior Trading Bot Started — scanning every minute during market hours")
