import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime
import requests
import os
import logging
import time

logger = logging.getLogger("VISION_SCANNER")

class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL = 5.0
        self.MAX_FLOAT = 20_000_000       # 20M shares (Warrior Trading standard)
        self.MIN_PRICE = 1.0
        self.MAX_PRICE = 20.0
        self.MIN_GAP = 3.0                # Minimum 3% gap up
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")

    def fetch_top_gappers(self):
        """Fetches top gap-up stocks from Finnhub, with watchlist fallback"""
        gappers = []

        try:
            if self.finnhub_key:
                url = f"https://finnhub.io/api/v1/stock/market-gap?token={self.finnhub_key}"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    for item in data.get('data', []):
                        if item.get('gap', 0) >= self.MIN_GAP:
                            gappers.append({
                                'symbol': item.get('symbol'),
                                'gap': round(item.get('gap', 0), 1),
                                'price': item.get('current', 0)
                            })
        except Exception as e:
            logger.warning(f"Finnhub error: {e}")

        # Fallback watchlist if Finnhub fails or returns nothing
        if not gappers:
            logger.info("Using fallback watchlist")
            fallback_watchlist = [
                "ASTS", "QUBT", "LUNR", "SATS", "NKTR", "MOBX", "CECO", "RKLB", "AISP", "HOLO",
                "MARA", "RIOT", "COIN", "NVDA", "AMD", "TSLA", "PLTR", "SOFI", "UPST", "AFRM",
                "GME", "AMC", "MULN", "SNAP", "PINS", "ROKU", "DOCU", "ZM", "PYPL", "SQ",
                "SHOP", "DKNG", "UBER", "LYFT", "ABNB", "DASH", "RBLX", "NIO", "LI", "XPEV",
                "LCID", "RIVN", "FUBO", "WBD", "PARA", "OPEN", "SPCE", "NKLA", "HOOD", "CLOV"
            ]
            for symbol in fallback_watchlist:
                gappers.append({'symbol': symbol, 'gap': 0, 'price': 0})

        return gappers

    def fetch_batch_history(self, symbols):
        """
        Fetch 1-day 5-min history for multiple symbols in one yfinance call.
        Returns a dict of {symbol: DataFrame}.
        Batching is far more efficient than one call per symbol.
        """
        try:
            raw = yf.download(
                tickers=" ".join(symbols),
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True
            )
            result = {}
            for sym in symbols:
                try:
                    if len(symbols) == 1:
                        df = raw.copy()
                    else:
                        df = raw[sym].copy()
                    df = df.dropna(how='all')
                    if not df.empty:
                        result[sym] = df
                except Exception:
                    pass
            return result
        except Exception as e:
            logger.warning(f"Batch fetch error: {e}")
            return {}

    def get_float(self, symbol):
        """Gets float shares from yfinance"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            float_shares = info.get('floatShares') or info.get('sharesOutstanding', 0)
            return float_shares / 1_000_000 if float_shares and float_shares > 0 else float('inf')
        except Exception:
            return float('inf')

    def calculate_rvol(self, df):
        """Calculates Relative Volume (RVOL) vs recent average"""
        if len(df) < 10:
            return 0
        avg_vol = df['Volume'].iloc[:-5].mean() if len(df) > 5 else df['Volume'].mean()
        current_vol = df['Volume'].iloc[-1]
        return current_vol / avg_vol if avg_vol > 0 else 0

    def calculate_adx(self, df, period=14):
        """Calculate ADX manually"""
        try:
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

            return adx.iloc[-1] if len(adx) > 0 else 25
        except Exception:
            return 25

    def detect_reversal(self, df):
        """Detects instant reversal patterns"""
        if len(df) < 3:
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
        Complete Warrior Trading scan:
        - Fetches price data in batches to avoid rate limits
        - Filters by RVOL, Float, Price, Gap %
        - Returns top 10 ranked candidates
        """
        logger.info("🔄 Scanning stocks for Warrior Trading setup...")

        gappers = self.fetch_top_gappers()
        symbols = [s['symbol'] for s in gappers[:50]]  # Cap at 50 to stay under rate limits
        gap_map = {s['symbol']: s.get('gap', 0) for s in gappers}

        logger.info(f"📊 Fetching data for {len(symbols)} stocks in batches of 10...")

        # Fetch all price data in batches — avoids per-symbol rate limiting
        all_data = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            batch_data = self.fetch_batch_history(batch)
            all_data.update(batch_data)
            time.sleep(1)  # 1 second between batches

        logger.info(f"✅ Got price data for {len(all_data)} symbols")

        candidates = []

        for symbol, df in all_data.items():
            try:
                if df.empty or len(df) < 5:
                    continue

                current_price = df['Close'].iloc[-1]
                open_price = df['Open'].iloc[0]
                gap_pct = ((current_price - open_price) / open_price) * 100

                # Use pre-calculated gap from Finnhub if available
                if gap_map.get(symbol, 0) > 0:
                    gap_pct = gap_map[symbol]

                # Price filter
                if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
                    continue

                # Gap filter
                if gap_pct < self.MIN_GAP:
                    continue

                # RVOL filter (uses already-fetched data — no extra call)
                rvol = self.calculate_rvol(df)
                if rvol < self.MIN_RVOL:
                    continue

                # Float filter — only hits API for stocks that passed all other filters
                float_millions = self.get_float(symbol)
                time.sleep(0.3)  # Small delay for float calls
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

            except Exception as e:
                logger.warning(f"Error processing {symbol}: {e}")
                continue

        candidates.sort(key=lambda x: x['score'], reverse=True)
        top_10 = candidates[:10]

        logger.info(f"🎯 Found {len(candidates)} qualified stocks. Top 10 selected.")
        for i, stock in enumerate(top_10):
            logger.info(
                f"  {i+1}. ${stock['symbol']} | Gap: {stock['pct_change']}% | "
                f"RVOL: {stock['rvol']}x | Float: {stock['float']}M | Score: {stock['score']}"
            )

        return top_10
