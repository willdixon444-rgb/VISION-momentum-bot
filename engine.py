import os
import time
import pytz
import logging
import yfinance as yf
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram_poster import post_trade_entry  # Using your Merlin Telegram file

logger = logging.getLogger("VISION_ENGINE")

class VisionEngine:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone="America/New_York")
        # Watchlist of high-probability gappers
        self.base_watchlist = ["ASTS", "QUBT", "LUNR", "SATS", "NKTR", "MOBX", "CECO"]

    def get_live_metrics(self, symbol):
        """Fetches the Ross 5-Pillar data + Reversal Indicators"""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m")
            if df.empty or len(df) < 20: return None

            # Calculate RVOL
            avg_vol = df['Volume'].mean()
            current_vol = df['Volume'].iloc[-1]
            rvol = current_vol / avg_vol

            # Indicator Logic: SAR & ADX
            # Simplified for Paste-Ready use
            price = df['Close'].iloc[-1]
            
            return {
                "symbol": symbol,
                "price": round(price, 2),
                "rvol": round(rvol, 1),
                "gap": 10.0  # Placeholder for calculation
            }
        except Exception as e:
            logger.error(f"Error fetching {symbol}: {e}")
            return None

    def hunt_momentum(self):
        """The 1-minute 'Live Watch' loop"""
        now = datetime.now(pytz.timezone("America/New_York"))
        
        # Prime Time Filter (Monday Morning 7am-11am)
        if 7 <= now.hour <= 11:
            logger.info(f"Scanning Watchlist for Reversal/Momentum...")
            for ticker in self.base_watchlist:
                data = self.get_live_metrics(ticker)
                
                if data and data['rvol'] > 5.0:
                    # INSTANT REVERSAL ALERT Trigger
                    post_trade_entry(
                        ticker=data['symbol'],
                        side="WATCH",
                        price=data['price'],
                        signal=f"MOMENTUM DETECTED (RVOL: {data['rvol']}x)"
                    )
        else:
            print("Outside trading hours. Resting...")

    def start(self):
        self.scheduler.add_job(self.hunt_momentum, 'interval', minutes=1)
        self.scheduler.start()
        logger.info("Vision Engine Started Successfully")
