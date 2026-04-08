"""
discord_poster.py -- VISION Discord Alerts (Oracle Theme)
Discord only -- no Telegram.

Webhook env vars (already saved in Render):
  DISCORD_VISION_PAPER_WEBHOOK   -> vision-paper-trades   (trade alerts - paper mode)
  DISCORD_VISION_LIVE_WEBHOOK    -> vision-live-trades    (trade alerts - live mode)
  DISCORD_VISION_STATS_WEBHOOK   -> mind-stone-metrics    (top 10 summary + daily stats)
  DISCORD_VISION_HEALTH_WEBHOOK  -> synthetic-pulse       (bot health, API failures, shutdowns)

Mode switching:
  Set VISION_MODE=paper (default) or VISION_MODE=live in Render env vars.
  Paper trades route to vision-paper-trades.
  Live trades route to vision-live-trades.
"""

import os
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Webhook URL helpers ───────────────────────────────────────────────────────

def _webhook(key):
    return os.environ.get(key, "")

PAPER_URL  = lambda: _webhook("DISCORD_VISION_PAPER_WEBHOOK")
LIVE_URL   = lambda: _webhook("DISCORD_VISION_LIVE_WEBHOOK")
STATS_URL  = lambda: _webhook("DISCORD_VISION_STATS_WEBHOOK")
HEALTH_URL = lambda: _webhook("DISCORD_VISION_HEALTH_WEBHOOK")

def _is_live():
    return os.environ.get("VISION_MODE", "paper").strip().lower() == "live"

def _trade_url():
    return LIVE_URL() if _is_live() else PAPER_URL()

def _mode_label():
    return "LIVE 🔴" if _is_live() else "PAPER 🟡"


# ── Core sender (same pattern as Merlin's working implementation) ─────────────

def _post(webhook_url, embeds=None, content=None):
    if not webhook_url:
        logger.warning("Discord webhook URL not set -- skipping")
        return
    try:
        payload = {}
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds
        r = requests.post(webhook_url, json=payload, timeout=5)
        r.raise_for_status()
        logger.info("Discord alert sent OK")
    except Exception as e:
        logger.warning(f"Discord post failed: {e}")


def _now_utc():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


# ── TRADE ALERT → vision-paper-trades or vision-live-trades ──────────────────

def post_trade_alert(
    ticker,
    price,
    gap,
    rvol,
    bull_flag=False,
    above_vwap=False,
    above_ema9=False,
    vwap=0,
    ema9=0,
    has_news=False,
    score=0,
    stop_loss=None,
    profit_target=None,
):
    """
    Full trade alert with Ross Cameron's scale-out trade plan.
    Routes to vision-paper-trades or vision-live-trades based on VISION_MODE.
    """
    stop = stop_loss    if stop_loss    else round(price - 0.20, 2)
    t1   = profit_target if profit_target else round(price + 0.40, 2)
    t2   = round(price + 0.80, 2)

    color = 0x00FF88 if bull_flag else 0x00AAFF
    title = "🚩 BULL FLAG ALERT" if bull_flag else "🔭 MOMENTUM WATCH"

    fields = [
        {"name": "Ticker",    "value": f"**${ticker}**",                   "inline": True},
        {"name": "Entry",     "value": f"**${price}**",                    "inline": True},
        {"name": "Gap",       "value": f"**+{gap}%**",                     "inline": True},
        {"name": "RVOL",      "value": f"**{rvol}x**",                     "inline": True},
        {"name": "Bull Flag", "value": "✅ Yes" if bull_flag else "❌ No", "inline": True},
        {"name": "News",      "value": "✅ Yes" if has_news else "❌ No",  "inline": True},
        {"name": "VWAP",      "value": f"{'✅' if above_vwap else '⚠️'} ${vwap}", "inline": True},
        {"name": "9 EMA",     "value": f"{'✅' if above_ema9  else '⚠️'} ${ema9}",  "inline": True},
        {"name": "Score",     "value": str(score),                         "inline": True},
        {
            "name": "📊 Trade Plan (Ross Cameron)",
            "value": (
                f"🔴 **Stop:** ${stop} (-20¢)\n"
                f"🟡 **T1:** ${t1} (+40¢) → **SELL HALF**\n"
                f"🟢 **T2:** ${t2} (+80¢) → sell remainder"
            ),
            "inline": False,
        },
        {
            "name": "📋 Exit Rules",
            "value": (
                "• T1 hit → sell half, move stop to **breakeven**\n"
                "• Hold rest above **9 EMA**\n"
                "• Parabolic spike → **sell into strength**\n"
                "• First red candle (no T1) → **EXIT ALL**"
            ),
            "inline": False,
        },
    ]

    embed = {
        "title":  title,
        "color":  color,
        "fields": fields,
        "footer": {"text": f"VISION -- The Digital Oracle | {_mode_label()} | {_now_utc()}"}
    }
    _post(_trade_url(), embeds=[embed])


# ── TOP 10 SUMMARY → mind-stone-metrics ──────────────────────────────────────

def post_top10_summary(candidates):
    """Top 10 Warrior Setups table sent to mind-stone-metrics every prime-window scan."""
    if not candidates:
        return

    lines = []
    for i, s in enumerate(candidates[:10]):
        bf   = "🚩" if s.get("bull_flag")  else "  "
        news = "📰" if s.get("has_news")   else "  "
        vwap = "📈" if s.get("above_vwap") else "📉"
        lines.append(
            f"`{i+1:2}. ${s['symbol']:<6}` {bf}{news}{vwap}"
            f" | +{s['pct_change']}%"
            f" | RVOL: {s['rvol']}x"
            f" | Score: {s['score']}"
        )

    embed = {
        "title":  "📊 TOP 10 WARRIOR SETUPS",
        "color":  0xFFCC00,
        "description": "\n".join(lines),
        "fields": [
            {"name": "Mode",   "value": _mode_label(),        "inline": True},
            {"name": "Window", "value": "9:30–11:30 AM ET",   "inline": True},
            {"name": "Count",  "value": str(len(candidates)), "inline": True},
        ],
        "footer": {"text": f"VISION -- The Digital Oracle | {_now_utc()}"}
    }
    _post(STATS_URL(), embeds=[embed])


# ── BOT STARTUP → synthetic-pulse ────────────────────────────────────────────

def post_bot_startup():
    """Fire to synthetic-pulse when bot starts or restarts after deploy/crash."""
    embed = {
        "title":  "🔮 VISION ONLINE",
        "color":  0x00FF88,
        "description": "Bot started successfully. Scanning every 60s during market hours.",
        "fields": [
            {"name": "Mode",       "value": _mode_label(),      "inline": True},
            {"name": "Window",     "value": "9:30–11:30 AM ET", "inline": True},
            {"name": "Time (UTC)", "value": _now_utc(),         "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle"}
    }
    _post(HEALTH_URL(), embeds=[embed])


# ── HEALTH CHECK → synthetic-pulse ───────────────────────────────────────────

def post_health_check(status="OK", detail=""):
    """Periodic or manual health ping to synthetic-pulse."""
    is_error = status.upper() != "OK"
    color    = 0xFF4444 if is_error else 0x64748B
    icon     = "🔴" if is_error else "🟢"

    embed = {
        "title":  f"{icon} VISION Health",
        "color":  color,
        "fields": [
            {"name": "Status",     "value": status,         "inline": True},
            {"name": "Mode",       "value": _mode_label(),  "inline": True},
            {"name": "Detail",     "value": detail or "—",  "inline": False},
            {"name": "Time (UTC)", "value": _now_utc(),     "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle"}
    }
    _post(HEALTH_URL(), embeds=[embed])


# ── API FAILURE → synthetic-pulse ─────────────────────────────────────────────

def post_api_failure(api_name, detail=""):
    """
    Fire when a critical API fails — Alpaca auth expired, Finnhub key invalid, etc.
    Anything that would stop the bot from scanning.
    """
    embed = {
        "title":  f"🔑 API FAILURE -- {api_name.upper()}",
        "color":  0xFF0000,
        "description": (
            f"**{api_name}** is failing. Scanning may be degraded or halted.\n"
            "Check Render env vars and verify the API key is still valid."
        ),
        "fields": [
            {"name": "API",            "value": api_name,          "inline": True},
            {"name": "Detail",         "value": detail or "—",     "inline": True},
            {"name": "Time (UTC)",     "value": _now_utc(),        "inline": True},
            {"name": "Action",         "value": "Render → Environment Variables → verify key", "inline": False},
        ],
        "footer": {"text": "VISION -- The Digital Oracle | Check Render logs"}
    }
    _post(HEALTH_URL(), embeds=[embed])


# ── CRASH ALERT → synthetic-pulse ─────────────────────────────────────────────

def post_crash_alert(error_msg, context="scan cycle"):
    """Fire on unhandled exception in a critical path."""
    short_err = str(error_msg)[:800] if error_msg else "Unknown error"
    embed = {
        "title":  "🔴 VISION ERROR",
        "color":  0xFF0000,
        "description": f"An error occurred in **{context}**:\n```{short_err}```",
        "fields": [
            {"name": "Time (UTC)", "value": _now_utc(), "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle | Check Render logs immediately"}
    }
    _post(HEALTH_URL(), embeds=[embed])


# ── DAILY STATS → mind-stone-metrics ─────────────────────────────────────────

def post_daily_stats(total_alerts, bull_flag_alerts, top_ticker=None, top_rvol=0):
    """End-of-day summary. Placeholder until v23 trade journal is built."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    top   = f"${top_ticker} ({top_rvol}x RVOL)" if top_ticker else "—"

    embed = {
        "title":  f"📅 Daily Summary -- {today}",
        "color":  0xFFCC00,
        "fields": [
            {"name": "Total Alerts",     "value": str(total_alerts),     "inline": True},
            {"name": "Bull Flag Alerts", "value": str(bull_flag_alerts), "inline": True},
            {"name": "Top Mover",        "value": top,                   "inline": True},
            {"name": "Mode",             "value": _mode_label(),         "inline": True},
            {"name": "Time (UTC)",       "value": _now_utc(),            "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle"}
    }
    _post(STATS_URL(), embeds=[embed])
