"""실데이터 학습용 데이터셋 빌더.

흐름:
    1. cache.duckdb에서 다수 심볼 4h OHLCV 로드
    2. 각 심볼에서 DC(period) 돌파 시점 추출 (entry candidates)
    3. 각 entry에 대해:
        - label_one()으로 +5%(slippage 포함)/horizon 도달 여부 → 라벨
        - compute_features()로 동일 시점 feature 계산
    4. feature_store에 누적 저장

학습 데이터 timeframe = 4h. label horizon은 6×4h = 24h 단위로 다시 환산:
    - LABEL_HORIZON_BARS=24 (4일치 = 24×4h)
    - bars_per_day는 features.py 내부 96(15분) 가정이지만, 학습/추론이 동일
      함수를 쓰므로 모델 입장에선 일관된 컨텍스트.

사용:
    PYTHONUTF8=1 python scripts/ml_build_dataset.py             # 기본 (BTC+10 알트)
    PYTHONUTF8=1 python scripts/ml_build_dataset.py --reset     # feature_store 초기화 후 재생성
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import feature_store, labeler  # noqa: E402
from services.ml.config import FEATURE_DIR, LABEL_HORIZON_BARS, ensure_dirs  # noqa: E402
from services.ml.features import MarketContext, compute_features  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ml_build_dataset")

DEFAULT_SYMBOLS = [
    "BTC/KRW", "ETH/KRW", "XRP/KRW", "SOL/KRW", "ADA/KRW",
    "DOGE/KRW", "DOT/KRW", "AVAX/KRW", "ATOM/KRW", "LINK/KRW", "NEAR/KRW",
]


def load_ohlcv_4h(con, symbol: str) -> pd.DataFrame:
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '4h' ORDER BY ts",
        [symbol],
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def load_fear_greed(con) -> pd.Series:
    """F&G 일별 시계열 (date → value). UTC 자정 기준."""
    rows = con.execute(
        "SELECT date, value FROM macro WHERE series_id='FEAR_GREED' ORDER BY date"
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        data=[float(v) for _, v in rows],
        index=pd.to_datetime([d for d, _ in rows], utc=True),
    )
    return s


def lookup_fg(fg: pd.Series, at_ts: pd.Timestamp, default: int = 50) -> int:
    """at_ts 이전 가장 최근 F&G 값 (lookahead 차단)."""
    if fg.empty:
        return default
    sub = fg[fg.index <= at_ts]
    if sub.empty:
        return default
    return int(sub.iloc[-1])


def detect_dc_breakouts(df: pd.DataFrame, period: int = 15) -> list[pd.Timestamp]:
    """DC(period) 상단 돌파 시점 (close 기준)."""
    if len(df) < period + 50:
        return []
    dc_upper = df["high"].shift(1).rolling(period).max()
    cross = (df["close"] > dc_upper) & (df["close"].shift(1) <= dc_upper.shift(1))
    return df.index[cross.fillna(False)].tolist()


def build_for_symbol(
    con,
    symbol: str,
    btc_df: pd.DataFrame,
    fg_series: pd.Series,
    *,
    dc_period: int = 15,
    label_horizon: int = 24,        # 4h × 24 = 4일
    target_pct: float = 0.05,
    slippage_pct: float = 0.002,
) -> list[dict]:
    df = load_ohlcv_4h(con, symbol)
    if df.empty:
        return []
    breakouts = detect_dc_breakouts(df, dc_period)
    log.info("  %s: %d 봉, %d 돌파", symbol, len(df), len(breakouts))

    rows: list[dict] = []
    for at_ts in breakouts:
        entry_price = float(df.loc[at_ts, "close"])
        target_price = entry_price * (1.0 + target_pct + slippage_pct)
        forward = df[df.index > at_ts].head(label_horizon)
        if len(forward) < label_horizon // 2:
            continue
        label = int((forward["high"] >= target_price).any())

        # BTC 30일 추세 (해당 시점 직전 30일 수익률)
        btc_window = btc_df[btc_df.index <= at_ts].tail(180)  # 30일 = 4h × 180
        btc_trend = 0.0
        btc_corr = 0.0
        if len(btc_window) >= 50:
            btc_trend = float((btc_window["close"].iloc[-1] / btc_window["close"].iloc[0]) - 1.0)
            # v2: 동일 기간 코인 close와 BTC close 상관 (return 기준)
            coin_window = df[df.index <= at_ts].tail(180)
            join = pd.concat([coin_window["close"].rename("c"), btc_window["close"].rename("b")], axis=1, join="inner")
            if len(join) >= 30:
                ret = join.pct_change().dropna()
                if len(ret) >= 20:
                    corr = ret["c"].corr(ret["b"])
                    btc_corr = float(corr) if pd.notna(corr) else 0.0

        ctx = MarketContext(
            btc_trend_30d=btc_trend,
            btc_dominance=50.0,             # placeholder (외부 API 미연동)
            fear_greed=lookup_fg(fg_series, at_ts),
            btc_corr_30d=btc_corr,
            market_cap_rank=1 if "BTC" in symbol else 99,
            days_since_listing=2000,
        )

        try:
            feat = compute_features(symbol.replace("/", "-"), df, at_ts, ctx)
        except ValueError:
            continue

        rows.append({
            "symbol": symbol.replace("/", "-"),
            "signal_ts": at_ts,
            "label": label,
            "label_horizon_bars": label_horizon,
            **feat,
        })
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    p.add_argument("--reset", action="store_true", help="feature_store 초기화")
    p.add_argument("--dc-period", type=int, default=15)
    p.add_argument("--label-horizon", type=int, default=24, help="4h봉 기준 horizon (24=4일)")
    args = p.parse_args()

    ensure_dirs()
    if args.reset:
        for f in FEATURE_DIR.glob("*.parquet"):
            f.unlink()
        log.info("feature_store reset")

    con = duckdb.connect("data/cache.duckdb", read_only=True)
    btc_df = load_ohlcv_4h(con, "BTC/KRW")
    fg_series = load_fear_greed(con)
    log.info("BTC 4h: %d 봉 [%s ~ %s]", len(btc_df), btc_df.index[0], btc_df.index[-1])
    log.info("F&G: %d 일치 [%s ~ %s]" if not fg_series.empty else "F&G: 없음",
             *((len(fg_series), str(fg_series.index[0]), str(fg_series.index[-1])) if not fg_series.empty else ()))

    total_rows = 0
    for sym in args.symbols:
        rows = build_for_symbol(
            con, sym, btc_df, fg_series,
            dc_period=args.dc_period,
            label_horizon=args.label_horizon,
        )
        if rows:
            n = labeler.persist(rows)
            total_rows += n
            pos = sum(r["label"] for r in rows)
            log.info("    저장 %d, positive %d (%.1f%%)", n, pos, 100 * pos / max(len(rows), 1))

    log.info("=" * 60)
    log.info("총 저장: %d rows | stats=%s", total_rows, feature_store.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
