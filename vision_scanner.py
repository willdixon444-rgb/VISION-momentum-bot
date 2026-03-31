"""
VISION Scanner v10 — Ross Cameron 5 Pillar Methodology
=======================================================
Fix from v9: Finviz blocks cloud server IPs (Render/AWS etc.)
returning empty pages — same issue as Yahoo Finance.

v10 fix: Two-pronged approach:
1. Better Finviz request with full session/cookies/headers
2. Fallback to Finnhub's /stock/market-status + quote polling
   of a curated small-cap universe if Finviz is still blocked
"""

import pandas as pd
import requests
import os
import logging
import time
import re
from datetime import date
from bs4 import BeautifulSoup

logger = logging.getLogger("VISION_SCANNER")

# Finviz screener — low float <10M, price $1-$20, gap up 5%+, sorted by change
FINVIZ_URL = (
    "https://finviz.com/screener.ashx"
    "?v=111"
    "&f=sh_float_u10,sh_price_1to20,ta_changeopen_u5"
    "&o=-change"
    "&r=1"
)

# Realistic browser session headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

# Valid ticker: 1-5 uppercase letters only
TICKER_RE = re.compile(r'^[A-Z]{1,5}$')

# Fallback small-cap watchlist — used if Finviz is blocked
# These are known frequent Ross Cameron-style movers
FALLBACK_WATCHLIST = [
    "ASTS", "QUBT", "LUNR", "RKLB", "AISP", "HOLO", "CECO", "MOBX",
    "MARA", "RIOT", "CIFR", "HUT", "BITF", "BTBT",
    "MULN", "LCID", "RIVN", "GOEV", "NKLA", "SOLO",
    "OCGN", "HRTX", "SNDL", "MVIS", "ATER", "CLOV", "SPCE",
    "HOOD", "OPEN", "FUBO", "FFIE", "GFAI", "KPLT",
    "ABML", "IMPP", "PROG", "HYMC", "LMND", "PSFE"
]


class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL    = 5.0
        self.MIN_GAP     = 10.0
        self.MIN_PRICE   = 1.0
        self.MAX_PRICE   = 20.0
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
        self.session     = self._make_session()

    def _make_session(self):
        """Create a requests session that mimics a real browser."""
        s = requests.Session()
        s.headers.update(HEADERS)
        # Warm up the session by hitting Finviz homepage first
        try:
            s.get("https://finviz.com", timeout=10)
            time.sleep(1)
        except Exception:
            pass
        return s

    # ------------------------------------------------------------------ #
    #  Finviz — dynamic universe                                          #
    # ------------------------------------------------------------------ #

    def get_top_gainers_finviz(self):
        """
        Scrape Finviz for top gaining low-float small caps.
        Uses a warmed-up session with realistic browser headers
        to avoid cloud IP blocking.
        """
        tickers = []
        try:
            resp = self.session.get(FINVIZ_URL, timeout=15)
            logger.info(f"Finviz status: {resp.status_code} | content length: {len(resp.text)}")

            if resp.status_code != 200:
                logger.warning(f"Finviz returned {resp.status_code}")
                return tickers

            soup = BeautifulSoup(resp.text, "html.parser")

            # Primary: screener-link-primary class (ticker links)
            links = soup.select("a.screener-link-primary")
            logger.info(f"Finviz screener-link-primary found: {len(links)}")

            if not links:
                # Fallback: any link with quote.ashx in href
                links = soup.select("a[href*='quote.ashx']")
                logger.info(f"Finviz quote links found: {len(links)}")

            seen = set()
            for link in links:
                ticker = link.get_text(strip=True)
                if TICKER_RE.match(ticker) and ticker not in seen:
                    seen.add(ticker)
                    tickers.append(ticker)

        except Exception as e:
            logger.warning(f"Finviz fetch error: {e}")

        logger.info(f"📋 Finviz returned {len(tickers)} valid tickers: {tickers[:10]}")
        return tickers

    # ------------------------------------------------------------------ #
    #  Finnhub helpers                                                    #
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
        """Get current price, change%, volume from Finnhub."""
        data = self._finnhub_get("quote", {"symbol": symbol})
        time.sleep(0.15)
        if not data or data.get("c", 0) == 0:
            return None
        return {
            "price":      data.get("c", 0),
            "prev_close": data.get("pc", 0),
            "open":       data.get("o", 0),
            "volume":     data.get("v", 0),
            "avg_volume": data.get("av", 0),
        }

    def calculate_rvol(self, symbol, today_volume, avg_volume):
        """RVOL using Finnhub /stock/metric 10-day avg volume."""
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
        """Ross Pillar 3 — news catalyst check."""
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
        """Ross Cameron 5 Pillar scan."""
        logger.info("🔄 VISION v10 — Ross Cameron 5 Pillar scan starting...")

        # Step 1: Try Finviz for dynamic universe
        tickers = self.get_top_gainers_finviz()

        # Step 2: Fall back to watchlist + Finnhub quotes if Finviz blocked
        if not tickers:
            logger.info("⚠️ Finviz blocked — using Finnhub quote scan on fallback watchlist")
            tickers = FALLBACK_WATCHLIST

        # Step 3: Fetch quotes and apply price + gap filters
        filtered = []
        for symbol in tickers[:30]:
            quote = self.get_quote(symbol)
            if not quote:
                continue

            price = quote["price"]
            prev_close = quote["prev_close"]
            gap_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

            if price < self.MIN_PRICE or price > self.MAX_PRICE:
                logger.debug(f"  {symbol} ❌ price ${price:.2f}")
                continue
            if gap_pct < self.MIN_GAP:
                logger.debug(f"  {symbol} ❌ gap {gap_pct:.1f}%")
                continue

            filtered.append({
                "symbol":     symbol,
                "price":      price,
                "change_pct": round(gap_pct, 1),
                "volume":     quote["volume"],
                "avg_volume": quote["avg_volume"],
            })
            logger.info(f"  {symbol} ✓ ${price:.2f} | +{gap_pct:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed filters — market may be slow today")
            return []

        # Step 4: RVOL + news + reversal
        qualified = []
        for s in filtered[:20]:
            symbol = s["symbol"]
            logger.info(f"  🔍 Enriching {symbol}...")

            rvol = self.calculate_rvol(symbol, s["volume"], s["avg_volume"])
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
                "pct_change": s["change_pct"],
                "rvol":       rvol,
                "float":      0,
                "has_news":   has_news,
                "reversal":   reversal,
                "score":      round(score, 0)
            })
            logger.info(
                f"  ✅ {symbol} QUALIFIED | +{s['change_pct']}% | "
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
