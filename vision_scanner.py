"""
VISION Scanner v8 — Ross Cameron 5 Pillar Methodology
======================================================
Fix from v7: RVOL was returning 0 for all stocks because Finnhub daily
candles don't return data for many small/micro-cap tickers on free tier.

v8 fix: Use Finnhub's /stock/metric endpoint which returns
'10DayAverageTradingVolume' and '3MonthAverageTradingVolume' — these
work reliably on free tier. Compare today's volume (from quote) against
that average to get accurate RVOL.
"""

import pandas as pd
import numpy as np
import requests
import os
import logging
import time
from datetime import date
from bs4 import BeautifulSoup

logger = logging.getLogger("VISION_SCANNER")

FINVIZ_URL = (
    "https://finviz.com/screener.ashx"
    "?v=111"
    "&f=sh_float_u10,sh_price_1to20,ta_changeopen_u5"
    "&o=-change"
    "&r=1"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL    = 5.0
        self.MIN_GAP     = 10.0
        self.MIN_PRICE   = 1.0
        self.MAX_PRICE   = 20.0
        self.MAX_FLOAT_M = 10.0
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")

    # ------------------------------------------------------------------ #
    #  Finviz — dynamic universe                                          #
    # ------------------------------------------------------------------ #

    def get_top_gainers_finviz(self):
        """Pull top gaining low-float small caps from Finviz screener."""
        results = []
        try:
            resp = requests.get(FINVIZ_URL, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Finviz returned {resp.status_code}")
                return results

            soup = BeautifulSoup(resp.text, "html.parser")

            # Find all ticker links — most reliable way to extract tickers
            ticker_links = soup.select("a.screener-link-primary")
            if not ticker_links:
                ticker_links = soup.select("td a[href*='quote.ashx']")

            seen = set()
            for link in ticker_links:
                ticker = link.get_text(strip=True)
                if not ticker or len(ticker) > 6 or ticker in seen:
                    continue
                seen.add(ticker)

                # Walk up to the row to get price/change
                row = link.find_parent("tr")
                if not row:
                    results.append({"symbol": ticker, "price": 0, "change_pct": 0, "volume": 0})
                    continue

                cells = row.find_all("td")
                cell_texts = [c.get_text(strip=True) for c in cells]

                price = 0.0
                change_pct = 0.0
                volume = 0

                for text in cell_texts:
                    # Price: looks like "3.45"
                    if price == 0:
                        try:
                            v = float(text.replace(",", "").replace("$", ""))
                            if 0.5 < v < 25:
                                price = v
                        except Exception:
                            pass
                    # Change: looks like "15.23%"
                    if "%" in text and change_pct == 0:
                        try:
                            change_pct = float(text.replace("%", "").replace(",", ""))
                        except Exception:
                            pass
                    # Volume: looks like "1.2M" or "500K"
                    if volume == 0 and ("M" in text or "K" in text):
                        volume = self._parse_volume(text)

                results.append({
                    "symbol":     ticker,
                    "price":      price,
                    "change_pct": change_pct,
                    "volume":     volume,
                })

        except Exception as e:
            logger.warning(f"Finviz fetch error: {e}")

        logger.info(f"📋 Finviz returned {len(results)} candidates")
        return results

    def _parse_volume(self, vol_str):
        try:
            vol_str = vol_str.replace(",", "").strip()
            if "M" in vol_str:
                return int(float(vol_str.replace("M", "")) * 1_000_000)
            elif "K" in vol_str:
                return int(float(vol_str.replace("K", "")) * 1_000)
            else:
                return int(float(vol_str)) if vol_str else 0
        except Exception:
            return 0

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

    def calculate_rvol(self, symbol, today_volume):
        """
        RVOL = today's volume / 10-day average volume.
        Uses Finnhub /stock/metric which reliably returns avg volume
        even for small caps on free tier.
        Falls back to intraday volume ratio if metric unavailable.
        """
        # Primary: use Finnhub basic metrics
        data = self._finnhub_get("stock/metric", {"symbol": symbol, "metric": "all"})
        time.sleep(0.15)

        if data and data.get("metric"):
            m = data["metric"]
            # Try 10-day avg first, then 3-month avg
            avg_vol = m.get("10DayAverageTradingVolume") or m.get("3MonthAverageTradingVolume")
            if avg_vol and avg_vol > 0:
                # avg_vol from Finnhub is in millions — convert
                avg_vol_shares = avg_vol * 1_000_000
                rvol = today_volume / avg_vol_shares if avg_vol_shares > 0 else 0
                logger.info(f"  {symbol} RVOL: {rvol:.1f}x (today:{today_volume:,} / avg:{avg_vol_shares:,.0f})")
                return round(rvol, 1)

        # Fallback: use Finnhub quote which has today's volume
        quote = self._finnhub_get("quote", {"symbol": symbol})
        time.sleep(0.15)
        if quote and quote.get("v") and quote.get("av"):
            today_vol = quote["v"]    # current volume
            avg_vol   = quote["av"]   # average volume
            if avg_vol > 0:
                rvol = today_vol / avg_vol
                logger.info(f"  {symbol} RVOL (quote fallback): {rvol:.1f}x")
                return round(rvol, 1)

        logger.info(f"  {symbol} RVOL: could not calculate")
        return 0

    def get_candles(self, symbol, resolution="5", count=80):
        """Get intraday 5-min candles for reversal detection."""
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
        """Check for news catalyst today — Ross Pillar 3."""
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
        logger.info("🔄 VISION v8 — Ross Cameron 5 Pillar scan starting...")

        # Step 1: Dynamic universe from Finviz
        candidates_raw = self.get_top_gainers_finviz()
        if not candidates_raw:
            logger.warning("⚠️ Finviz returned no results")
            return []

        # Step 2: Quick price + gap filter
        filtered = []
        for s in candidates_raw:
            if s["price"] < self.MIN_PRICE or s["price"] > self.MAX_PRICE:
                continue
            if s["change_pct"] < self.MIN_GAP:
                continue
            filtered.append(s)
            logger.info(f"  {s['symbol']} ✓ ${s['price']} | +{s['change_pct']:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed initial filters — market may be slow today")
            return []

        # Step 3: Enrich with Finnhub
        qualified = []
        for s in filtered[:20]:
            symbol = s["symbol"]
            logger.info(f"  🔍 Enriching {symbol}...")

            # Pillar 1: RVOL (use Finviz volume as today's volume seed)
            rvol = self.calculate_rvol(symbol, s.get("volume", 0))
            if rvol < self.MIN_RVOL:
                logger.info(f"  {symbol} ❌ RVOL {rvol}x (need {self.MIN_RVOL}x)")
                continue

            # Pillar 3: News
            has_news = self.has_news_today(symbol)

            # Reversal pattern
            df = self.get_candles(symbol)
            time.sleep(0.15)
            reversal = self.detect_reversal(df)

            # Score
            score  = min(rvol / self.MIN_RVOL, 4) * 35
            score += min(s["change_pct"] / 10, 3) * 25
            score += 30 if has_news else 0
            score += 20 if reversal else 0

            qualified.append({
                "symbol":     symbol,
                "price":      round(s["price"], 2),
                "pct_change": round(s["change_pct"], 1),
                "rvol":       rvol,
                "float":      s.get("float_m", 0),
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
