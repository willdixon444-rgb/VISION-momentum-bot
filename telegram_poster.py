import os
import requests
import logging
from datetime import datetime
import pytz

logger = logging.getLogger("VISION_POSTER")

# These will be set in your Render Environment Variables
_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
_BASE    = "https://api.telegram.org/bot"

def _now_et() -> str:
    """Vision operates on Eastern Time (Market Hours)"""
    et = pytz.timezone("America/New_York")
    return datetime.now(pytz.utc).astimezone(et).strftime("%I:%M:%S %p ET")

def _send(message: str) -> bool:
    """Standard Telegram Bot API caller"""
    if not _TOKEN or not _CHAT_ID:
        logger.warning("Telegram not configured - check Environment Variables")
        return False
        
    url = f"{_BASE}{_TOKEN}/sendMessage"
    payload = {
        "chat_id": _CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        return False

def post_trade_entry(ticker, side, price, signal):
    """
    The main alert for Vision. 
    Matches your 'INSTANT REVERSAL ALERT' requirement.
    """
    # Color coding/Emoji based on alert type
    header = "🚨 <b>INSTANT REVERSAL ALERT</b>" if "REVERSAL" in signal else "🔭 <b>MOMENTUM WATCH</b>"
    
    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Ticker:</b> <code>{ticker}</code>\n"
        f"<b>Action:</b> {side}\n"
        f"<b>Price:</b> ${price}\n"
        f"<b>Signal:</b> {signal}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {_now_et()}"
    )
    _send(msg)

def post_health_check(status_msg):
    """Simple heartbeat to let you know the scanner is alive"""
    msg = (
        f"🤖 <b>Vision System Health</b>\n"
        f"Status: {status_msg}\n"
        f"Time: {_now_et()}"
    )
    _send(msg)
