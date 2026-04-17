"""
VISION Engine v22
=================
New in v22:
1. GAP AND GO — fires 9:30-9:35 AM ET on first green candle + news + RVOL 10x+
2. CHART SNAPSHOT — captures 1-min and 5-min candles at exact alert time
   Stored in database for backtesting review
"""

import pytz
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from apscheduler.schedulers.background import BackgroundScheduler

from discord_poster import (
    post_trade_alert,
    post_top10_summary,
    post_bot_startup,
    post_crash_alert,
    post_paper_open,
    post_paper_half_exit,
    post_paper_close,
    post_paper_daily_summary,
)
from vision_scanner import VisionRossScanner
from paper_engine   import PaperEngine
from webull_trader  import WebullTrader

logger = logging.getLogger("VISION_ENGINE")


class VisionEngine:
    def __init__(self):
        self.scheduler       = BackgroundScheduler(timezone="America/New_York")
        self.scanner         = VisionRossScanner()
        self.paper_engine    = PaperEngine()
        self.webull_trader   = WebullTrader()
        self.top_candidates  = []
        self._executor       = ThreadPoolExecutor(max_workers=1)
        self._scan_running   = False
        self._alerted_today  = {}
        self._eod_posted     = False
        self._last_summary_time = 0.0   # throttle top10 to once per 10 min
        self._prime_first_scan  = True  # force summary on first prime window scan
        self._webull_enabled = os.environ.get("ENABLE_WEBULL", "false").lower() == "true"

        # Scan interval: 15 seconds
        self.SCAN_INTERVAL_SECONDS = 15

        # Rate limit guard: Finnhub allows 60 req/min
        # At 15s intervals = 4 scans/min × ~8 calls = 32 calls/min — safe
        self._scans_this_minute = 0
        self._minute_start      = datetime.now()

    # ── Deduplication ─────────────────────────────────────────────────

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
                logger.info(f"  {symbol} new bull flag, re-alerting")
                return True
        return False

    def _record_alert(self, symbol, price):
        today = date.today()
        if symbol not in self._alerted_today:
            self._alerted_today[symbol] = {
                "date": today, "alert_count": 0, "last_price": 0
            }
        self._alerted_today[symbol]["date"]        = today
        self._alerted_today[symbol]["alert_count"] += 1
        self._alerted_today[symbol]["last_price"]  = price

    # ── Paper position handling ────────────────────────────────────────

    def _open_paper_positions(self, symbol, price, shares, stock=None):
        ctx = stock or {}
        # Internal paper engine
        pos = self.paper_engine.open_position(
            symbol, price, shares,
            rvol=ctx.get("rvol", 0),
            gap_pct=ctx.get("pct_change", 0),
            float_m=ctx.get("float", 0),
            has_news=ctx.get("has_news", False),
            bull_flag=ctx.get("bull_flag", False),
            above_vwap=ctx.get("above_vwap", False),
            above_ema9=ctx.get("above_ema9", False),
            score=ctx.get("score", 0),
            alert_type=ctx.get("alert_type", "bull_flag"),
            extended=ctx.get("extended", False),
            chart_snapshot=ctx.get("chart_snapshot", {}),
        )
        if pos:
            post_paper_open(
                symbol=symbol,
                entry=pos.entry_price,
                stop=pos.stop_loss,
                t1=pos.target1,
                t2=pos.target2,
                shares=pos.shares,
                source="internal sim",
            )

        # Webull paper trade (if enabled)
        if self._webull_enabled:
            result = self.webull_trader.open_paper_trade(symbol, price)
            if result.get("success"):
                post_paper_open(
                    symbol=symbol,
                    entry=result["limit"],
                    stop=round(price - 0.20, 2),
                    t1=round(price + 0.40, 2),
                    t2=round(price + 0.80, 2),
                    shares=result["shares"],
                    source="Webull paper",
                )
            else:
                logger.warning(f"  Webull paper trade failed: {result.get('error')}")

    def _process_paper_alerts(self, alerts):
        for alert in alerts:
            pos    = alert["pos"]
            symbol = alert["symbol"]
            atype  = alert["type"]

            if atype == "half_exit":
                post_paper_half_exit(
                    symbol=symbol,
                    price=pos.target1,
                    pnl_so_far=pos.realized_pnl,
                    new_stop=pos.stop_loss,
                    shares_remaining=pos.shares_remaining,
                )
                if self._webull_enabled:
                    self.webull_trader.close_half(symbol, pos.target1, pos.shares)

            elif atype in ("stop", "red_candle", "breakeven_stop",
                           "t2_exit", "extension", "ema_break", "eod"):
                post_paper_close(
                    symbol=symbol,
                    entry=pos.entry_price,
                    exit_price=pos.exit_price,
                    shares=pos.shares,
                    total_pnl=pos.total_pnl,
                    reason=pos.exit_reason,
                )
                if self._webull_enabled:
                    self.webull_trader.close_all(
                        symbol, pos.exit_price, pos.shares_remaining
                    )

    # ── EOD ───────────────────────────────────────────────────────────

    def _check_eod(self, now):
        is_eod = now.hour == 11 and now.minute >= 30
        today  = date.today()

        if is_eod and not self._eod_posted:
            eod_alerts = self.paper_engine.close_all_eod()
            self._process_paper_alerts(eod_alerts)
            stats = self.paper_engine.get_daily_summary()
            post_paper_daily_summary(stats)
            self._eod_posted = True
            logger.info("📊 EOD paper summary posted")

        if now.hour < 9:
            self._eod_posted    = False
            self._prime_first_scan = True  # reset for next trading day

    # ── Main scan ─────────────────────────────────────────────────────

    def hunt_momentum(self):
        if self._scan_running:
            return

        et  = pytz.timezone("America/New_York")
        now = datetime.now(et)

        # Weekends off
        if now.weekday() >= 5:
            return

        # Scan window: 6:30 AM - 1:00 PM ET
        market_open  = now.hour > 6 or (now.hour == 6 and now.minute >= 30)
        market_close = now.hour < 13
        if not (market_open and market_close):
            logger.debug(f"💤 Outside scan window ({now.strftime('%H:%M ET')}) — sleeping")
            return

        # Pre-market mode: scan + build watchlist, no alerts
        is_premarket = now.hour < 9 or (now.hour == 9 and now.minute < 30)

        # Prime alert window: 9:30 AM - 11:30 AM ET
        prime_window = (
            (now.hour == 9 and now.minute >= 30) or
            (now.hour == 10) or
            (now.hour == 11 and now.minute <= 30)
        )

        if is_premarket:
            logger.info("🌅 Pre-market scan — building watchlist (no alerts)")
        else:
            logger.info(
                f"🔍 Scan — "
                f"{'[PRIME WINDOW - alerts ON]' if prime_window else '[late - alerts OFF]'}"
            )

        self._scan_running = True

        try:
            # Monitor paper positions every scan cycle
            if self.paper_engine.open_positions_count() > 0:
                paper_alerts = self.paper_engine.monitor_positions()
                self._process_paper_alerts(paper_alerts)

            # EOD check
            self._check_eod(now)

            # Run scanner
            top_10 = self.scanner.scan_for_momentum()
            self.top_candidates = top_10

            if not top_10:
                logger.info("No qualified candidates")
                return

            # Pre-market: just log the watchlist being built, no alerts
            if is_premarket:
                logger.info(
                    f"📋 Watchlist updated: "
                    f"{[s['symbol'] for s in top_10[:5]]}"
                )
                return

            # Post prime window: no alerts
            if not prime_window:
                logger.info(
                    f"Found {len(top_10)} — outside prime window, no alerts"
                )
                return

            # Prime window: send alerts for top 3 ALERT READY stocks only
            alert_ready = [s for s in top_10 if s.get("alert_ready", False)]
            watchlist_only = [s for s in top_10 if not s.get("alert_ready", False)]

            logger.info(
                f"  🚨 Alert ready: {len(alert_ready)} | "
                f"📋 Watchlist only: {len(watchlist_only)}"
            )

            for stock in alert_ready[:3]:
                symbol        = stock["symbol"]
                price         = stock["price"]
                has_bull_flag = stock.get("bull_flag", False)
                shares        = stock.get("shares", 100)
                alert_type    = stock.get("alert_type", "bull_flag")
                extended      = stock.get("extended", False)

                if not self._is_new_setup(symbol, price, has_bull_flag):
                    logger.info(f"  {symbol} already alerted — skipping")
                    continue

                # Capture chart snapshot at exact alert time
                try:
                    snapshot = self.scanner.capture_chart_snapshot(symbol)
                    stock["chart_snapshot"] = snapshot
                except Exception as e:
                    logger.warning(f"  Snapshot failed: {e}")
                    stock["chart_snapshot"] = {}

                # Discord alert — labeled by strategy type
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
                    alert_type=alert_type,
                    extended=extended,
                )

                # Paper positions with volume-scaled share count + stock context
                self._open_paper_positions(symbol, price, shares, stock)
                self._record_alert(symbol, price)

            # Top 10 to mind-stone-metrics — throttled to once per 10 min
            # Always fires on first scan of prime window so you see it immediately
            import time as _time
            now_ts   = _time.time()
            elapsed  = now_ts - self._last_summary_time
            should_post = (
                self._prime_first_scan or      # first scan of prime window today
                elapsed >= 600 or              # 10 minutes since last post
                len(alert_ready) > 0           # always post when alert ready
            )
            if should_post:
                post_top10_summary(top_10, force=self._prime_first_scan)
                self._last_summary_time = now_ts
                self._prime_first_scan  = False

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
            seconds=self.SCAN_INTERVAL_SECONDS,  # 15 seconds
            id="momentum_scan",
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(
            f"🔥 VISION v20 — scanning every {self.SCAN_INTERVAL_SECONDS}s | "
            f"Pre-market watchlist active | Webull: {'ON' if self._webull_enabled else 'OFF'}"
        )
        post_bot_startup()
