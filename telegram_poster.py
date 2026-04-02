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

def post_trade_entry(ticker, side, price, signal_type, rvol, gap, float_m,
                     stop_loss=None, profit_target=None):
    """
    Main alert for Vision — Bull Flag or Momentum Watch
    Includes Ross Cameron's full scale-out exit strategy
    """
    if signal_type == "REVERSAL":
        header = "🚩 <b>BULL FLAG ALERT</b>"
    else:
        header = "🔭 <b>MOMENTUM WATCH</b>"

    # Calculate scale-out levels
    stop    = stop_loss    if stop_loss    else round(price - 0.20, 2)
    target1 = profit_target if profit_target else round(price + 0.40, 2)
    target2 = round(price + 0.80, 2)   # second target if momentum continues

    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Ticker:</b> <code>{ticker}</code>\n"
        f"<b>Action:</b> {side}\n"
        f"<b>Entry:</b> ${price}\n"
        f"<b>Gap:</b> {gap}%\n"
        f"<b>RVOL:</b> {rvol}x\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>TRADE PLAN (Ross Cameron)</b>\n"
        f"🔴 Stop Loss:  ${stop} (-20¢)\n"
        f"🟡 Target 1:   ${target1} (+40¢) → <b>SELL HALF</b>\n"
        f"🟢 Target 2:   ${target2} (+80¢) → sell remainder\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>EXIT RULES</b>\n"
        f"• Hit T1 → sell half, move stop to breakeven\n"
        f"• Hold rest above 9 EMA\n"
        f"• Parabolic spike → sell into strength\n"
        f"• First red candle (if no T1 hit) → EXIT ALL\n"
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
