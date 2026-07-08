"""SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    turnover_rate REAL,
    amount REAL,
    source TEXT DEFAULT 'akshare',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS indicators (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ma5 REAL,
    ma10 REAL,
    ma20 REAL,
    ma60 REAL,
    ma120 REAL,
    ema12 REAL,
    ema26 REAL,
    macd REAL,
    macd_signal REAL,
    macd_hist REAL,
    rsi14 REAL,
    k REAL,
    d REAL,
    j REAL,
    boll_mid REAL,
    boll_upper REAL,
    boll_lower REAL,
    volume_ratio REAL,
    cci20 REAL,
    williams_r14 REAL,
    plus_di14 REAL,
    minus_di14 REAL,
    adx14 REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS capital_flow (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    main_net_inflow REAL,
    super_large_net REAL,
    large_net REAL,
    medium_net REAL,
    small_net REAL,
    northbound_hold_shares REAL,
    northbound_hold_ratio REAL,
    margin_balance REAL,
    short_balance REAL,
    on_lhb INTEGER DEFAULT 0,
    source TEXT DEFAULT 'akshare',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    symbol TEXT,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_breadth (
    trade_date TEXT NOT NULL PRIMARY KEY,
    rising_count INTEGER,
    falling_count INTEGER,
    flat_count INTEGER,
    limit_up INTEGER,
    limit_down INTEGER,
    activity_pct REAL,
    raw_json TEXT,
    source TEXT DEFAULT 'akshare',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT,
    quantity REAL NOT NULL DEFAULT 0,
    cost_price REAL,
    buy_date TEXT,
    asset_type TEXT DEFAULT 'stock',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL
);
"""

_INDICATOR_MIGRATION_COLUMNS = (
    "cci20 REAL",
    "williams_r14 REAL",
    "plus_di14 REAL",
    "minus_di14 REAL",
    "adx14 REAL",
)


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add indicator columns introduced after initial schema."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(indicators)").fetchall()}
    for col_def in _INDICATOR_MIGRATION_COLUMNS:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE indicators ADD COLUMN {col_def}")
    migrate_chat_sessions(conn)


def migrate_chat_sessions(conn: sqlite3.Connection) -> None:
    """Ensure chat session tables/columns exist; migrate legacy flat messages."""
    from datetime import datetime, timezone

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "session_id" not in msg_cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN session_id INTEGER")

    orphan = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_messages WHERE session_id IS NULL"
    ).fetchone()[0]
    if orphan > 0:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO chat_sessions (title, summary, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("历史对话", "", now, now),
        )
        legacy_id = cur.lastrowid
        conn.execute(
            "UPDATE chat_messages SET session_id = ? WHERE session_id IS NULL",
            (legacy_id,),
        )
        active = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", ("active_chat_session_id",)
        ).fetchone()
        if not active:
            conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                ("active_chat_session_id", str(legacy_id)),
            )


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> Path:
    path = db_path or DEFAULT_DB_PATH
    with get_connection(path) as conn:
        conn.executescript(SCHEMA)
        migrate_schema(conn)
        conn.commit()
    return path
