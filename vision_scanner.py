"""
VISION Scanner v12 — Ross Cameron 5 Pillar Methodology
=======================================================
Universe discovery: Financial Modeling Prep (FMP)
- /v3/gainers  → pre-market & morning gap-up stocks (Phase 1)
- /v3/actives  → intraday high volume movers (Phase 2 HOD)
Enrichment: Finnhub
- RVOL, candles, news catalyst (same as before)

FMP is a proper REST API — no scraping, no Cloudflare, works
perfectly from Render cloud servers.

Two-phase approach matching Ross Cameron's actual workflow:
Phase 1 (pre-market to 9:30 AM): Focus on gap-up stocks
Phase 2 (9:30 AM onward): Focus on intraday HOD movers
"""

import pandas as pd
import requests
import os
import logging
import time
import re
from datetime import date, datetime
import pytz

logger = logging.getLogger("VISION_SCANNER")

# Valid ticker regex
TICKER_RE = re.compile(r'^[A-Z]{1,5}$')

FMP_BASE = "https://financialmodelingprep.com/api/v3"


class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL    = 5.0
        self.MIN_GAP     = 10.0
        self.MIN_PRICE   = 1.0
        self.MAX_PRICE   = 20.0
        self.fmp_key     = os.environ.get("FMP_API_KEY", "")
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")

    # ------------------------------------------------------------------ #
    #  FMP — universe discovery                                           #
    # ------------------------------------------------------------------ #

    def _fmp_get(self, endpoint):
        """Generic FMP API caller."""
        if not self.fmp_key:
            logger.error("FMP_API_KEY not set")
            return None
        url = f"{FMP_BASE}/{endpoint}?apikey={self.fmp_key}"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning("FMP rate limit hit")
            else:
                logger.warning(f"FMP {endpoint} returned {r.status_code}")
            return None
        except Exception as e:
            logger.warning(f"FMP error [{endpoint}]: {e}")
            return None

    def get_fmp_gainers(self):
        """
        Phase 1: Top gaining stocks right now from FMP.
        Returns list of symbols that are up big today.
        """
        data = self._fmp_get("gainers")
        if not data:
            return []
        symbols = []
        for item in data:
            sym = item.get("ticker") or item.get("symbol", "")
            if TICKER_RE.match(sym):
                symbols.append({
                    "symbol":     sym,
                    "price":      float(item.get("price", 0)),
                    "change_pct": float(item.get("changesPercentage", 0)),
                })
        logger.info(f"📈 FMP gainers returned {len(symbols)} stocks")
        return symbols

    def get_fmp_actives(self):
        """
        Phase 2: Most active stocks by volume from FMP.
        Used after 9:30 AM to catch HOD intraday movers.
        """
        data = self._fmp_get("actives")
        if not data:
            return []
        symbols = []
        for item in data:
            sym = item.get("ticker") or item.get("symbol", "")
            if TICKER_RE.match(sym):
                symbols.append({
                    "symbol":     sym,
                    "price":      float(item.get("price", 0)),
                    "change_pct": float(item.get("changesPercentage", 0)),
                })
        logger.info(f"🔥 FMP actives returned {len(symbols)} stocks")
        return symbols

    # ------------------------------------------------------------------ #
    #  Finnhub — enrichment                                               #
    # ------------------------------------------------------------------ #

    def _finnhub_get(self, endpoint, params=None):
        if not self.finnhub_key:
            return None
        url = f"https://finnhub.io/api/v1/{endpoint}"
        p = {"token": self.finnhub_key}
        if params:
            p.update(params)
        try:
            r = requests.get(url, params=p, timeout=10)
            if r.status_code == 200 and r.text.strip():
                return r.json()
            elif r.status_code == 429:
                logger.warning("Finnhub rate limit — sleeping 10s")
                time.sleep(10)
            return None
        except Exception as e:
            logger.warning(f"Finnhub [{endpoint}] error: {e}")
            return None

    def get_quote(self, symbol):
        """Finnhub quote for volume data."""
        data = self._finnhub_get("quote", {"symbol": symbol})
        time.sleep(0.15)
        if not data or data.get("c", 0) == 0:
            return None
        return {
            "price":      data.get("c", 0),
            "prev_close": data.get("pc", 0),
            "volume":     data.get("v", 0),
            "avg_volume": data.get("av", 0),
        }

    def calculate_rvol(self, symbol, today_volume, avg_volume):
        """RVOL using Finnhub 10-day avg volume metric."""
        data = self._finnhub_get("stock/metric", {"symbol": symbol, "metric": "all"})
        time.sleep(0.15)
        if data and data.get("metric"):
            m = data["metric"]
            avg_vol_m = m.get("10DayAverageTradingVolume") or m.get("3MonthAverageTradingVolume")
            if avg_vol_m and avg_vol_m > 0:
                avg_vol_shares = avg_vol_m * 1_000_000
                rvol = today_volume / avg_vol_shares
                logger.info(f"  {symbol} RVOL: {rvol:.1f}x (today:{today_volume:,} / 10d-avg:{avg_vol_shares:,.0f})")
                return round(rvol, 1)
        if avg_volume and avg_volume > 0:
            rvol = today_volume / avg_volume
            logger.info(f"  {symbol} RVOL (quote fallback): {rvol:.1f}x")
            return round(rvol, 1)
        return 0

    def get_candles(self, symbol, resolution="5", count=80):
        """Intraday candles for reversal detection."""
        now = int(time.time())
        lookback = count * int(resolution) * 60 * 2
        data = self._finnhub_get("stock/candle", {
            "symbol": symbol,
            "resolution": resolution,
            "from": now - lookback,
            "to": now
        })
        if not data or data.get("s") != "ok":
            return None
        try:
            return pd.DataFrame({
                "Open":   data["o"],
                "High":   data["h"],
                "Low":    data["l"],
                "Close":  data["c"],
                "Volume": data["v"],
            })
        except Exception:
            return None

    def has_news_today(self, symbol):
        """Ross Pillar 3 — news catalyst."""
        today = date.today().strftime("%Y-%m-%d")
        data = self._finnhub_get("company-news", {
            "symbol": symbol,
            "_from": today,
            "to": today
        })
        time.sleep(0.15)
        has = bool(data and len(data) > 0)
        if has:
            logger.info(f"  📰 {symbol} has {len(data)} news item(s) today")
        return has

    def detect_reversal(self, df):
        if df is None or len(df) < 3:
            return False
        last, prev = df.iloc[-1], df.iloc[-2]
        if last["Close"] > prev["High"] and last["Volume"] > prev["Volume"] * 1.5:
            return True
        if len(df) > 10:
            low_zone = df["Low"].iloc[-10:].min()
            if last["Low"] <= low_zone * 1.01 and last["Close"] > last["Open"] * 1.02:
                return True
        return False

    # ------------------------------------------------------------------ #
    #  Main scan                                                          #
    # ------------------------------------------------------------------ #

    def scan_for_momentum(self):
        """
        Ross Cameron two-phase scan:
        Phase 1 (before 9:30 AM ET): gainers = gap-up watchlist
        Phase 2 (9:30 AM+ ET): actives = HOD intraday movers
        Both phases then enriched with Finnhub RVOL + news
        """
        logger.info("🔄 VISION v12 — Ross Cameron 5 Pillar scan starting...")

        if not self.fmp_key:
            logger.error("❌ FMP_API_KEY not set — cannot scan")
            return []

        # Determine phase based on time
        et = pytz.timezone("America/New_York")
        now_et = datetime.now(et)
        is_premarket = now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30)

        if is_premarket:
            logger.info("📋 Phase 1 — Pre-market: fetching gap-up stocks from FMP")
            universe = self.get_fmp_gainers()
        else:
            logger.info("📋 Phase 2 — Market hours: fetching top gainers + actives from FMP")
            gainers = self.get_fmp_gainers()
            actives = self.get_fmp_actives()
            # Merge and deduplicate — gainers take priority
            seen = set()
            universe = []
            for s in gainers + actives:
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    universe.append(s)

        if not universe:
            logger.warning("⚠️ FMP returned no stocks")
            return []

        # Apply price + gap filters
        filtered = []
        for s in universe:
            price = s.get("price", 0)
            gap = s.get("change_pct", 0)

            if price < self.MIN_PRICE or price > self.MAX_PRICE:
                logger.debug(f"  {s['symbol']} ❌ price ${price:.2f}")
                continue
            if gap < self.MIN_GAP:
                logger.debug(f"  {s['symbol']} ❌ gap {gap:.1f}%")
                continue

            filtered.append(s)
            logger.info(f"  {s['symbol']} ✓ ${price:.2f} | +{gap:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed filters — market may be slow today")
            return []

        # Enrich with Finnhub
        qualified = []
        for s in filtered[:20]:
            symbol = s["symbol"]
            logger.info(f"  🔍 Enriching {symbol}...")

            # Get volume from Finnhub quote
            quote = self.get_quote(symbol)
            if not quote:
                continue

            today_vol = quote["volume"]
            avg_vol   = quote["avg_volume"]

            rvol = self.calculate_rvol(symbol, today_vol, avg_vol)
            if rvol < self.MIN_RVOL:
                logger.info(f"  {symbol} ❌ RVOL {rvol}x (need {self.MIN_RVOL}x)")
                continue

            has_news = self.has_news_today(symbol)
            df = self.get_candles(symbol)
            time.sleep(0.15)
            reversal = self.detect_reversal(df)

            score  = min(rvol / self.MIN_RVOL, 4) * 35
            score += min(s["change_pct"] / 10, 3) * 25
            score += 30 if has_news else 0
            score += 20 if reversal else 0

            qualified.append({
                "symbol":     symbol,
                "price":      round(s["price"], 2),
                "pct_change": round(s["change_pct"], 1),
                "rvol":       rvol,
                "float":      0,
                "has_news":   has_news,
                "reversal":   reversal,
                "score":      round(score, 0)
            })
            logger.info(
                f"  ✅ {symbol} QUALIFIED | +{s['change_pct']:.1f}% | "
                f"RVOL:{rvol}x | News:{'YES' if has_news else 'no'} | Score:{score:.0f}"
            )

        qualified.sort(key=lambda x: x["score"], reverse=True)
        top_10 = qualified[:10]

        logger.info(f"🎯 Scan complete — {len(qualified)} qualified, top {len(top_10)} selected")
        for i, s in enumerate(top_10):
            logger.info(
                f"  {i+1}. ${s['symbol']} | +{s['pct_change']}% | "
                f"RVOL:{s['rvol']}x | News:{'✓' if s['has_news'] else '✗'} | Score:{s['score']}"
            )
        return top_10
