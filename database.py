"""
database.py -- VISION Trade Logging
=====================================
Uses PostgreSQL/Supabase via VISION_DATABASE_URL env var.
Falls back to SQLite if no DATABASE_URL is set.

Separate from Merlin's database — VISION has its own Supabase project.

Tables:
  vision_trades  -- every paper trade open/close with full context
  kv_store       -- session state persistence (survives Render restarts)

Trade fields tracked:
  symbol, entry_price, exit_price, shares
  stop_loss, target1, target2
  rvol, gap_pct, float_m, has_news, bull_flag
  above_vwap, above_ema9, score
  entry_time, exit_time, exit_reason
  pnl_usd, result (win/loss/breakeven)
  hour_of_day, day_of_week
"""

import os
import logging
import sqlite3

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("VISION_DATABASE_URL", "")
_USE_SQLITE  = not DATABASE_URL

if not _USE_SQLITE:
    try:
        import psycopg2
        import psycopg2.extras
        _PG_AVAILABLE = True
    except ImportError:
        logger.warning("psycopg2 not installed -- falling back to SQLite")
        _USE_SQLITE    = True
        _PG_AVAILABLE  = False
else:
    _PG_AVAILABLE = False

SQLITE_PATH = "vision_trades.db"


# ── Connection helpers ────────────────────────────────────────────────────────

def _pg_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)

def _sqlite_conn():
    return sqlite3.connect(SQLITE_PATH)


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db():
    if _USE_SQLITE:
        _init_sqlite()
    else:
        _init_pg()
    _init_kv_table()
    _migrate_v21()
    logger.info(f"VISION DB initialised ({'SQLite' if _USE_SQLITE else 'PostgreSQL'})")


def _migrate_v21():
    """Add v21 columns if they don't exist — safe to run on existing deployments."""
    if _USE_SQLITE:
        try:
            conn = _sqlite_conn()
            cols = [r[1] for r in conn.execute("PRAGMA table_info(vision_trades)").fetchall()]
            for col, defn in [
                ("alert_type",     "TEXT DEFAULT 'bull_flag'"),
                ("extended",       "INTEGER DEFAULT 0"),
                ("chart_snapshot", "TEXT DEFAULT NULL"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE vision_trades ADD COLUMN {col} {defn}")
                    logger.info(f"v21 migration: added {col} (SQLite)")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"v21 migration (sqlite): {e}")
    else:
        try:
            conn = _pg_conn()
            cur  = conn.cursor()
            for col, defn in [
                ("alert_type",     "VARCHAR(20) DEFAULT 'bull_flag'"),
                ("extended",       "BOOLEAN DEFAULT FALSE"),
                ("chart_snapshot", "TEXT"),
            ]:
                cur.execute(f"ALTER TABLE vision_trades ADD COLUMN IF NOT EXISTS {col} {defn}")
            conn.commit()
            cur.close()
            conn.close()
            logger.info("v21 migration: verified new columns exist (PostgreSQL)")
        except Exception as e:
            logger.error(f"v21 migration (pg): {e}")


def _init_sqlite():
    try:
        conn = _sqlite_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vision_trades (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_time    TEXT DEFAULT (datetime('now')),
                exit_time     TEXT DEFAULT NULL,
                symbol        TEXT,
                entry_price   REAL,
                exit_price    REAL DEFAULT NULL,
                shares        INTEGER,
                stop_loss     REAL,
                target1       REAL,
                target2       REAL,
                rvol          REAL,
                gap_pct       REAL,
                float_m       REAL,
                has_news      INTEGER DEFAULT 0,
                bull_flag     INTEGER DEFAULT 0,
                above_vwap    INTEGER DEFAULT 0,
                above_ema9    INTEGER DEFAULT 0,
                score         REAL,
                pnl_usd       REAL DEFAULT NULL,
                result        TEXT DEFAULT NULL,
                exit_reason   TEXT DEFAULT NULL,
                hour_of_day   INTEGER DEFAULT NULL,
                day_of_week   INTEGER DEFAULT NULL,
                alert_type    TEXT DEFAULT 'bull_flag',
                extended      INTEGER DEFAULT 0,
                chart_snapshot TEXT DEFAULT NULL
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"SQLite init error: {e}")


def _init_pg():
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vision_trades (
                id            SERIAL PRIMARY KEY,
                entry_time    TIMESTAMPTZ DEFAULT NOW(),
                exit_time     TIMESTAMPTZ DEFAULT NULL,
                symbol        VARCHAR(20),
                entry_price   FLOAT,
                exit_price    FLOAT DEFAULT NULL,
                shares        INTEGER,
                stop_loss     FLOAT,
                target1       FLOAT,
                target2       FLOAT,
                rvol          FLOAT,
                gap_pct       FLOAT,
                float_m       FLOAT,
                has_news      BOOLEAN DEFAULT FALSE,
                bull_flag     BOOLEAN DEFAULT FALSE,
                above_vwap    BOOLEAN DEFAULT FALSE,
                above_ema9    BOOLEAN DEFAULT FALSE,
                score         FLOAT,
                pnl_usd       FLOAT DEFAULT NULL,
                result        VARCHAR(20) DEFAULT NULL,
                exit_reason   TEXT DEFAULT NULL,
                hour_of_day   INTEGER DEFAULT NULL,
                day_of_week   INTEGER DEFAULT NULL,
                alert_type    VARCHAR(20) DEFAULT 'bull_flag',
                extended      BOOLEAN DEFAULT FALSE,
                chart_snapshot TEXT DEFAULT NULL
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"PostgreSQL init error: {e}")


# ── Trade CRUD ────────────────────────────────────────────────────────────────

def insert_trade(data: dict) -> int | None:
    """Insert a new trade on open. Returns trade ID."""
    if _USE_SQLITE:
        return _insert_sqlite(data)
    return _insert_pg(data)


def _insert_sqlite(data: dict):
    import json
    try:
        conn = _sqlite_conn()
        cur  = conn.execute("""
            INSERT INTO vision_trades (
                symbol, entry_price, shares, stop_loss, target1, target2,
                rvol, gap_pct, float_m, has_news, bull_flag,
                above_vwap, above_ema9, score,
                hour_of_day, day_of_week,
                alert_type, extended, chart_snapshot
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("symbol"),       data.get("entry_price"),
            data.get("shares", 100),  data.get("stop_loss"),
            data.get("target1"),      data.get("target2"),
            data.get("rvol", 0),      data.get("gap_pct", 0),
            data.get("float_m", 0),   int(data.get("has_news", False)),
            int(data.get("bull_flag", False)),
            int(data.get("above_vwap", False)),
            int(data.get("above_ema9", False)),
            data.get("score", 0),
            data.get("hour_of_day"),  data.get("day_of_week"),
            data.get("alert_type", "bull_flag"),
            int(data.get("extended", False)),
            json.dumps(data.get("chart_snapshot", {})),
        ))
        trade_id = cur.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Trade logged id={trade_id} [{data.get('symbol')}]")
        return trade_id
    except Exception as e:
        logger.error(f"insert_trade (sqlite) error: {e}")
        return None


def _insert_pg(data: dict):
    import json
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO vision_trades (
                symbol, entry_price, shares, stop_loss, target1, target2,
                rvol, gap_pct, float_m, has_news, bull_flag,
                above_vwap, above_ema9, score,
                hour_of_day, day_of_week,
                alert_type, extended, chart_snapshot
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            data.get("symbol"),       data.get("entry_price"),
            data.get("shares", 100),  data.get("stop_loss"),
            data.get("target1"),      data.get("target2"),
            data.get("rvol", 0),      data.get("gap_pct", 0),
            data.get("float_m", 0),   data.get("has_news", False),
            data.get("bull_flag", False),
            data.get("above_vwap", False),
            data.get("above_ema9", False),
            data.get("score", 0),
            data.get("hour_of_day"),  data.get("day_of_week"),
            data.get("alert_type", "bull_flag"),
            data.get("extended", False),
            json.dumps(data.get("chart_snapshot", {})),
        ))
        trade_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Trade logged id={trade_id} [{data.get('symbol')}]")
        return trade_id
    except Exception as e:
        logger.error(f"insert_trade (pg) error: {e}")
        return None


def update_trade_result(trade_id, exit_price, pnl_usd, result, exit_reason):
    """Update trade on close with exit price, PnL, result."""
    if _USE_SQLITE:
        return _update_sqlite(trade_id, exit_price, pnl_usd, result, exit_reason)
    return _update_pg(trade_id, exit_price, pnl_usd, result, exit_reason)


def _update_sqlite(trade_id, exit_price, pnl_usd, result, exit_reason):
    try:
        conn = _sqlite_conn()
        conn.execute("""
            UPDATE vision_trades
            SET exit_time=datetime('now'), exit_price=?, pnl_usd=?,
                result=?, exit_reason=?
            WHERE id=?
        """, (exit_price, pnl_usd, result, exit_reason, trade_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"update_trade (sqlite) error: {e}")


def _update_pg(trade_id, exit_price, pnl_usd, result, exit_reason):
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE vision_trades
            SET exit_time=NOW(), exit_price=%s, pnl_usd=%s,
                result=%s, exit_reason=%s
            WHERE id=%s
        """, (exit_price, pnl_usd, result, exit_reason, trade_id))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"update_trade (pg) error: {e}")


def get_all_trades() -> list:
    if _USE_SQLITE:
        try:
            conn = _sqlite_conn()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM vision_trades ORDER BY entry_time DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_all_trades (sqlite): {e}")
            return []
    try:
        conn = _pg_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM vision_trades ORDER BY entry_time DESC")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"get_all_trades (pg): {e}")
        return []


# 60s cache to avoid hammering DB on every scan cycle
_trades_cache = {"data": [], "ts": 0.0}
_CACHE_TTL    = 60.0

def get_all_trades_cached() -> list:
    import time as _t
    if _t.time() - _trades_cache["ts"] < _CACHE_TTL and _trades_cache["data"]:
        return _trades_cache["data"]
    fresh = get_all_trades()
    _trades_cache["data"] = fresh
    _trades_cache["ts"]   = _t.time()
    return fresh


# ── KV Store (session state) ──────────────────────────────────────────────────

def _init_kv_table():
    if _USE_SQLITE:
        try:
            conn = _sqlite_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key        TEXT PRIMARY KEY,
                    value      TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"kv_store init (sqlite): {e}")
    else:
        try:
            conn = _pg_conn()
            cur  = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key        VARCHAR(100) PRIMARY KEY,
                    value      TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"kv_store init (pg): {e}")


def save_state(key: str, state_dict: dict):
    import json
    value = json.dumps(state_dict)
    if _USE_SQLITE:
        try:
            conn = _sqlite_conn()
            conn.execute("""
                INSERT OR REPLACE INTO kv_store (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
            """, (key, value))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"save_state (sqlite): {e}")
    else:
        try:
            conn = _pg_conn()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO kv_store (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=NOW()
            """, (key, value, value))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"save_state (pg): {e}")


def load_state(key: str) -> dict:
    import json
    if _USE_SQLITE:
        try:
            conn = _sqlite_conn()
            row  = conn.execute(
                "SELECT value FROM kv_store WHERE key=?", (key,)
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception as e:
            logger.error(f"load_state (sqlite): {e}")
        return {}
    try:
        conn = _pg_conn()
        cur  = conn.cursor()
        cur.execute("SELECT value FROM kv_store WHERE key=%s", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        logger.error(f"load_state (pg): {e}")
    return {}
