"""DuckDB 기반 OHLCV 캐시 — 업비트 현물."""
import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "cache.duckdb"


def _conn() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def init_schema() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                exchange  VARCHAR NOT NULL,
                symbol    VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                ts        BIGINT  NOT NULL,
                open      DOUBLE  NOT NULL,
                high      DOUBLE  NOT NULL,
                low       DOUBLE  NOT NULL,
                close     DOUBLE  NOT NULL,
                volume    DOUBLE  NOT NULL,
                PRIMARY KEY (exchange, symbol, timeframe, ts)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS macro (
                series_id VARCHAR NOT NULL,
                date      VARCHAR NOT NULL,
                value     DOUBLE  NOT NULL,
                label     VARCHAR,
                PRIMARY KEY (series_id, date)
            )
        """)


def upsert_ohlcv(exchange: str, symbol: str, timeframe: str,
                 rows: list[dict]) -> None:
    if not rows:
        return
    with _conn() as con:
        con.executemany("""
            INSERT OR REPLACE INTO ohlcv
                (exchange, symbol, timeframe, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (exchange, symbol, timeframe,
             r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in rows
        ])


def select_ohlcv(exchange: str, symbol: str, timeframe: str,
                 start_ms: int, end_ms: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute("""
            SELECT ts, open, high, low, close, volume
            FROM ohlcv
            WHERE exchange=? AND symbol=? AND timeframe=?
              AND ts >= ? AND ts <= ?
            ORDER BY ts
        """, [exchange, symbol, timeframe, start_ms, end_ms]).fetchall()
    return [
        {"ts": r[0], "open": r[1], "high": r[2],
         "low": r[3], "close": r[4], "volume": r[5]}
        for r in rows
    ]


def upsert_macro(series_id: str, rows: list[dict]) -> None:
    if not rows:
        return
    with _conn() as con:
        con.executemany("""
            INSERT OR REPLACE INTO macro (series_id, date, value, label)
            VALUES (?, ?, ?, ?)
        """, [(series_id, r["date"], r["value"], r.get("label")) for r in rows])


def select_macro(series_id: str, start_date: str, end_date: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute("""
            SELECT date, value, label FROM macro
            WHERE series_id=? AND date >= ? AND date <= ?
            ORDER BY date
        """, [series_id, start_date, end_date]).fetchall()
    return [{"date": r[0], "value": r[1], "label": r[2]} for r in rows]
