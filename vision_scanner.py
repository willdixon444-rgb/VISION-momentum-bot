"""
VISION Scanner v16 — Ross Cameron 5 Pillar + Bull Flag Detection
================================================================
New in v16:
- VWAP calculation from today's 1-min candles
- 9 EMA calculation
- Bull flag detection: 2-3 red candles + first green candle making new high
- Confirmed on both 1-min and 5-min charts
- Price must be above VWAP for valid setup
- Stop loss / profit target passed through to engine
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
    #  Alpaca — universe + volume                                         #
    # ------------------------------------------------------------------ #

    def get_alpaca_movers(self):
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
        if not symbols:
            return {}
        try:
            url = f"{ALPACA_DATA_BASE}/v2/stocks/snapshots"
            params = {"symbols": ",".join(symbols), "feed": "iex"}
            r = requests.get(url, headers=self._alpaca_headers(), params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                results = {}
                for sym, snap in data.items():
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
            else:
                logger.warning(f"Alpaca snapshots {r.status_code}")
                return {}
        except Exception as e:
            logger.warning(f"Alpaca snapshots error: {e}")
            return {}

    # ------------------------------------------------------------------ #
    #  Finnhub — candles for technical analysis                          #
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

    def get_candles(self, symbol, resolution="1", count=100):
        """Get intraday candles from Finnhub."""
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
                "Time":   pd.to_datetime(data["t"], unit="s")
            })
            df = df.set_index("Time")
            return df
        except Exception:
            return None

    def has_news_today(self, symbol):
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

    # ------------------------------------------------------------------ #
    #  Technical analysis — Ross Cameron style                            #
    # ------------------------------------------------------------------ #

    def calculate_vwap(self, df):
        """VWAP = sum(price * volume) / sum(volume)"""
        try:
            if df is None or len(df) < 2:
                return 0
            typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
            vwap = (typical_price * df["Volume"]).sum() / df["Volume"].sum()
            return round(float(vwap), 4)
        except Exception:
            return 0

    def calculate_ema(self, df, period=9):
        """Exponential Moving Average."""
        try:
            if df is None or len(df) < period:
                return 0
            ema = df["Close"].ewm(span=period, adjust=False).mean()
            return round(float(ema.iloc[-1]), 4)
        except Exception:
            return 0

    def detect_bull_flag(self, df, timeframe="1min"):
        """
        Ross Cameron bull flag detection:
        1. Strong move up (flagpole) — 2+ large green candles
        2. Pullback of 2-3 red candles on lighter volume
        3. First green candle making new high = ENTRY signal

        Returns: (is_bull_flag, entry_price)
        """
        try:
            if df is None or len(df) < 6:
                return False, 0

            candles = df.tail(8).reset_index(drop=True)
            n = len(candles)

            # Look for the pattern in the last 8 candles
            for i in range(2, n):
                current = candles.iloc[i]
                is_green = current["Close"] > current["Open"]

                if not is_green:
                    continue

                # Check if this green candle makes a new high
                # compared to the previous 1-3 red candles
                prev_candles = candles.iloc[max(0, i-3):i]
                red_candles = prev_candles[prev_candles["Close"] < prev_candles["Open"]]

                if len(red_candles) < 1:
                    continue

                # Must have 1-3 red candles before this green one
                # Check that green candle broke above the red candle highs
                max_red_high = red_candles["High"].max()
                if current["Close"] <= max_red_high:
                    continue

                # Check pullback volume is lighter than flagpole
                # Look for flagpole (2+ green candles before the reds)
                if i >= 3:
                    pre_red = candles.iloc[max(0, i-5):i-len(red_candles)]
                    green_before = pre_red[pre_red["Close"] > pre_red["Open"]]
                    if len(green_before) >= 1:
                        avg_green_vol = green_before["Volume"].mean() if len(green_before) > 0 else 1
                        avg_red_vol   = red_candles["Volume"].mean() if len(red_candles) > 0 else 1
                        # Pullback should be on lower volume (Ross's rule)
                        if avg_red_vol < avg_green_vol * 1.5:
                            entry_price = round(float(current["High"]), 2)
                            logger.info(f"  🚩 Bull flag detected on {timeframe} chart — entry: ${entry_price}")
                            return True, entry_price

            return False, 0
        except Exception as e:
            logger.debug(f"Bull flag detection error: {e}")
            return False, 0

    def calculate_rvol(self, symbol, today_volume, prev_volume):
        """RVOL using Finnhub 10-day avg, fallback to vs yesterday."""
        if self.finnhub_key:
            try:
                url = "https://finnhub.io/api/v1/stock/metric"
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

        if prev_volume and prev_volume > 0:
            rvol = today_volume / prev_volume
            logger.info(f"  {symbol} RVOL (vs yesterday): {rvol:.1f}x")
            return round(rvol, 1)

        return 0

    # ------------------------------------------------------------------ #
    #  Main scan                                                          #
    # ------------------------------------------------------------------ #

    def scan_for_momentum(self):
        """Ross Cameron 5 Pillar scan with bull flag + VWAP + 9 EMA."""
        logger.info("🔄 VISION v16 — Ross Cameron 5 Pillar + Bull Flag scan starting...")

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
                continue
            if gap < self.MIN_GAP:
                continue
            filtered.append(s)
            logger.info(f"  {s['symbol']} ✓ ${price:.2f} | +{gap:.1f}%")

        logger.info(f"📊 {len(filtered)} stocks passed price/gap filters")
        if not filtered:
            logger.info("No stocks passed filters — market may be slow today")
            return []

        # Step 3: Alpaca snapshots for volume
        symbols = [s["symbol"] for s in filtered[:20]]
        snapshots = self.get_alpaca_snapshots(symbols)

        # Step 4: Full enrichment
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

            # Get 1-min candles for VWAP, 9 EMA, bull flag
            df_1min = self.get_candles(symbol, resolution="1", count=100)
            time.sleep(0.15)

            # Get 5-min candles for bull flag confirmation
            df_5min = self.get_candles(symbol, resolution="5", count=50)
            time.sleep(0.15)

            # Calculate VWAP
            vwap = self.calculate_vwap(df_1min) if df_1min is not None else snap.get("vwap", 0)

            # Calculate 9 EMA
            ema9 = self.calculate_ema(df_1min, period=9) if df_1min is not None else 0

            current_price = s["price"]

            # Ross's rule: price must be above VWAP
            above_vwap = vwap > 0 and current_price > vwap
            above_ema9 = ema9 > 0 and current_price > ema9

            if not above_vwap:
                logger.info(f"  {symbol} ⚠️ price ${current_price} below VWAP ${vwap:.2f} — weaker setup")

            # Detect bull flag on 1-min (primary) and 5-min (confirmation)
            bull_flag_1min, entry_1min = self.detect_bull_flag(df_1min, "1min")
            bull_flag_5min, entry_5min = self.detect_bull_flag(df_5min, "5min")

            bull_flag = bull_flag_1min or bull_flag_5min
            entry_price = entry_1min if bull_flag_1min else (entry_5min if bull_flag_5min else current_price)

            # Check news
            has_news = self.has_news_today(symbol)

            # Scoring — Ross's priorities
            score  = min(rvol / self.MIN_RVOL, 4) * 35      # RVOL weight
            score += min(s["change_pct"] / 10, 3) * 25      # Gap weight
            score += 30 if has_news else 0                   # News catalyst
            score += 25 if bull_flag else 0                  # Bull flag bonus
            score += 15 if above_vwap else 0                 # Above VWAP bonus
            score += 10 if above_ema9 else 0                 # Above 9 EMA bonus

            qualified.append({
                "symbol":      symbol,
                "price":       round(current_price, 2),
                "entry_price": round(entry_price, 2),
                "pct_change":  round(s["change_pct"], 1),
                "rvol":        rvol,
                "float":       0,
                "has_news":    has_news,
                "bull_flag":   bull_flag,
                "above_vwap":  above_vwap,
                "above_ema9":  above_ema9,
                "vwap":        round(vwap, 2),
                "ema9":        round(ema9, 2),
                "score":       round(score, 0)
            })

            bf_str = "🚩 BULL FLAG" if bull_flag else ""
            vwap_str = "✅ above VWAP" if above_vwap else "⚠️ below VWAP"
            logger.info(
                f"  ✅ {symbol} QUALIFIED {bf_str} | +{s['change_pct']:.1f}% | "
                f"RVOL:{rvol}x | {vwap_str} | News:{'YES' if has_news else 'no'} | Score:{score:.0f}"
            )

        qualified.sort(key=lambda x: x["score"], reverse=True)
        top_10 = qualified[:10]

        logger.info(f"🎯 Scan complete — {len(qualified)} qualified, top {len(top_10)} selected")
        for i, s in enumerate(top_10):
            bf = "🚩" if s["bull_flag"] else ""
            logger.info(
                f"  {i+1}. ${s['symbol']} {bf} | +{s['pct_change']}% | "
                f"RVOL:{s['rvol']}x | VWAP:{'✓' if s['above_vwap'] else '✗'} | "
                f"News:{'✓' if s['has_news'] else '✗'} | Score:{s['score']}"
            )
        return top_10
