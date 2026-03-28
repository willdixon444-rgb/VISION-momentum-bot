import yfinance as yf
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import requests
import os

class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL = 5.0
        self.MAX_FLOAT = 20_000_000  # 20M Shares (Warrior Trading standard)
        self.MIN_PRICE = 1.0
        self.MAX_PRICE = 20.0
        self.MIN_GAP = 3.0  # Minimum 3% gap up
        self.finnhub_key = os.environ.get("FINNHUB_API_KEY", "")
        
    def fetch_top_gappers(self):
        """Fetches top 100 gap-up stocks from Finnhub"""
        gappers = []
        
        try:
            # Try Finnhub first
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
            print(f"Finnhub error: {e}")
        
        # Fallback: Use predefined large watchlist if Finnhub fails
        if not gappers:
            fallback_watchlist = [
                "ASTS", "QUBT", "LUNR", "SATS", "NKTR", "MOBX", "CECO", "RKLB", "AISP", "HOLO",
                "MARA", "RIOT", "COIN", "NVDA", "AMD", "TSLA", "PLTR", "SOFI", "UPST", "AFRM",
                "GME", "AMC", "BBBY", "MULN", "SNAP", "PINS", "ROKU", "U", "DOCU", "ZM",
                "PYPL", "SQ", "SHOP", "DKNG", "UBER", "LYFT", "ABNB", "DASH", "RBLX", "UAA",
                "NIO", "LI", "XPEV", "LCID", "RIVN", "F", "GM", "FUBO", "WBD", "PARA"
            ]
            for symbol in fallback_watchlist[:100]:
                gappers.append({'symbol': symbol, 'gap': 0, 'price': 0})
                
        return gappers
    
    def get_float(self, symbol):
        """Gets shares outstanding (float) from yfinance"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            shares_outstanding = info.get('sharesOutstanding', 0)
            float_shares = info.get('floatShares', shares_outstanding)
            if float_shares and float_shares > 0:
                return float_shares / 1_000_000  # Convert to millions
            return float('inf')
        except Exception:
            return float('inf')
    
    def calculate_rvol(self, df):
        """Calculates Relative Volume (RVOL)"""
        if len(df) < 10:
            return 0
        avg_vol = df['Volume'].iloc[:-5].mean() if len(df) > 5 else df['Volume'].mean()
        current_vol = df['Volume'].iloc[-1]
        return current_vol / avg_vol if avg_vol > 0 else 0
    
    def calculate_adx(self, df, period=14):
        """Calculate ADX (Average Directional Index) manually"""
        try:
            high = df['High']
            low = df['Low']
            close = df['Close']
            
            # True Range
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            
            # Directional Movement
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low
            
            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
            
            # Smoothed averages
            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
            minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)
            
            # DX and ADX
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
        
        # Bullish reversal: Price breaks above previous high with volume spike
        if last_candle['Close'] > prev_candle['High'] and last_candle['Volume'] > prev_candle['Volume'] * 1.5:
            return True
        
        # V-shaped reversal: Strong bounce off low
        if len(df) > 10:
            low_5min = df['Low'].iloc[-10:].min()
            if last_candle['Low'] <= low_5min * 1.01 and last_candle['Close'] > last_candle['Open'] * 1.02:
                return True
                
        return False
    
    def scan_for_momentum(self):
        """
        Complete Warrior Trading scan:
        - Scans 100+ gap-up stocks
        - Filters by RVOL, Float, Price, Gap %
        - Returns top 10 ranked candidates
        """
        print("🔄 Scanning 100+ stocks for Warrior Trading setup...")
        
        # Step 1: Get top gappers
        gappers = self.fetch_top_gappers()
        print(f"📊 Found {len(gappers)} gap-up stocks")
        
        candidates = []
        
        for stock in gappers[:150]:  # Scan up to 150 stocks
            symbol = stock['symbol']
            
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="1d", interval="5m")
                
                if df.empty or len(df) < 5:
                    continue
                
                # Get current metrics
                current_price = df['Close'].iloc[-1]
                open_price = df['Open'].iloc[0]
                gap_pct = ((current_price - open_price) / open_price) * 100
                
                # Use pre-calculated gap if available
                if stock.get('gap', 0) > 0:
                    gap_pct = stock['gap']
                
                # Filter by price range
                if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
                    continue
                
                # Filter by gap %
                if gap_pct < self.MIN_GAP:
                    continue
                
                # Get float data
                float_millions = self.get_float(symbol)
                if float_millions > self.MAX_FLOAT:
                    continue
                
                # Calculate RVOL
                rvol = self.calculate_rvol(df)
                if rvol < self.MIN_RVOL:
                    continue
                
                # Technical indicators
                adx = self.calculate_adx(df)
                reversal = self.detect_reversal(df)
                
                # Calculate score for ranking
                score = 0
                score += min(rvol / self.MIN_RVOL, 3) * 30  # RVOL up to 90 points
                score += min(gap_pct / 5, 3) * 20  # Gap up to 60 points
                score += max(0, (self.MAX_FLOAT - float_millions) / self.MAX_FLOAT) * 20  # Low float up to 20 points
                score += (30 if reversal else 10)  # Reversal bonus
                
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
                print(f"Error scanning {symbol}: {e}")
                continue
        
        # Step 2: Sort by score and return top 10
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top_10 = candidates[:10]
        
        print(f"🎯 Found {len(candidates)} qualified stocks. Top 10 selected.")
        for i, stock in enumerate(top_10):
            print(f"  {i+1}. ${stock['symbol']} | Gap: {stock['pct_change']}% | RVOL: {stock['rvol']}x | Float: {stock['float']}M | Score: {stock['score']}")
        
        return top_10
