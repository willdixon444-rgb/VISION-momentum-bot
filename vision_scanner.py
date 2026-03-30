import numpy as np
import pandas as pd
import requests
import os
import logging
import time

logger = logging.getLogger("VISION_SCANNER")

class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL = 5.0
        self.MAX_FLOAT = 20_000_000       # 20M shares
        self.MIN_PRICE = 1.0
        self.MAX_PRICE = 20.0
        self.MIN_GAP = 3.0                # Minimum 3% gap up
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")

        # Warrior Trading style watchlist — small/mid cap movers
        self.watchlist = [
            "ASTS", "QUBT", "LUNR", "SATS", "NKTR", "MOBX", "CECO", "RKLB", "AISP", "HOLO",
            "MARA", "RIOT", "COIN", "PLTR", "SOFI", "UPST", "AFRM", "GME", "AMC", "MULN",
            "SNAP", "PINS", "ROKU", "PYPL", "SQ", "DKNG", "RBLX", "NIO", "XPEV", "LCID",
            "RIVN", "FUBO", "HOOD", "CLOV", "OPEN", "SPCE", "NKLA", "BBIG", "PRTY", "HRTX",
            "SNDL", "MVIS", "OCGN", "ATER", "CLOV", "EXPR", "SDC", "SPRT", "IRNT", "PHUN"
        ]

    def _finnhub_get(self, endpoint, params=None):
        """Generic Finnhub API caller with error handling"""
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
                logger.warning("Finnhub rate limit hit — sleeping 5s")
                time.sleep(5)
            return None
        except Exception as e:
            logger.warning(f"Finnhub request error: {e}")
            return None

    def get_quote(self, symbol):
        """
        Get current quote from Finnhub.
        Returns: {c: current, o: open, h: high, l: low, pc: prev_close}
        """
        return self._finnhub_get("quote", {"symbol": symbol})

    def get_candles(self, symbol, resolution="5", count=50):
        """
        Get intraday candles from Finnhub.
        resolution: 1, 5, 15, 30, 60 (minutes)
        Returns DataFrame with OHLCV or None
        """
        import time as t
        now = int(t.time())
        # Go back far enough to get ~50 candles of the given resolution
        lookback = count * int(resolution) * 60 * 2
        from_ts = now - lookback

        data = self._finnhub_get("stock/candle", {
            "symbol": symbol,
            "resolution": resolution,
            "from": from_ts,
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
                "Time":   data["t"]
            })
            df["Time"] = pd.to_datetime(df["Time"], unit="s")
            df = df.set_index("Time")
            return df
        except Exception as e:
            logger.warning(f"Candle parse error for {symbol}: {e}")
            return None

    def get_basic_financials(self, symbol):
        """Get float/shares outstanding from Finnhub"""
        data = self._finnhub_get("stock/profile2", {"symbol": symbol})
        if data and data.get("shareOutstanding"):
            return data["shareOutstanding"]  # Already in millions
        return float('inf')

    def calculate_rvol(self, df):
        """Calculates Relative Volume (RVOL) vs recent average"""
        if df is None or len(df) < 10:
            return 0
        avg_vol = df['Volume'].iloc[:-5].mean() if len(df) > 5 else df['Volume'].mean()
        current_vol = df['Volume'].iloc[-1]
        return current_vol / avg_vol if avg_vol > 0 else 0

    def calculate_adx(self, df, period=14):
        """Calculate ADX manually"""
        try:
            if df is None or len(df) < period + 2:
                return 25
            high = df['High']
            low = df['Low']
            close = df['Close']

            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            up_move = high - high.shift(1)
            down_move = low.shift(1) - low

            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
            minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)

            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            adx = dx.rolling(window=period).mean()

            return float(adx.iloc[-1]) if len(adx) > 0 and not np.isnan(adx.iloc[-1]) else 25
        except Exception:
            return 25

    def detect_reversal(self, df):
        """Detects instant reversal patterns"""
        if df is None or len(df) < 3:
            return False

        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        if last_candle['Close'] > prev_candle['High'] and last_candle['Volume'] > prev_candle['Volume'] * 1.5:
            return True

        if len(df) > 10:
            low_5min = df['Low'].iloc[-10:].min()
            if last_candle['Low'] <= low_5min * 1.01 and last_candle['Close'] > last_candle['Open'] * 1.02:
                return True

        return False

    def scan_for_momentum(self):
        """
        Warrior Trading scan using Finnhub for all data.
        No yfinance — avoids Yahoo Finance cloud IP blocks.
        """
        logger.info("🔄 Scanning stocks for Warrior Trading setup...")

        if not self.finnhub_key:
            logger.error("❌ FINNHUB_API_KEY not set — cannot scan without it")
            return []

        candidates = []
        scanned = 0

        for symbol in self.watchlist:
            try:
                # Step 1: Quick quote check — price and gap
                quote = self.get_quote(symbol)
                time.sleep(0.12)  # ~8 req/sec — Finnhub free tier allows 60/min

                if not quote or quote.get("c", 0) == 0:
                    continue

                current_price = quote["c"]
                open_price = quote["o"]
                prev_close = quote["pc"]

                # Use open vs prev_close for gap (more accurate than open vs current)
                gap_pct = ((open_price - prev_close) / prev_close) * 100 if prev_close > 0 else 0

                scanned += 1

                # Quick filters before expensive candle fetch
                if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
                    continue
                if gap_pct < self.MIN_GAP:
                    continue

                # Step 2: Get candles for RVOL and reversal
                df = self.get_candles(symbol, resolution="5", count=50)
                time.sleep(0.12)

                if df is None or len(df) < 5:
                    continue

                rvol = self.calculate_rvol(df)
                if rvol < self.MIN_RVOL:
                    continue

                # Step 3: Float check — only for stocks passing all filters
                float_millions = self.get_basic_financials(symbol)
                time.sleep(0.12)

                if float_millions > self.MAX_FLOAT:
                    continue

                # Technical indicators
                adx = self.calculate_adx(df)
                reversal = self.detect_reversal(df)

                # Scoring
                score = 0
                score += min(rvol / self.MIN_RVOL, 3) * 30
                score += min(gap_pct / 5, 3) * 20
                score += max(0, (self.MAX_FLOAT - float_millions) / self.MAX_FLOAT) * 20
                score += 30 if reversal else 10

                candidates.append({
                    'symbol': symbol,
                    'price': round(current_price, 2),
                    'pct_change': round(gap_pct, 1),
                    'rvol': round(rvol, 1),
                    'float': round(float_millions, 1),
                    'adx': round(adx, 1),
                    'reversal': reversal,
                    'score': round(score, 0)
                })

                logger.info(f"✅ {symbol} qualified | Gap: {gap_pct:.1f}% | RVOL: {rvol:.1f}x | Float: {float_millions:.1f}M")

            except Exception as e:
                logger.warning(f"Error processing {symbol}: {e}")
                continue

        candidates.sort(key=lambda x: x['score'], reverse=True)
        top_10 = candidates[:10]

        logger.info(f"🎯 Scanned {scanned} stocks. Found {len(candidates)} qualified. Top 10 selected.")
        for i, stock in enumerate(top_10):
            logger.info(
                f"  {i+1}. ${stock['symbol']} | Gap: {stock['pct_change']}% | "
                f"RVOL: {stock['rvol']}x | Float: {stock['float']}M | Score: {stock['score']}"
            )

        return top_10
