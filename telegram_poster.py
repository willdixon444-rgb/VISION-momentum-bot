import os
import requests
import logging
from datetime import datetime
import pytz

logger = logging.getLogger("VISION_POSTER")

_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
_BASE    = "https://api.telegram.org/bot"

def _now_et() -> str:
    et = pytz.timezone("America/New_York")
    return datetime.now(pytz.utc).astimezone(et).strftime("%I:%M:%S %p ET")

def send_message(message: str) -> bool:
    """Core Telegram sender — always uses HTML parse mode"""
    if not _TOKEN or not _CHAT_ID:
        logger.warning("Telegram not configured — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars")
        return False

    url = f"{_BASE}{_TOKEN}/sendMessage"
    payload = {
        "chat_id": _CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"Telegram error {r.status_code}: {r.text}")
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram request failed: {e}")
        return False

def post_trade_entry(ticker, side, price, signal_type, rvol, gap, float_m):
    """Main alert for Vision — Instant Reversal or Momentum Watch"""
    if signal_type == "REVERSAL":
        header = "🚨 <b>INSTANT REVERSAL ALERT</b>"
    else:
        header = "🔭 <b>MOMENTUM WATCH</b>"

    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Ticker:</b> <code>{ticker}</code>\n"
        f"<b>Action:</b> {side}\n"
        f"<b>Price:</b> ${price}\n"
        f"<b>Gap:</b> {gap}%\n"
        f"<b>RVOL:</b> {rvol}x\n"
        f"<b>Float:</b> {float_m}M shares\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {_now_et()}"
    )
    send_message(msg)

def post_health_check(status_msg):
    """Heartbeat message"""
    msg = (
        f"🤖 <b>Vision System Health</b>\n"
        f"Status: {status_msg}\n"
        f"Time: {_now_et()}"
    )
    send_message(msg)
