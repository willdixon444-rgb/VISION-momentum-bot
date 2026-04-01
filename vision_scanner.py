"""
VISION Scanner v14 — Ross Cameron 5 Pillar Methodology
=======================================================
Fix from v13: Finnhub returns volume=0 for micro-cap stocks.

v14 fix: Use Alpaca's own snapshot endpoint for volume data.
Alpaca already has the data since it's surfacing these stocks —
we just need to ask it for the volume too.

Finnhub still used for: news catalyst check only.
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

TICKER_RE = re.compile(r'^[A-Z]{1,6}$')
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

    def _alpaca_headers(self):
        return {
            "APCA-API-KEY-ID":     self.alpaca_key,
            "APCA-API-SECRET-KEY": self.alpaca_secret,
            "Accept": "application/json"
        }

    # ------------------------------------------------------------------ #
    #  Alpaca — universe + volume data                                    #
    # ------------------------------------------------------------------ #

    def get_alpaca_movers(self):
        """Top gaining stocks from Alpaca movers endpoint."""
        if not self.alpaca_key or not self.alpaca_secret:
            logger.error("❌ Alpaca keys not set")
            return []
        try:
            url = f"{ALPACA_DATA_BASE}/v1beta1/screener/stocks/movers"
            r = requests.get(url, headers=self._alpaca_headers(), params={"top": 50}, timeout=10)
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
        """
        Get snapshots for a list of symbols from Alpaca.
        Returns dict of {symbol: {volume, vwap, prev_close, ...}}
        Alpaca snapshot includes today's volume — reliable for micro-caps.
        """
        if not symbols:
            return {}
        try:
            url = f"{ALPACA_DATA_BASE}/v2/stocks/snapshots"
            params = {
                "symbols": ",".join(symbols),
                "feed": "iex"  # IEX feed works on free tier
            }
            r = requests.get(url, headers=self._alpaca_headers(), params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                results = {}
                for sym, snap in data.items():
                    try:
                        daily = snap.get("dailyBar", {})
                        prev  = snap.get("prevDailyBar", {})
                        results[sym] = {
                            "volume":     daily.get("v", 0),
                            "prev_volume": prev.get("v", 0),
                            "vwap":       daily.get("vw", 0),
                            "open":       daily.get("o", 0),
                            "high":       daily.get("h", 0),
                            "low":        daily.get("l", 0),
                            "close":      daily.get("c", 0),
                            "prev_close": prev.get("c", 0),
                        }
                    except Exception:
                        pass
                logger.info(f"📊 Alpaca snapshots returned data for {len(results)} symbols")
                return results
            else:
                logger.warning(f"Alpaca snapshots {r.status_code}: {r.text[:200]}")
                return {}
        except Exception as e:
            logger.warning(f"Alpaca snapshots error: {e}")
            return {}

    def calculate_rvol(self, symbol, today_volume, prev_volume):
        """
        RVOL = today's volume / previous day's volume.
        Simple and reliable using Alpaca data.
        For more accuracy we use Finnhub 10-day avg if available.
        """
        # Try Finnhub 10-day avg first
        if self.finnhub_key:
            try:
                url = f"https://finnhub.io/api/v1/stock/metric"
                params = {"symbol": symbol, "metric": "all", "token": self.finnhub_key}
                r = requests.get(url, params=params, timeout=8)
                time.sleep(0.15)
                if r.status_code == 200:
                    m = r.json().get("metric", {})
                    avg_vol_m = m.get("10DayAverageTradingVolume") or m.get("3MonthAverageTradingVolume")
                    if avg_vol_m and avg_vol_m > 0:
                        avg_vol = avg_vol_m * 1_000_000
                        rvol = today_volume / avg_vol
                        logger.info(f"  {symbol} RVOL: {rvol:.1f}x (today:{today_volume:,} / 10d-avg:{avg_vol:,.0f})")
                        return round(rvol, 1)
            except Exception:
                pass

        # Fallback: compare to yesterday's volume
        if prev_volume and prev_volume > 0:
            rvol = today_volume / prev_volume
            logger.info(f"  {symbol} RVOL (vs yesterday): {rvol:.1f}x (today:{today_volume:,} / prev:{prev_volume:,})")
            return round(rvol, 1)

        logger.info(f"  {symbol} RVOL: could not calculate")
        return 0

    # ------------------------------------------------------------------ #
    #  Finnhub — news only                                                #
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

    def has_news_today(self, symbol):
        """Ross Pillar 3 — news catalyst."""
        today = date.today().strftime("%Y-%m-%d")
        data = self._finnhub_get("company-news", {
            "symbol": symbol,
            "_from": today,
            "to": today
        })
        time.sleep(0.12)
        has = bool(data and len(data) > 0)
        if has:
            logger.info(f"  📰 {symbol} has {len(data)} news item(s) today")
        return has

    def detect_reversal(self, snap):
        """Simple reversal detection using OHLC from Alpaca snapshot."""
        if not snap:
            return False
        high  = snap.get("high", 0)
        low   = snap.get("low", 0)
        close = snap.get("close", 0)
        open_ = snap.get("open", 0)
        # Strong close near high of day = momentum
        if high > 0 and (close - low) / (high - low + 0.001) > 0.7:
            return True
        # Big move from open
        if open_ > 0 and (close - open_) / open_ > 0.05:
            return True
        return False

    # ------------------------------------------------------------------ #
    #  Main scan                                                          #
    # ------------------------------------------------------------------ #

    def scan_for_momentum(self):
        """Ross Cameron 5 Pillar scan — Alpaca movers + Alpaca volume + Finnhub news."""
        logger.info("🔄 VISION v14 — Ross Cameron 5 Pillar scan starting...")

        # Step 1: Get top movers
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
                continue
            if gap < self.MIN_GAP:
                continue
            filtered.append(s)
            logger.info(f"  {s['symbol']} ✓ ${price:.2f} | +{gap:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed filters — market may be slow today")
            return []

        # Step 3: Get Alpaca snapshots for volume data (batch call)
        symbols = [s["symbol"] for s in filtered[:20]]
        snapshots = self.get_alpaca_snapshots(symbols)

        # Step 4: RVOL + news + reversal
        qualified = []
        for s in filtered[:20]:
            symbol = s["symbol"]
            snap   = snapshots.get(symbol, {})

            today_vol = snap.get("volume", 0)
            prev_vol  = snap.get("prev_volume", 0)

            logger.info(f"  🔍 {symbol} vol today:{today_vol:,} prev:{prev_vol:,}")

            rvol = self.calculate_rvol(symbol, today_vol, prev_vol)
            if rvol < self.MIN_RVOL:
                logger.info(f"  {symbol} ❌ RVOL {rvol}x (need {self.MIN_RVOL}x)")
                continue

            has_news = self.has_news_today(symbol)
            reversal = self.detect_reversal(snap)

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
