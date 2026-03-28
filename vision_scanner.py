import yfinance as yf
import pandas_ta as ta
from datetime import datetime

class VisionRossScanner:
    def __init__(self):
        self.MIN_RVOL = 5.0
        self.MAX_FLOAT = 20_000_000 # 20M Shares

    def refresh_low_float_watchlist(self):
        # Dynamically pulls top movers or uses your core watchlist
        return ["QUBT", "ASTS", "SATS", "LUNR", "NKTR", "MOBX"]

    def scan_for_momentum(self, watchlist):
        candidates = []
        for symbol in watchlist:
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period="1d", interval="1m")
                if df.empty: continue

                # RVOL Calculation
                avg_vol = df['Volume'].mean()
                current_vol = df['Volume'].iloc[-1]
                rvol = current_vol / avg_vol if avg_vol > 0 else 0

                # Reversal Indicators (SAR & ADX)
                adx = df.ta.adx()
                psar = df.ta.psar()
                
                current_adx = adx['ADX_14'].iloc[-1]
                
                # Check for SAR Flip (Reversal)
                sar_flip = False
                if len(psar) > 2:
                    # Simple check if the SAR dot moved from above to below price
                    sar_flip = (psar['PSARl_0.02_0.2'].iloc[-2] != psar['PSARl_0.02_0.2'].iloc[-1])

                if rvol >= self.MIN_RVOL:
                    candidates.append({
                        'symbol': symbol,
                        'price': round(df['Close'].iloc[-1], 2),
                        'pct_change': round(((df['Close'].iloc[-1] - df['Open'].iloc[0]) / df['Open'].iloc[0]) * 100, 1),
                        'rvol': round(rvol, 1),
                        'reversal': (sar_flip or current_adx < 20)
                    })
            except Exception:
                continue
        return candidates
