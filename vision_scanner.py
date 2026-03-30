"""
VISION Scanner v7 — Ross Cameron 5 Pillar Methodology
======================================================
Pillar 1: RVOL >= 5x (vs 30-day average)
Pillar 2: Up 10%+ from previous close (gap from prev close, not open)
Pillar 3: News catalyst today
Pillar 4: Price $1–$20
Pillar 5: Float <= 10M shares (under 5M = priority)

Key change from v6: No static watchlist.
We dynamically pull top gainers from Finviz every scan cycle,
just like Ross's scanner does — finding ANY stock in the market
that is gapping up hard today, regardless of name.
"""

import pandas as pd
import numpy as np
import requests
import os
import logging
import time
from datetime import datetime, date
from bs4 import BeautifulSoup

logger = logging.getLogger("VISION_SCANNER")

# Finviz screener URL — Ross Cameron 5 Pillar filters baked in:
# - Price $1-$20 (ta_highlow20_a0to5 = within 0-5% of 20-day high, not needed)
# - Gap up 5%+ from prev close (gap_u5 = gap up 5%+)
# - Float under 10M (sh_float_u10 = float under 10M)
# - Price between $1 and $20 (sh_price_1to20)
# - Exchange: NYSE + NASDAQ only (exch_nasd + exch_nyse handled by default)
# - Sorted by % change descending (-change)
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
        # Ross Cameron 5 Pillars — exact criteria
        self.MIN_RVOL       = 5.0        # Pillar 1: 5x relative volume
        self.MIN_GAP        = 10.0       # Pillar 2: up 10%+ from prev close
        self.MIN_PRICE      = 1.0        # Pillar 4
        self.MAX_PRICE      = 20.0       # Pillar 4
        self.MAX_FLOAT_M    = 10.0       # Pillar 5: under 10M shares (in millions)
        self.finnhub_key    = os.environ.get("FINNHUB_API_KEY", "")

    # ------------------------------------------------------------------ #
    #  STEP 1 — Get dynamic universe from Finviz                          #
    # ------------------------------------------------------------------ #

    def get_top_gainers_finviz(self):
        """
        Scrape Finviz screener for top gaining small-cap low-float stocks.
        Returns list of dicts: [{symbol, price, change_pct, float_m, volume}, ...]
        This is the equivalent of Ross's 'Top Gappers' list each morning.
        """
        results = []
        try:
            resp = requests.get(FINVIZ_URL, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Finviz returned {resp.status_code}")
                return results

            soup = BeautifulSoup(resp.text, "html.parser")

            # Find the screener results table
            table = soup.find("table", {"id": "screener-views-table"})
            if not table:
                # Try alternate table class used by Finviz
                table = soup.find("table", class_="table-light")
            if not table:
                tables = soup.find_all("table")
                # The data table is usually the largest one
                table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)

            if not table:
                logger.warning("Could not find Finviz results table")
                return results

            rows = table.find_all("tr")
            if len(rows) < 2:
                logger.warning("Finviz table has no data rows")
                return results

            # Parse header to find column indices
            header_row = rows[0]
            headers = [th.get_text(strip=True) for th in header_row.find_all("td")]
            if not headers:
                headers = [th.get_text(strip=True) for th in header_row.find_all("th")]

            # Column name mappings
            col_map = {}
            for i, h in enumerate(headers):
                h_lower = h.lower()
                if h_lower == "ticker":
                    col_map["ticker"] = i
                elif h_lower == "price":
                    col_map["price"] = i
                elif "change" in h_lower and "%" not in h_lower:
                    col_map["change"] = i
                elif h_lower in ("change", "chg"):
                    col_map["change"] = i
                elif "volume" in h_lower and "rel" not in h_lower:
                    col_map["volume"] = i
                elif "float" in h_lower:
                    col_map["float"] = i

            logger.info(f"Finviz columns found: {col_map}")

            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                try:
                    # Ticker is usually in a link
                    ticker_cell = cells[col_map.get("ticker", 1)]
                    ticker = ticker_cell.get_text(strip=True)
                    if not ticker or len(ticker) > 6:
                        continue

                    price_text = cells[col_map.get("price", 8)].get_text(strip=True)
                    change_text = cells[col_map.get("change", 9)].get_text(strip=True)
                    volume_text = cells[col_map.get("volume", 10)].get_text(strip=True)

                    price = float(price_text.replace(",", "").replace("$", "")) if price_text else 0
                    change_pct = float(change_text.replace("%", "").replace(",", "")) if change_text else 0
                    volume = self._parse_volume(volume_text)

                    if price > 0 and ticker:
                        results.append({
                            "symbol":     ticker,
                            "price":      price,
                            "change_pct": change_pct,
                            "volume":     volume,
                        })
                except Exception as e:
                    logger.debug(f"Row parse error: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Finviz fetch error: {e}")

        logger.info(f"📋 Finviz returned {len(results)} candidates")
        return results

    def _parse_volume(self, vol_str):
        """Parse volume strings like '1.2M', '500K' into integers"""
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
    #  STEP 2 — Finnhub enrichment: RVOL + candles                       #
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
            logger.warning(f"Finnhub error [{endpoint}]: {e}")
            return None

    def get_candles(self, symbol, resolution="5", count=80):
        """Get intraday 5-min candles from Finnhub"""
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
            df = pd.DataFrame({
                "Open":   data["o"],
                "High":   data["h"],
                "Low":    data["l"],
                "Close":  data["c"],
                "Volume": data["v"],
            })
            return df
        except Exception:
            return None

    def get_daily_candles(self, symbol, days=35):
        """Get ~35 days of daily candles to calculate 30-day avg volume"""
        now = int(time.time())
        from_ts = now - (days * 86400)
        data = self._finnhub_get("stock/candle", {
            "symbol": symbol,
            "resolution": "D",
            "from": from_ts,
            "to": now
        })
        if not data or data.get("s") != "ok":
            return None
        try:
            return pd.DataFrame({
                "Close":  data["c"],
                "Volume": data["v"],
            })
        except Exception:
            return None

    def calculate_rvol(self, symbol):
        """
        Ross Cameron RVOL: today's volume vs 30-day average daily volume.
        Returns float or 0 if data unavailable.
        """
        daily = self.get_daily_candles(symbol, days=35)
        time.sleep(0.15)
        if daily is None or len(daily) < 5:
            return 0

        # 30-day avg excludes today (last row)
        avg_vol = daily["Volume"].iloc[:-1].tail(30).mean()
        today_vol = daily["Volume"].iloc[-1]

        return round(today_vol / avg_vol, 1) if avg_vol > 0 else 0

    # ------------------------------------------------------------------ #
    #  STEP 3 — News catalyst check (Pillar 3)                           #
    # ------------------------------------------------------------------ #

    def has_news_today(self, symbol):
        """Check if Finnhub has news for this stock today — Ross Pillar 3"""
        today = date.today().strftime("%Y-%m-%d")
        data = self._finnhub_get("company-news", {
            "symbol": symbol,
            "_from": today,
            "to": today
        })
        time.sleep(0.15)
        if data and len(data) > 0:
            logger.info(f"  📰 {symbol} has {len(data)} news item(s) today")
            return True
        return False

    # ------------------------------------------------------------------ #
    #  STEP 4 — Reversal pattern detection                               #
    # ------------------------------------------------------------------ #

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
    #  MAIN SCAN                                                          #
    # ------------------------------------------------------------------ #

    def scan_for_momentum(self):
        """
        Ross Cameron 5 Pillar scan:
        1. Pull dynamic universe from Finviz (top gaining small-cap low-float stocks)
        2. Apply Pillar 2 filter: up 10%+ 
        3. Enrich with Finnhub RVOL (Pillar 1: 5x)
        4. Check news catalyst (Pillar 3)
        5. Score and return top 10
        """
        logger.info("🔄 VISION v7 — Ross Cameron 5 Pillar scan starting...")

        # --- Step 1: Dynamic universe from Finviz ---
        candidates_raw = self.get_top_gainers_finviz()

        if not candidates_raw:
            logger.warning("⚠️ Finviz returned no results — market may be closed or blocked")
            return []

        # --- Step 2: Quick filters (price, gap) ---
        filtered = []
        for s in candidates_raw:
            if s["price"] < self.MIN_PRICE or s["price"] > self.MAX_PRICE:
                logger.debug(f"  {s['symbol']} ❌ price ${s['price']}")
                continue
            if s["change_pct"] < self.MIN_GAP:
                logger.debug(f"  {s['symbol']} ❌ change {s['change_pct']:.1f}% (need {self.MIN_GAP}%+)")
                continue
            filtered.append(s)
            logger.info(f"  {s['symbol']} ✓ price ${s['price']} | up {s['change_pct']:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")

        if not filtered:
            logger.info("No stocks passed initial filters — market may be slow today")
            return []

        # --- Step 3: Enrich with Finnhub (RVOL + news) ---
        qualified = []

        for s in filtered[:20]:  # Cap at 20 to stay within Finnhub rate limits
            symbol = s["symbol"]
            logger.info(f"  🔍 Checking {symbol}...")

            # Pillar 1: RVOL
            rvol = self.calculate_rvol(symbol)
            if rvol < self.MIN_RVOL:
                logger.info(f"  {symbol} ❌ RVOL {rvol}x (need {self.MIN_RVOL}x)")
                continue

            # Pillar 3: News catalyst
            has_news = self.has_news_today(symbol)

            # Intraday candles for reversal detection
            df = self.get_candles(symbol)
            time.sleep(0.15)
            reversal = self.detect_reversal(df)

            # Scoring — weighted by Ross's priorities
            score = 0
            score += min(rvol / self.MIN_RVOL, 4) * 35        # RVOL: up to 140 pts
            score += min(s["change_pct"] / 10, 3) * 25        # Gap: up to 75 pts
            score += 30 if has_news else 0                     # News catalyst bonus
            score += 20 if reversal else 0                     # Reversal pattern bonus

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
                f"  ✅ {symbol} QUALIFIED | Gap:{s['change_pct']:.1f}% | "
                f"RVOL:{rvol}x | News:{'YES' if has_news else 'no'} | "
                f"Reversal:{'YES' if reversal else 'no'} | Score:{score:.0f}"
            )

        qualified.sort(key=lambda x: x["score"], reverse=True)
        top_10 = qualified[:10]

        logger.info(f"🎯 Scan complete — {len(qualified)} qualified, top {len(top_10)} selected")
        for i, s in enumerate(top_10):
            logger.info(
                f"  {i+1}. ${s['symbol']} | {s['pct_change']}% | "
                f"RVOL:{s['rvol']}x | News:{'✓' if s['has_news'] else '✗'} | Score:{s['score']}"
            )

        return top_10
