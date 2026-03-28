import pytz
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram_poster import post_trade_entry
from vision_scanner import VisionRossScanner

logger = logging.getLogger("VISION_ENGINE")

class VisionEngine:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="America/New_York")
        self.scanner = VisionRossScanner()
        self.top_candidates = []

    def hunt_momentum(self):
        """Warrior Trading: Scan 100+ stocks, find top 10, send alerts"""
        now = datetime.now(pytz.timezone("America/New_York"))
        
        # Trading hours: 6:30 AM - 1:00 PM ET (pre-market through lunch)
        if 6 <= now.hour <= 13:
            logger.info("🔍 Running Warrior Trading scan...")
            
            # Get top 10 candidates from full scan
            top_10 = self.scanner.scan_for_momentum()
            self.top_candidates = top_10
            
            # Send Telegram alerts for top candidates
            if top_10:
                # Send top 3 as immediate alerts
                for stock in top_10[:3]:
                    signal_type = "🚨 INSTANT REVERSAL ALERT" if stock['reversal'] else "🔭 MOMENTUM WATCH"
                    
                    post_trade_entry(
                        ticker=stock['symbol'],
                        side="BUY",
                        price=stock['price'],
                        signal=f"{signal_type} | RVOL: {stock['rvol']}x | Gap: {stock['pct_change']}% | Float: {stock['float']}M"
                    )
                
                # Send summary of top 10
                summary = "📊 *TOP 10 WARRIOR SETUPS*\n━━━━━━━━━━━━━━━━━━━━\n"
                for i, stock in enumerate(top_10[:10]):
                    summary += f"{i+1}. *${stock['symbol']}* | {stock['pct_change']}% | RVOL: {stock['rvol']}x | Score: {stock['score']}\n"
                
                from telegram_poster import _send
                _send(summary)
                
            else:
                logger.info("No qualified candidates found in this scan")
                
        else:
            # Outside trading hours
            pass

    def start(self):
        """Start the scanner on 1-minute intervals"""
        self.scheduler.add_job(self.hunt_momentum, 'interval', minutes=1)
        self.scheduler.start()
        logger.info("🔥 Vision Warrior Trading Bot Started - Scanning 100+ stocks every minute")
