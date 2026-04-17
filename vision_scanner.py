"""
VISION Scanner v22 — Gap and Go + Chart Snapshot at Alert Time
==============================================================
New in v22:
1. GAP AND GO DETECTOR (9:30-9:35 AM ET only)
   - Separate from bull flag strategy — fires right at open
   - Requires: news + RVOL 10x+ + gap 15%+ + first candle green + above VWAP
   - Pre-market extension check: flags stocks already up 100%+ as extended
   - Ross Cameron enters Gap and Go right at 9:30 on strong news catalysts

2. CHART SNAPSHOT AT ALERT TIME
   - Captures last 15 1-min candles + last 10 5-min candles at moment of alert
   - Stored in trade record for backtesting comparison
   - Lets you see exactly what the chart looked like when the bot fired

New in v20:
1. PRE-MARKET WATCHLIST CACHE
   - 6:30-9:30 AM: scans and caches top gap-up stocks with full enrichment
   - 9:30 AM open: watchlist already built, alert fires within seconds
   - Matches exactly what Ross does — he knows his stocks before the bell

2. SMART ENRICHMENT CACHE
   - Tracks which symbols were seen last scan
   - Only re-enriches NEW symbols with expensive Finnhub calls
   - Returning symbols use cached RVOL/news/candles (refreshed every 5 scans)
   - Dramatically reduces API calls, allows faster scan frequency

3. VOLUME-SCALED POSITION SIZING
   - Position size capped at 1% of avg daily volume
   - Prevents bot from moving price on thinly traded micro-caps
   - Returns recommended share count with each qualified stock

4. FLOAT DATA
   - Pulls float from Finnhub /stock/profile2 (free tier)
   - Shows real float in alerts instead of 0M
   - Ross targets <10M float, ideal <5M
"""

import pandas as pd
import numpy as np
import requests
import os
import logging
import time
import re
from datetime import date, datetime
import pytz

logger = logging.getLogger("VISION_SCANNER")

TICKER_RE        = re.compile(r'^[A-Z]{1,6}$')
ALPACA_DATA_BASE = "https://data.alpaca.markets"

# How many scans before we re-enrich a cached symbol
CACHE_REFRESH_SCANS = 5


class VisionRossScanner:
    def __init__(self):
        # Watchlist thresholds — stocks that make the pre-market list
        self.MIN_RVOL      = 5.0
        self.MIN_GAP       = 10.0
        self.MIN_PRICE     = 1.0
        self.MAX_PRICE     = 20.0

        # Alert thresholds — HIGHER bar required to actually fire a trade alert
        # Ross Cameron's real entry criteria are much stricter than just "qualifying"
        self.ALERT_MIN_RVOL = 10.0   # 10x+ for actual alerts (Ross targets abnormal volume)
        self.ALERT_REQUIRE_NEWS = True   # Hard gate — no news = no alert
        self.ALERT_REQUIRE_BULL_FLAG = True  # Hard gate — no pattern = no alert

        # Gap and Go thresholds (9:30–9:35 AM ET only)
        self.GAP_GO_MIN_GAP  = 15.0   # 15%+ gap required (higher bar, no pattern confirmation)
        self.GAP_GO_MIN_RVOL = 10.0   # same RVOL requirement
        self.GAP_GO_EXTENDED = 100.0  # flag as extended if already up 100%+ pre-market
        self.alpaca_key    = os.environ.get("ALPACA_API_KEY", "")
        self.alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.finnhub_key   = os.environ.get("FINNHUB_API_KEY", "")

        # Pre-market watchlist cache
        # {symbol: {enriched stock dict, cached_at: datetime, scan_count: int}}
        self._watchlist_cache: dict = {}
        self._scan_count = 0

        # Track last Alpaca movers list for smart enrichment
        self._last_movers: set = set()

    def _alpaca_headers(self):
        return {
            "APCA-API-KEY-ID":     self.alpaca_key,
            "APCA-API-SECRET-KEY": self.alpaca_secret,
            "Accept": "application/json"
        }

    # ------------------------------------------------------------------ #
    #  Alpaca — universe + volume                                         #
    # ------------------------------------------------------------------ #

    def get_alpaca_movers(self):
        if not self.alpaca_key or not self.alpaca_secret:
            logger.error("❌ Alpaca keys not set")
            return []
        try:
            url = f"{ALPACA_DATA_BASE}/v1beta1/screener/stocks/movers"
            r   = requests.get(
                url, headers=self._alpaca_headers(),
                params={"top": 50}, timeout=10
            )
            if r.status_code == 200:
                gainers = r.json().get("gainers", [])
                results = []
                for item in gainers:
                    sym = item.get("symbol", "")
                    if TICKER_RE.match(sym):
                        results.append({
                            "symbol":     sym,
                            "price":      float(item.get("price", 0)),
                            "change_pct": float(item.get("percent_change", 0)),
                        })
                logger.info(f"📈 Alpaca movers returned {len(results)} gainers")
                return results
            else:
                logger.warning(f"Alpaca movers {r.status_code}: {r.text[:200]}")
                return []
        except Exception as e:
            logger.warning(f"Alpaca movers error: {e}")
            return []

    def get_alpaca_snapshots(self, symbols):
        if not symbols:
            return {}
        try:
            url    = f"{ALPACA_DATA_BASE}/v2/stocks/snapshots"
            params = {"symbols": ",".join(symbols), "feed": "iex"}
            r      = requests.get(
                url, headers=self._alpaca_headers(),
                params=params, timeout=15
            )
            if r.status_code == 200:
                results = {}
                for sym, snap in r.json().items():
                    try:
                        daily = snap.get("dailyBar", {})
                        prev  = snap.get("prevDailyBar", {})
                        results[sym] = {
                            "volume":      daily.get("v", 0),
                            "prev_volume": prev.get("v", 0),
                            "vwap":        daily.get("vw", 0),
                            "open":        daily.get("o", 0),
                            "high":        daily.get("h", 0),
                            "low":         daily.get("l", 0),
                            "close":       daily.get("c", 0),
                            "prev_close":  prev.get("c", 0),
                        }
                    except Exception:
                        pass
                return results
        except Exception as e:
            logger.warning(f"Alpaca snapshots error: {e}")
        return {}

    # ------------------------------------------------------------------ #
    #  Finnhub — enrichment                                               #
    # ------------------------------------------------------------------ #

    def _finnhub_get(self, endpoint, params=None):
        if not self.finnhub_key:
            return None
        url = f"https://finnhub.io/api/v1/{endpoint}"
        p   = {"token": self.finnhub_key}
        if params:
            p.update(params)
        try:
            r = requests.get(url, params=p, timeout=10)
            if r.status_code == 200 and r.text.strip():
                return r.json()
            elif r.status_code == 429:
                logger.warning("Finnhub rate limit — sleeping 10s")
                time.sleep(10)
        except Exception as e:
            logger.warning(f"Finnhub [{endpoint}] error: {e}")
        return None

    def get_float(self, symbol) -> float:
        """Get share float from Finnhub profile2 (free tier)."""
        data = self._finnhub_get("stock/profile2", {"symbol": symbol})
        time.sleep(0.12)
        if data:
            shares_out = data.get("shareOutstanding", 0)  # in millions
            if shares_out:
                return round(float(shares_out), 2)
        return 0.0

    def has_news_today(self, symbol) -> bool:
        today = date.today().strftime("%Y-%m-%d")
        data  = self._finnhub_get("company-news", {
            "symbol": symbol,
            "_from":  today,
            "to":     today
        })
        time.sleep(0.12)
        has = bool(data and len(data) > 0)
        if has:
            logger.info(f"  📰 {symbol} has {len(data)} news item(s) today")
        return has

    def calculate_rvol(self, symbol, today_volume, prev_volume):
        if self.finnhub_key:
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/stock/metric",
                    params={"symbol": symbol, "metric": "all", "token": self.finnhub_key},
                    timeout=8
                )
                time.sleep(0.15)
                if r.status_code == 200:
                    m = r.json().get("metric", {})
                    avg_vol_m = (
                        m.get("10DayAverageTradingVolume") or
                        m.get("3MonthAverageTradingVolume")
                    )
                    if avg_vol_m and avg_vol_m > 0:
                        avg_vol = avg_vol_m * 1_000_000
                        rvol    = today_volume / avg_vol
                        logger.info(
                            f"  {symbol} RVOL: {rvol:.1f}x "
                            f"(today:{today_volume:,} / 10d-avg:{avg_vol:,.0f})"
                        )
                        return round(rvol, 1), avg_vol
            except Exception:
                pass

        if prev_volume and prev_volume > 0:
            rvol = today_volume / prev_volume
            logger.info(f"  {symbol} RVOL (vs yesterday): {rvol:.1f}x")
            return round(rvol, 1), prev_volume

        return 0, 0

    def get_candles(self, symbol, resolution="1", count=100):
        now      = int(time.time())
        lookback = count * int(resolution) * 60 * 2
        data     = self._finnhub_get("stock/candle", {
            "symbol":     symbol,
            "resolution": resolution,
            "from":       now - lookback,
            "to":         now
        })
        if not data or data.get("s") != "ok":
            return None
        try:
            df = pd.DataFrame({
                "Open":   data["o"],
                "High":   data["h"],
                "Low":    data["l"],
                "Close":  data["c"],
                "Volume": data["v"],
                "Time":   pd.to_datetime(data["t"], unit="s")
            })
            return df.set_index("Time")
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Technical analysis                                                  #
    # ------------------------------------------------------------------ #

    def calculate_vwap(self, df):
        try:
            if df is None or len(df) < 2:
                return 0
            tp   = (df["High"] + df["Low"] + df["Close"]) / 3
            vwap = (tp * df["Volume"]).sum() / df["Volume"].sum()
            return round(float(vwap), 4)
        except Exception:
            return 0

    def calculate_ema(self, df, period=9):
        try:
            if df is None or len(df) < period:
                return 0
            ema = df["Close"].ewm(span=period, adjust=False).mean()
            return round(float(ema.iloc[-1]), 4)
        except Exception:
            return 0

    def detect_bull_flag(self, df, timeframe="1min"):
        try:
            if df is None or len(df) < 6:
                return False, 0
            candles = df.tail(8).reset_index(drop=True)
            n       = len(candles)
            for i in range(2, n):
                current  = candles.iloc[i]
                is_green = current["Close"] > current["Open"]
                if not is_green:
                    continue
                prev_candles = candles.iloc[max(0, i-3):i]
                red_candles  = prev_candles[prev_candles["Close"] < prev_candles["Open"]]
                if len(red_candles) < 1:
                    continue
                max_red_high = red_candles["High"].max()
                if current["Close"] <= max_red_high:
                    continue
                if i >= 3:
                    pre_red      = candles.iloc[max(0, i-5):i-len(red_candles)]
                    green_before = pre_red[pre_red["Close"] > pre_red["Open"]]
                    if len(green_before) >= 1:
                        avg_green_vol = green_before["Volume"].mean() if len(green_before) > 0 else 1
                        avg_red_vol   = red_candles["Volume"].mean() if len(red_candles) > 0 else 1
                        if avg_red_vol < avg_green_vol * 1.5:
                            entry = round(float(current["High"]), 2)
                            logger.info(
                                f"  🚩 Bull flag on {timeframe} — entry: ${entry}"
                            )
                            return True, entry
        except Exception as e:
            logger.debug(f"Bull flag error: {e}")
        return False, 0

    # ------------------------------------------------------------------ #
    #  Volume-scaled position sizing                                       #
    # ------------------------------------------------------------------ #

    def calc_position_size(self, avg_daily_volume: float, price: float,
                            max_shares: int = 500) -> int:
        """
        Cap position at 1% of avg daily volume to avoid moving the price.
        Also cap at $2000 notional value for risk management.
        Ross uses much larger sizes — we scale up as the account grows.
        """
        if avg_daily_volume <= 0:
            return 50  # default conservative size

        vol_cap      = int(avg_daily_volume * 0.01)   # 1% of avg daily volume
        notional_cap = int(2000 / price) if price > 0 else 100  # max $2000

        shares = min(vol_cap, notional_cap, max_shares)
        shares = max(shares, 10)  # minimum 10 shares

        # Round to nearest 10
        shares = round(shares / 10) * 10
        return shares

    # ------------------------------------------------------------------ #
    #  Chart snapshot at alert time — for backtesting                     #
    # ------------------------------------------------------------------ #

    def capture_chart_snapshot(self, symbol: str) -> dict:
        """
        Capture the last 15 1-min candles and last 10 5-min candles
        at the exact moment an alert fires. Stored with the trade record
        so you can replay what the chart looked like when the bot entered.
        """
        snapshot = {
            "symbol":    symbol,
            "captured_at": datetime.now(pytz.timezone("America/New_York")).isoformat(),
            "candles_1min": [],
            "candles_5min": [],
        }
        try:
            df_1 = self.get_candles(symbol, resolution="1", count=15)
            time.sleep(0.15)
            df_5 = self.get_candles(symbol, resolution="5", count=10)
            time.sleep(0.15)

            if df_1 is not None:
                snapshot["candles_1min"] = [
                    {
                        "t": str(idx),
                        "o": round(float(row["Open"]),   4),
                        "h": round(float(row["High"]),   4),
                        "l": round(float(row["Low"]),    4),
                        "c": round(float(row["Close"]),  4),
                        "v": int(row["Volume"]),
                    }
                    for idx, row in df_1.tail(15).iterrows()
                ]

            if df_5 is not None:
                snapshot["candles_5min"] = [
                    {
                        "t": str(idx),
                        "o": round(float(row["Open"]),   4),
                        "h": round(float(row["High"]),   4),
                        "l": round(float(row["Low"]),    4),
                        "c": round(float(row["Close"]),  4),
                        "v": int(row["Volume"]),
                    }
                    for idx, row in df_5.tail(10).iterrows()
                ]

            logger.info(
                f"  📸 Chart snapshot captured for {symbol}: "
                f"{len(snapshot['candles_1min'])} 1-min, "
                f"{len(snapshot['candles_5min'])} 5-min candles"
            )
        except Exception as e:
            logger.warning(f"  Chart snapshot failed for {symbol}: {e}")
        return snapshot

    # ------------------------------------------------------------------ #
    #  Gap and Go detector — 9:30–9:35 AM ET only                         #
    # ------------------------------------------------------------------ #

    def check_gap_and_go(self, s: dict, snap: dict, rvol: float) -> dict | None:
        """
        Ross Cameron Gap and Go — fires ONLY in first 5 minutes of market open.
        No bull flag required. Requires stronger news + RVOL conviction.

        Returns enriched dict with alert_type='gap_and_go' or None if not qualified.

        Gates:
          1. News confirmed (hard requirement — no news = no Gap and Go)
          2. RVOL >= 10x
          3. Gap >= 15%
          4. First 1-min candle at open closes GREEN
          5. Price above VWAP
          6. Pre-market extension flag (if up 100%+ pre-market = extended, warn but allow)
        """
        symbol        = s["symbol"]
        current_price = s["price"]
        gap_pct       = s["change_pct"]

        logger.info(f"  ⚡ Gap and Go check: {symbol} +{gap_pct:.1f}% RVOL:{rvol}x")

        # Gate 1: Gap threshold
        if gap_pct < self.GAP_GO_MIN_GAP:
            logger.info(f"  {symbol} ❌ Gap and Go: gap {gap_pct:.1f}% < {self.GAP_GO_MIN_GAP}%")
            return None

        # Gate 2: RVOL
        if rvol < self.GAP_GO_MIN_RVOL:
            logger.info(f"  {symbol} ❌ Gap and Go: RVOL {rvol}x < {self.GAP_GO_MIN_RVOL}x")
            return None

        # Gate 3: News (hard gate)
        has_news = self.has_news_today(symbol)
        if not has_news:
            logger.info(f"  {symbol} ❌ Gap and Go: no news catalyst")
            return None

        # Gate 4: Get first 1-min candle and check it's green
        df_1min = self.get_candles(symbol, resolution="1", count=10)
        time.sleep(0.15)

        if df_1min is None or len(df_1min) < 1:
            logger.info(f"  {symbol} ❌ Gap and Go: no candle data")
            return None

        first_candle = df_1min.iloc[-1]  # most recent = the open candle
        first_green  = float(first_candle["Close"]) > float(first_candle["Open"])
        if not first_green:
            logger.info(f"  {symbol} ❌ Gap and Go: first candle is RED — opening weakness, skip")
            return None

        # Gate 5: Above VWAP
        df_5min = self.get_candles(symbol, resolution="5", count=5)
        time.sleep(0.15)
        vwap       = self.calculate_vwap(df_1min)
        ema9       = self.calculate_ema(df_1min, period=9)
        above_vwap = vwap > 0 and current_price > vwap
        above_ema9 = ema9 > 0 and current_price > ema9

        if not above_vwap:
            logger.info(f"  {symbol} ❌ Gap and Go: price below VWAP ${vwap:.2f}")
            return None

        # Pre-market extension check
        prev_close   = snap.get("prev_close", 0)
        pre_mkt_high = snap.get("high", current_price)
        extended     = False
        if prev_close and prev_close > 0:
            pre_mkt_move = ((pre_mkt_high - prev_close) / prev_close) * 100
            if pre_mkt_move >= self.GAP_GO_EXTENDED:
                extended = True
                logger.info(
                    f"  ⚠️  {symbol} Gap and Go: EXTENDED "
                    f"(pre-mkt move {pre_mkt_move:.0f}% — already ran hard)"
                )
            else:
                logger.info(f"  {symbol} Pre-mkt move: {pre_mkt_move:.0f}% (not extended)")

        # Float + position size
        float_m = self.get_float(symbol)
        avg_vol  = snap.get("volume", 0)
        shares   = self.calc_position_size(avg_vol, current_price)

        # Score (same formula but Gap and Go gets bonus for clean open)
        score  = min(rvol / self.MIN_RVOL, 4) * 35
        score += min(gap_pct / 10, 3) * 25
        score += 30  # news is confirmed (hard gate passed)
        score += 15 if above_vwap else 0
        score += 10 if above_ema9  else 0
        score += 15  # Gap and Go bonus — clean open candle
        if extended:
            score -= 20  # penalty for being already extended
        if 0 < float_m < 5:
            score += 20
        elif 0 < float_m < 10:
            score += 10

        enriched = {
            "symbol":       symbol,
            "price":        round(current_price, 2),
            "entry_price":  round(float(first_candle["High"]), 2),  # enter above first candle high
            "pct_change":   round(gap_pct, 1),
            "rvol":         rvol,
            "avg_vol":      avg_vol,
            "float":        float_m,
            "shares":       shares,
            "has_news":     True,
            "bull_flag":    False,   # Gap and Go — no flag needed
            "above_vwap":   above_vwap,
            "above_ema9":   above_ema9,
            "vwap":         round(vwap, 2),
            "ema9":         round(ema9, 2),
            "score":        round(score, 0),
            "alert_ready":  True,
            "alert_type":   "gap_and_go",
            "extended":     extended,
            "first_candle_green": True,
        }

        logger.info(
            f"  ✅ GAP AND GO: {symbol} | "
            f"+{gap_pct:.1f}% | RVOL:{rvol}x | "
            f"{'⚠️ EXTENDED' if extended else 'clean'} | Score:{score:.0f}"
        )
        return enriched

    # ------------------------------------------------------------------ #
    #  Full enrichment for one symbol                                      #
    # ------------------------------------------------------------------ #

    def _enrich_symbol(self, s: dict, snap: dict) -> dict | None:
        """
        Run full Ross Cameron enrichment on one symbol.
        Called for new symbols and for cache refreshes.
        Returns enriched dict or None if fails filters.
        """
        symbol    = s["symbol"]
        today_vol = snap.get("volume", 0)
        prev_vol  = snap.get("prev_volume", 0)

        logger.info(f"  🔍 Enriching {symbol} (vol today:{today_vol:,} prev:{prev_vol:,})")

        rvol, avg_vol = self.calculate_rvol(symbol, today_vol, prev_vol)
        if rvol < self.MIN_RVOL:
            logger.info(f"  {symbol} ❌ RVOL {rvol}x (need {self.MIN_RVOL}x)")
            return None

        # Float data
        float_m = self.get_float(symbol)

        # Candles
        df_1min = self.get_candles(symbol, resolution="1", count=100)
        time.sleep(0.15)
        df_5min = self.get_candles(symbol, resolution="5", count=50)
        time.sleep(0.15)

        # Technical indicators
        vwap  = self.calculate_vwap(df_1min) if df_1min is not None else snap.get("vwap", 0)
        ema9  = self.calculate_ema(df_1min, period=9) if df_1min is not None else 0

        current_price = s["price"]
        above_vwap    = vwap > 0 and current_price > vwap
        above_ema9    = ema9 > 0 and current_price > ema9

        # Bull flag
        bf_1min, entry_1min = self.detect_bull_flag(df_1min, "1min")
        bf_5min, entry_5min = self.detect_bull_flag(df_5min, "5min")
        bull_flag   = bf_1min or bf_5min
        entry_price = entry_1min if bf_1min else (entry_5min if bf_5min else current_price)

        # News
        has_news = self.has_news_today(symbol)

        # Volume-scaled position size
        shares = self.calc_position_size(avg_vol, current_price)

        # Score
        score  = min(rvol / self.MIN_RVOL, 4) * 35
        score += min(s["change_pct"] / 10, 3) * 25
        score += 30 if has_news   else 0
        score += 25 if bull_flag  else 0
        score += 15 if above_vwap else 0
        score += 10 if above_ema9 else 0
        # Float bonus — Ross loves under 5M float
        if 0 < float_m < 5:
            score += 20
        elif 0 < float_m < 10:
            score += 10

        # ── Alert readiness — Ross Cameron hard gates ──────────────────
        # News gate has a high-RVOL override:
        # If RVOL >= 50x the volume itself IS the signal (Ross trades these)
        # Below 50x RVOL, news is required
        news_required = self.ALERT_REQUIRE_NEWS and rvol < 50.0

        alert_reasons_failed = []
        if self.ALERT_REQUIRE_BULL_FLAG and not bull_flag:
            alert_reasons_failed.append("no bull flag")
        if news_required and not has_news:
            alert_reasons_failed.append("no news (RVOL<50x)")
        if rvol < self.ALERT_MIN_RVOL:
            alert_reasons_failed.append(f"RVOL {rvol}x < {self.ALERT_MIN_RVOL}x")
        if not above_vwap:
            alert_reasons_failed.append("below VWAP")

        alert_ready = len(alert_reasons_failed) == 0

        if not alert_ready:
            logger.info(f"  ⚠️  {symbol} WATCHLIST — blocked: {', '.join(alert_reasons_failed)}")
        elif rvol >= 50.0 and not has_news:
            logger.info(f"  ✅ {symbol} ALERT READY — high RVOL {rvol}x overrides news gate")

        enriched = {
            "symbol":      symbol,
            "price":       round(current_price, 2),
            "entry_price": round(entry_price, 2),
            "pct_change":  round(s["change_pct"], 1),
            "rvol":        rvol,
            "avg_vol":     avg_vol,
            "float":       float_m,
            "shares":      shares,
            "has_news":    has_news,
            "bull_flag":   bull_flag,
            "above_vwap":  above_vwap,
            "above_ema9":  above_ema9,
            "vwap":        round(vwap, 2),
            "ema9":        round(ema9, 2),
            "score":       round(score, 0),
            "alert_ready": alert_ready,
            "alert_type":  "bull_flag",  # distinguish from gap_and_go
            "extended":    False,
        }

        status   = "🚨 ALERT READY" if alert_ready else "📋 WATCHLIST"
        bf_str   = "🚩" if bull_flag  else "  "
        news_str = "📰" if has_news   else "  "
        vwap_str = "📈" if above_vwap else "📉"
        logger.info(
            f"  {status} {bf_str}{news_str}{vwap_str} {symbol} | "
            f"+{s['change_pct']:.1f}% | RVOL:{rvol}x | "
            f"Float:{float_m}M | Score:{score:.0f}"
        )
        return enriched

    # ------------------------------------------------------------------ #
    #  Main scan — with pre-market cache + smart enrichment               #
    # ------------------------------------------------------------------ #

    def scan_for_momentum(self):
        """
        v20 scan with three improvements:
        1. Pre-market cache — builds watchlist 6:30-9:30, fires instantly at open
        2. Smart enrichment — only re-enriches new symbols, caches returning ones
        3. Volume-scaled sizing — returned with each qualified stock
        """
        logger.info("🔄 VISION v22 — Ross Cameron scan starting...")
        self._scan_count += 1

        et  = pytz.timezone("America/New_York")
        now = datetime.now(et)
        is_premarket   = now.hour < 9 or (now.hour == 9 and now.minute < 30)
        is_gap_and_go  = (now.hour == 9 and 30 <= now.minute <= 35)  # first 5 min only

        # Step 1: Get movers from Alpaca
        universe = self.get_alpaca_movers()
        if not universe:
            logger.warning("⚠️ Alpaca returned no movers")
            # During prime window, return cached watchlist if available
            if not is_premarket and self._watchlist_cache:
                logger.info(f"  Using cached watchlist ({len(self._watchlist_cache)} stocks)")
                cached = sorted(
                    self._watchlist_cache.values(),
                    key=lambda x: x["score"], reverse=True
                )
                return cached[:10]
            return []

        # Step 2: Price + gap filter
        filtered = []
        for s in universe:
            price = s.get("price", 0)
            gap   = s.get("change_pct", 0)
            if price < self.MIN_PRICE or price > self.MAX_PRICE:
                continue
            if gap < self.MIN_GAP:
                continue
            filtered.append(s)

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed filters")
            if not is_premarket and self._watchlist_cache:
                logger.info(f"  Falling back to watchlist cache")
                cached = sorted(
                    self._watchlist_cache.values(),
                    key=lambda x: x["score"], reverse=True
                )
                return cached[:10]
            return []

        # Step 3: Batch snapshots for all filtered symbols
        symbols   = [s["symbol"] for s in filtered[:20]]
        snapshots = self.get_alpaca_snapshots(symbols)

        # Step 4: Smart enrichment
        # New symbols = not in cache → full enrichment
        # Returning symbols = in cache → use cache unless refresh due
        current_symbols = set(symbols)
        new_symbols     = current_symbols - set(self._watchlist_cache.keys())
        old_symbols     = current_symbols & set(self._watchlist_cache.keys())

        logger.info(
            f"  📋 New symbols: {len(new_symbols)} | "
            f"Cached: {len(old_symbols)} | "
            f"Scan #{self._scan_count}"
        )

        qualified = []

        # Enrich new symbols fully
        for s in filtered[:20]:
            sym  = s["symbol"]
            snap = snapshots.get(sym, {})

            if sym in new_symbols:
                enriched = self._enrich_symbol(s, snap)
                if enriched:
                    self._watchlist_cache[sym] = enriched
                    qualified.append(enriched)

            elif sym in old_symbols:
                cached = self._watchlist_cache[sym]
                # Check if refresh needed (every N scans)
                scans_since = self._scan_count - cached.get("cached_scan", 0)

                if scans_since >= CACHE_REFRESH_SCANS:
                    # Re-enrich with fresh data
                    logger.info(f"  🔄 Refreshing cache for {sym}")
                    enriched = self._enrich_symbol(s, snap)
                    if enriched:
                        enriched["cached_scan"] = self._scan_count
                        self._watchlist_cache[sym] = enriched
                        qualified.append(enriched)
                    else:
                        # Failed RVOL on refresh — remove from cache
                        del self._watchlist_cache[sym]
                else:
                    # Use cached data but update price
                    cached["price"] = round(s["price"], 2)
                    logger.info(
                        f"  ✓ {sym} cached (score:{cached['score']} "
                        f"rvol:{cached['rvol']}x bull_flag:{cached['bull_flag']})"
                    )
                    qualified.append(cached)

        # Remove symbols no longer in movers list from cache
        stale = set(self._watchlist_cache.keys()) - current_symbols
        for sym in stale:
            logger.info(f"  🗑 {sym} no longer in movers — removing from cache")
            del self._watchlist_cache[sym]

        qualified.sort(key=lambda x: x["score"], reverse=True)
        top_10 = qualified[:10]

        # ── Gap and Go pass (9:30–9:35 AM only) ──────────────────────
        # Run separately — checks first candle green + news + RVOL on all
        # filtered stocks. Fires immediately without waiting for bull flag.
        gap_go_candidates = []
        if is_gap_and_go and not is_premarket:
            logger.info("⚡ GAP AND GO WINDOW — checking all filtered stocks")
            for s in filtered[:20]:
                sym  = s["symbol"]
                snap = snapshots.get(sym, {})

                # Need RVOL — use cached if available, else quick calc
                cached_entry = self._watchlist_cache.get(sym, {})
                rvol = cached_entry.get("rvol", 0)
                avg_vol = cached_entry.get("avg_vol", 0)

                if rvol == 0:
                    # Quick RVOL calc using snapshot data
                    today_vol = snap.get("volume", 0)
                    prev_vol  = snap.get("prev_volume", 0)
                    if prev_vol > 0:
                        rvol = round(today_vol / prev_vol, 1)
                        avg_vol = prev_vol

                result = self.check_gap_and_go(s, snap, rvol)
                if result:
                    gap_go_candidates.append(result)

            if gap_go_candidates:
                logger.info(f"  ⚡ {len(gap_go_candidates)} Gap and Go setup(s) found")
                # Merge into top_10, gap_and_go at the front
                gap_go_syms = {g["symbol"] for g in gap_go_candidates}
                top_10 = gap_go_candidates + [s for s in top_10 if s["symbol"] not in gap_go_syms]
                top_10 = top_10[:10]

        mode = "PRE-MARKET WATCHLIST BUILD" if is_premarket else "MARKET HOURS"
        logger.info(
            f"🎯 [{mode}] Scan complete — "
            f"{len(qualified)} qualified, top {len(top_10)} selected"
        )
        for i, s in enumerate(top_10):
            bf = "🚩" if s["bull_flag"] else ""
            logger.info(
                f"  {i+1}. ${s['symbol']} {bf} | +{s['pct_change']}% | "
                f"RVOL:{s['rvol']}x | Float:{s['float']}M | "
                f"Shares:{s.get('shares', 100)} | Score:{s['score']}"
            )
        return top_10
