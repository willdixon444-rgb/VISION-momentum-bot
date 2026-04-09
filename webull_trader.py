"""
webull_trader.py -- VISION Webull Paper Trading Connector
==========================================================
Uses the unofficial `webull` Python SDK (tedchou12/webull) to place
real paper trades on Webull without needing the official API key.

Required Render env vars:
  WEBULL_EMAIL    -- your Webull login email
  WEBULL_PASSWORD -- your Webull login password
  WEBULL_TRADE_PIN -- your 6-digit Webull trading PIN

Setup notes:
  1. pip install webull (added to requirements.txt)
  2. First deploy will attempt login — Webull requires MFA on first login.
     If MFA fails, set WEBULL_MFA env var with the code from your email.
  3. Session tokens are saved in memory — Render restarts will require re-login.
  4. Paper trading uses paper_webull class, no real money involved.

How it works:
  - open_paper_trade()  → places BUY limit order at alert price
  - close_half()        → places SELL limit order for half shares at T1
  - close_all()         → places SELL limit order for all remaining shares
  - All orders are LIMIT orders (Ross never uses market orders)
  - Reports fill status back to caller for Discord alerts
"""

import os
import logging
import time

logger = logging.getLogger("WEBULL_TRADER")

# Lazy import — only import webull if the package is installed
# Prevents crash if webull not yet in requirements
try:
    from webull import paper_webull
    WEBULL_AVAILABLE = True
except ImportError:
    WEBULL_AVAILABLE = False
    logger.warning("webull package not installed — Webull paper trading disabled")


class WebullTrader:
    """
    Manages Webull paper trading session.
    Instantiated once by VisionEngine, session reused across trades.
    """

    SHARES_PER_TRADE = 100   # Fixed share size — adjust once profitable

    def __init__(self):
        self._wb         = None
        self._logged_in  = False
        self._email      = os.environ.get("WEBULL_EMAIL", "")
        self._password   = os.environ.get("WEBULL_PASSWORD", "")
        self._trade_pin  = os.environ.get("WEBULL_TRADE_PIN", "")
        self._mfa_code   = os.environ.get("WEBULL_MFA", "")

    def _connect(self) -> bool:
        """
        Login to Webull paper trading account.
        Called once on first trade, session reused after that.
        """
        if not WEBULL_AVAILABLE:
            logger.error("webull package not installed")
            return False

        if not self._email or not self._password or not self._trade_pin:
            logger.error(
                "WEBULL_EMAIL, WEBULL_PASSWORD, WEBULL_TRADE_PIN "
                "must all be set in Render env vars"
            )
            return False

        try:
            self._wb = paper_webull()

            # Login — MFA code required on first login
            # Set WEBULL_MFA env var with the code Webull emails you
            if self._mfa_code:
                result = self._wb.login(
                    self._email,
                    self._password,
                    mfa=self._mfa_code,
                )
            else:
                result = self._wb.login(
                    self._email,
                    self._password,
                )

            if result and result.get("accessToken"):
                # Get trade token (needed for order placement)
                self._wb.get_trade_token(self._trade_pin)
                self._logged_in = True
                logger.info("✅ Webull paper trading logged in successfully")
                return True
            else:
                logger.error(f"Webull login failed: {result}")
                return False

        except Exception as e:
            logger.error(f"Webull connection error: {e}")
            return False

    def is_connected(self) -> bool:
        return self._logged_in and self._wb is not None

    # ── Order placement ────────────────────────────────────────────────

    def open_paper_trade(self, symbol: str, price: float) -> dict:
        """
        Place a BUY limit order on Webull paper account.
        Returns dict with order details or error.
        """
        if not self._logged_in and not self._connect():
            return {"success": False, "error": "Not connected to Webull"}

        try:
            # Limit order slightly above current price to ensure fill
            # on fast-moving stocks (Ross's approach — chase by a few cents)
            limit_price = round(price + 0.03, 2)

            order = self._wb.place_order(
                stock=symbol,
                price=limit_price,
                action="BUY",
                orderType="LMT",
                enforce="DAY",
                quant=self.SHARES_PER_TRADE,
            )

            if order:
                logger.info(
                    f"  🟡 WEBULL PAPER BUY: {symbol} x{self.SHARES_PER_TRADE} "
                    f"@ ${limit_price} limit"
                )
                return {
                    "success":    True,
                    "symbol":     symbol,
                    "shares":     self.SHARES_PER_TRADE,
                    "limit":      limit_price,
                    "order_id":   order.get("orderId", ""),
                    "order":      order,
                }
            else:
                logger.warning(f"  Webull order returned empty for {symbol}")
                return {"success": False, "error": "Empty order response"}

        except Exception as e:
            logger.error(f"  Webull BUY error [{symbol}]: {e}")
            return {"success": False, "error": str(e)}

    def close_half(self, symbol: str, price: float, shares: int) -> dict:
        """
        Place a SELL limit order for half the position at T1.
        """
        if not self._logged_in and not self._connect():
            return {"success": False, "error": "Not connected to Webull"}

        half = shares // 2
        # Sell slightly below current to ensure fill
        limit_price = round(price - 0.02, 2)

        try:
            order = self._wb.place_order(
                stock=symbol,
                price=limit_price,
                action="SELL",
                orderType="LMT",
                enforce="DAY",
                quant=half,
            )
            if order:
                logger.info(
                    f"  🟡 WEBULL PAPER SELL HALF: {symbol} x{half} @ ${limit_price}"
                )
                return {"success": True, "shares_sold": half, "limit": limit_price}
            return {"success": False, "error": "Empty order response"}

        except Exception as e:
            logger.error(f"  Webull SELL HALF error [{symbol}]: {e}")
            return {"success": False, "error": str(e)}

    def close_all(self, symbol: str, price: float, shares: int) -> dict:
        """
        Place a SELL limit order for all remaining shares.
        Used for stop hits, red candle exits, EOD close.
        """
        if not self._logged_in and not self._connect():
            return {"success": False, "error": "Not connected to Webull"}

        # For stop exits, sell below current to ensure fill
        limit_price = round(price - 0.05, 2)

        try:
            order = self._wb.place_order(
                stock=symbol,
                price=limit_price,
                action="SELL",
                orderType="LMT",
                enforce="DAY",
                quant=shares,
            )
            if order:
                logger.info(
                    f"  🟡 WEBULL PAPER SELL ALL: {symbol} x{shares} @ ${limit_price}"
                )
                return {"success": True, "shares_sold": shares, "limit": limit_price}
            return {"success": False, "error": "Empty order response"}

        except Exception as e:
            logger.error(f"  Webull SELL ALL error [{symbol}]: {e}")
            return {"success": False, "error": str(e)}

    def get_account_info(self) -> dict:
        """Get paper account balance and positions."""
        if not self._logged_in and not self._connect():
            return {}
        try:
            return self._wb.get_account() or {}
        except Exception as e:
            logger.error(f"Webull account info error: {e}")
            return {}

    def cancel_all_orders(self, symbol: str):
        """Cancel any open orders for a symbol before placing new ones."""
        if not self._logged_in:
            return
        try:
            orders = self._wb.get_current_orders() or []
            for order in orders:
                if order.get("symbol") == symbol:
                    self._wb.cancel_order(order.get("orderId", ""))
        except Exception as e:
            logger.debug(f"Cancel orders error: {e}")
