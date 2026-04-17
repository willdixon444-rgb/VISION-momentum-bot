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
    alert_type="bull_flag",
    extended=False,
):
    """
    Full trade alert with Ross Cameron's scale-out trade plan.
    Routes to vision-paper-trades or vision-live-trades based on VISION_MODE.
    alert_type: 'bull_flag' or 'gap_and_go'
    """
    stop = stop_loss    if stop_loss    else round(price - 0.20, 2)
    t1   = profit_target if profit_target else round(price + 0.40, 2)
    t2   = round(price + 0.80, 2)

    # Title and color by strategy type
    if alert_type == "gap_and_go":
        color = 0xFFAA00
        title = "⚡ GAP AND GO" + (" — ⚠️ EXTENDED" if extended else "")
    elif bull_flag:
        color = 0x00FF88
        title = "🚩 BULL FLAG ALERT"
    else:
        color = 0x00AAFF
        title = "🔭 MOMENTUM WATCH"

    # Strategy note for Gap and Go
    strategy_note = ""
    if alert_type == "gap_and_go":
        strategy_note = (
            "**Gap and Go** — entering on first green candle at open.\n"
            + ("⚠️ Stock already up 100%+ pre-market — higher risk, smaller size.\n" if extended else "")
        )

    fields = [
        {"name": "Ticker",    "value": f"**${ticker}**",                   "inline": True},
        {"name": "Entry",     "value": f"**${price}**",                    "inline": True},
        {"name": "Gap",       "value": f"**+{gap}%**",                     "inline": True},
        {"name": "RVOL",      "value": f"**{rvol}x**",                     "inline": True},
        {"name": "Strategy",  "value": "⚡ Gap & Go" if alert_type == "gap_and_go" else ("🚩 Bull Flag" if bull_flag else "📋 Momentum"), "inline": True},
        {"name": "News",      "value": "✅ Yes" if has_news else "❌ No",  "inline": True},
        {"name": "VWAP",      "value": f"{'✅' if above_vwap else '⚠️'} ${vwap}", "inline": True},
        {"name": "9 EMA",     "value": f"{'✅' if above_ema9  else '⚠️'} ${ema9}",  "inline": True},
        {"name": "Score",     "value": str(score),                         "inline": True},
    ]

    if strategy_note:
        fields.append({"name": "⚡ Strategy Note", "value": strategy_note, "inline": False})

    fields += [
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

def post_top10_summary(candidates, force: bool = False):
    """
    Watchlist summary to mind-stone-metrics.
    v22: throttled to once per 10 minutes — not every 15s scan.
         Shows gates passed/failed for each stock.
         CST timestamp so you know local time.
    force=True bypasses throttle (first scan of prime window).
    """
    if not candidates:
        return

    import pytz
    from datetime import datetime as _dt
    ct      = pytz.timezone("America/Chicago")
    now_ct  = _dt.now(ct)
    cst_str = now_ct.strftime("%I:%M %p CST")
    utc_str = _dt.utcnow().strftime("%H:%M UTC")
    time_str = f"{cst_str} · {utc_str}"

    lines = []
    for s in candidates[:10]:
        sym  = s.get("symbol", "?")
        gap  = s.get("pct_change", 0)
        rvol = s.get("rvol", 0)

        bf   = "🚩" if s.get("bull_flag")   else "❌"
        news = "📰" if s.get("has_news")    else "❌"
        vwap = "📈" if s.get("above_vwap")  else "📉"
        ema  = "✅" if s.get("above_ema9")  else "➖"
        rdy  = "🚨" if s.get("alert_ready") else "👁"

        blocks = []
        if not s.get("bull_flag"):  blocks.append("no flag")
        if not s.get("has_news"):   blocks.append("no news")
        if not s.get("above_vwap"): blocks.append("↓VWAP")
        if float(rvol) < 10:        blocks.append(f"RVOL {rvol}x<10x")
        block_str = f"  ← {', '.join(blocks)}" if blocks and not s.get("alert_ready") else ""

        lines.append(
            f"{rdy} `${sym:<6}` +{gap}% | {rvol}x | "
            f"{bf}{news}{vwap}{ema}{block_str}"
        )

    ready_count = sum(1 for s in candidates if s.get("alert_ready"))
    watch_count = len(candidates) - ready_count
    color = 0x00FF88 if ready_count > 0 else 0xFFCC00
    title = f"🚨 ALERT READY ({ready_count})" if ready_count > 0 else f"👁 WATCHLIST — {len(candidates[:10])} STOCKS"

    embed = {
        "title":  title,
        "color":  color,
        "description": "\n".join(lines),
        "fields": [
            {"name": "🚨 Alert Ready", "value": str(ready_count),   "inline": True},
            {"name": "👁 Watching",    "value": str(watch_count),   "inline": True},
            {"name": "🕐 Time",        "value": time_str,           "inline": True},
            {"name": "Window",         "value": "9:30–11:30 AM ET", "inline": True},
            {"name": "Mode",           "value": _mode_label(),      "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle | 🚩=BullFlag 📰=News 📈=AboveVWAP ✅=Above9EMA | ❌=Gate Failed"}
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


# ── PAPER TRADE ALERTS ────────────────────────────────────────────────────────

def post_paper_open(symbol, entry, stop, t1, t2, shares, source="internal"):
    """Alert when a paper position is opened."""
    mode_icon = "🟡" if source == "internal" else "🟢"
    embed = {
        "title":  f"📄 PAPER TRADE OPENED — ${symbol}",
        "color":  0x00AAFF,
        "fields": [
            {"name": "Entry",   "value": f"**${entry}**",          "inline": True},
            {"name": "Shares",  "value": str(shares),              "inline": True},
            {"name": "Source",  "value": f"{mode_icon} {source}",  "inline": True},
            {"name": "🔴 Stop", "value": f"${stop} (-20¢)",        "inline": True},
            {"name": "🟡 T1",   "value": f"${t1} (+40¢) → sell half", "inline": True},
            {"name": "🟢 T2",   "value": f"${t2} (+80¢) → sell rest", "inline": True},
        ],
        "footer": {"text": f"VISION -- The Digital Oracle | PAPER | {_now_utc()}"}
    }
    _post(PAPER_URL(), embeds=[embed])


def post_paper_half_exit(symbol, price, pnl_so_far, new_stop, shares_remaining):
    """Alert when T1 hit — sold half, stop moved to breakeven."""
    embed = {
        "title":  f"🟡 T1 HIT — SOLD HALF ${symbol}",
        "color":  0xFFCC00,
        "description": "Stop moved to **breakeven**. Hold remainder above 9 EMA.",
        "fields": [
            {"name": "Exit Price",       "value": f"${price}",              "inline": True},
            {"name": "PnL (half)",       "value": f"${pnl_so_far:+.2f}",   "inline": True},
            {"name": "Remaining Shares", "value": str(shares_remaining),    "inline": True},
            {"name": "New Stop",         "value": f"${new_stop} (breakeven)", "inline": True},
            {"name": "Time (UTC)",       "value": _now_utc(),               "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle | PAPER"}
    }
    _post(PAPER_URL(), embeds=[embed])


def post_paper_close(symbol, entry, exit_price, shares, total_pnl, reason):
    """Alert when a paper position is fully closed."""
    won   = total_pnl > 0
    color = 0x00FF88 if won else 0xFF4444
    icon  = "✅ WIN" if won else "❌ LOSS"

    embed = {
        "title":  f"{icon} — PAPER CLOSED ${symbol}",
        "color":  color,
        "fields": [
            {"name": "Entry",      "value": f"${entry}",             "inline": True},
            {"name": "Exit",       "value": f"${exit_price}",        "inline": True},
            {"name": "Total PnL",  "value": f"**${total_pnl:+.2f}**","inline": True},
            {"name": "Shares",     "value": str(shares),             "inline": True},
            {"name": "Reason",     "value": reason,                  "inline": True},
            {"name": "Time (UTC)", "value": _now_utc(),              "inline": True},
        ],
        "footer": {"text": "VISION -- The Digital Oracle | PAPER"}
    }
    _post(PAPER_URL(), embeds=[embed])


def post_paper_daily_summary(stats: dict):
    """End of prime window — daily paper trading P&L summary."""
    total  = stats.get("total_trades", 0)
    wins   = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    pnl    = stats.get("gross_pnl", 0.0)
    win_rate = round((wins / total * 100) if total > 0 else 0, 1)

    color = 0x00FF88 if pnl >= 0 else 0xFF4444
    icon  = "📈" if pnl >= 0 else "📉"

    embed = {
        "title":  f"{icon} PAPER TRADING DAILY SUMMARY",
        "color":  color,
        "fields": [
            {"name": "Total Trades",    "value": str(total),                     "inline": True},
            {"name": "Wins / Losses",   "value": f"{wins}W / {losses}L",         "inline": True},
            {"name": "Win Rate",        "value": f"{win_rate}%",                 "inline": True},
            {"name": "Gross PnL",       "value": f"**${pnl:+.2f}**",            "inline": True},
            {"name": "T1 Hits",         "value": str(stats.get("t1_hits", 0)),   "inline": True},
            {"name": "Stop Hits",       "value": str(stats.get("stop_hits", 0)), "inline": True},
            {"name": "Red Candle Exits","value": str(stats.get("red_candle_exits", 0)), "inline": True},
            {"name": "Extension Exits", "value": str(stats.get("extension_exits", 0)),  "inline": True},
        ],
        "footer": {"text": f"VISION -- The Digital Oracle | PAPER | {_now_utc()}"}
    }
    _post(STATS_URL(), embeds=[embed])
