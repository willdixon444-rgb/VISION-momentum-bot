"""
VISION Scanner v13 — Ross Cameron 5 Pillar Methodology
=======================================================
Universe discovery: Alpaca Market Movers API (free, cloud-friendly)
- /v1beta1/screener/stocks/movers → top gaining stocks right now
Enrichment: Finnhub
- RVOL, candles, news catalyst

Alpaca is a proper REST API — no scraping, no Cloudflare,
works perfectly from Render cloud servers.
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

TICKER_RE = re.compile(r'^[A-Z]{1,5}$')

# Alpaca paper trading base URL
ALPACA_BASE = "https://paper-api.alpaca.markets/v2"
ALPACA_DATA_BASE = "https://data.alpaca.markets"


class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL      = 5.0
        self.MIN_GAP       = 10.0
        self.MIN_PRICE     = 1.0
        self.MAX_PRICE     = 20.0
        self.alpaca_key    = os.environ.get("ALPACA_API_KEY", "")
        self.alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.finnhub_key   = os.environ.get("FINNHUB_API_KEY", "")

    # ------------------------------------------------------------------ #
    #  Alpaca — universe discovery                                        #
    # ------------------------------------------------------------------ #

    def _alpaca_headers(self):
        return {
            "APCA-API-KEY-ID":     self.alpaca_key,
            "APCA-API-SECRET-KEY": self.alpaca_secret,
            "Accept": "application/json"
        }

    def get_alpaca_movers(self):
        """
        Get top gaining stocks from Alpaca's market movers endpoint.
        Returns list of {symbol, price, change_pct}
        Free with any Alpaca account — works from cloud servers.
        """
        if not self.alpaca_key or not self.alpaca_secret:
            logger.error("❌ ALPACA_API_KEY or ALPACA_SECRET_KEY not set")
            return []

        try:
            url = f"{ALPACA_DATA_BASE}/v1beta1/screener/stocks/movers"
            params = {"top": 50}
            r = requests.get(url, headers=self._alpaca_headers(), params=params, timeout=10)

            if r.status_code == 200:
                data = r.json()
                gainers = data.get("gainers", [])
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
                logger.warning(f"Alpaca movers returned {r.status_code}: {r.text[:200]}")
                return []

        except Exception as e:
            logger.warning(f"Alpaca movers error: {e}")
            return []

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
        """Ross Cameron 5 Pillar scan using Alpaca movers + Finnhub enrichment."""
        logger.info("🔄 VISION v13 — Ross Cameron 5 Pillar scan starting...")

        # Step 1: Get top movers from Alpaca
        universe = self.get_alpaca_movers()

        if not universe:
            logger.warning("⚠️ Alpaca returned no movers")
            return []

        # Step 2: Price + gap filter
        filtered = []
        for s in universe:
            price = s.get("price", 0)
            gap   = s.get("change_pct", 0)

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

        # Step 3: Finnhub enrichment — RVOL, news, candles
        qualified = []
        for s in filtered[:20]:
            symbol = s["symbol"]
            logger.info(f"  🔍 Enriching {symbol}...")

            quote = self.get_quote(symbol)
            if not quote:
                continue

            rvol = self.calculate_rvol(symbol, quote["volume"], quote["avg_volume"])
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
