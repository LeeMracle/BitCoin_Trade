"""shadow JSONL → feature_store 변환 어댑터 (ADR 20260515-1 Phase A-4 초안).

목적:
    실거래 매수 결정(`workspace/ml_shadow/*.jsonl`) + outcome 매칭 결과를
    학습용 feature_store(`data/features/*.parquet`)에 누적 저장.

흐름:
    1. shadow JSONL 로드 — `kind != "outcome"` (decision) 라인만 후보
    2. 동일 파일에서 (signal_ts, symbol)로 outcome 라인 join — outcome 없으면 skip
    3. signal_ts 시점의 OHLCV(4h, ccxt) 재fetch → `compute_features()` 재계산
        - shadow.log_decision은 feature를 저장하지 않음 — 모델 신뢰성 위해 재계산이 원칙
        - 시장 컨텍스트(BTC trend, F&G)도 재구성 (label과 동일한 시점 기준, lookahead 차단)
    4. label = `reached_target` (1/0)
    5. `feature_store.write_rows()` 누적 — `(symbol, signal_ts)` 키로 dedup

Phase 운영:
    - Phase A (5/15~5/26): `--dry-run`만 실행, 변환 가능 행 수만 카운트
    - Phase B (5/27~): cron 매일 03:30 KST (`ml_outcome_match.py` 03:00 직후)
    - 6/4 v3 재학습 시 백테스트 데이터(`ml_build_dataset.py`)와 합쳐서 학습

사용:
    PYTHONUTF8=1 python scripts/ml_shadow_to_features.py --dry-run
    PYTHONUTF8=1 python scripts/ml_shadow_to_features.py --since 20260501
    PYTHONUTF8=1 python scripts/ml_shadow_to_features.py --since 20260501 --commit

⚠️ 주의:
    - `--commit` 없으면 dry-run (변환만, parquet 저장 안 함)
    - shadow 표본은 작아서 학습 효과 제한적 — 백테스트 데이터의 보조 역할
    - outcome_pct는 4h봉 high 기반 (실 체결가 X) — Phase B에서 trade_log 매칭 보강 검토
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import feature_store  # noqa: E402
from services.ml.config import LABEL_HORIZON_BARS, SHADOW_LOG_DIR, ensure_dirs  # noqa: E402
from services.ml.features import MarketContext, compute_features  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ml_shadow_to_features")

DEFAULT_TIMEFRAME = "4h"
DEFAULT_BACKFILL_BARS = 250  # feature 계산용 (RSI/EMA200 안정화)
LABEL_HORIZON_HOURS_4H = 24  # label은 outcome 결과 사용, horizon은 메타로만 기록


def _load_decisions_with_outcome(path: Path) -> list[dict]:
    """단일 JSONL에서 decision + outcome을 (signal_ts, symbol)로 join.

    outcome 없는 decision은 skip (학습에 라벨 필요).
    Returns: [{symbol, signal_ts(str), reached_target, outcome_pct, ...}, ...]
    """
    if not path.exists():
        return []
    decisions: dict[tuple[str, str], dict] = {}
    outcomes: dict[tuple[str, str], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        key = (rec.get("signal_ts", ""), rec.get("symbol", ""))
        if rec.get("kind") == "outcome":
            outcomes[key] = rec
        else:
            decisions[key] = rec

    joined: list[dict] = []
    for key, dec in decisions.items():
        oc = outcomes.get(key)
        if oc is None:
            continue
        joined.append({
            "symbol": dec.get("symbol"),
            "signal_ts": dec.get("signal_ts"),
            "signal_type": dec.get("signal_type"),
            "score_at_decision": dec.get("score"),
            "will_buy": dec.get("will_buy"),
            "reached_target": bool(oc.get("reached_target")),
            "outcome_pct": float(oc.get("outcome_pct", 0.0)),
            "horizon_bars_actual": int(oc.get("horizon_bars_actual", 0)),
        })
    return joined


def _ccxt_symbol(symbol: str) -> str:
    """KRW-BTC → BTC/KRW (ccxt 형식). 이미 형식이면 통과."""
    if "/" in symbol:
        return symbol
    if "-" in symbol:
        a, b = symbol.split("-", 1)
        return f"{b}/{a}" if a in ("KRW", "USDT", "BTC") else f"{a}/{b}"
    return symbol


def _fetch_ohlcv_for_features(
    ex: ccxt.upbit,
    ccxt_sym: str,
    signal_ts: pd.Timestamp,
    n_bars: int = DEFAULT_BACKFILL_BARS,
) -> pd.DataFrame:
    """signal_ts 직전 n_bars개 4h봉 OHLCV — feature 계산용 backfill."""
    end_ms = int(signal_ts.timestamp() * 1000)
    since_ms = end_ms - n_bars * 4 * 3600 * 1000
    bars = ex.fetch_ohlcv(ccxt_sym, timeframe=DEFAULT_TIMEFRAME, since=since_ms, limit=n_bars + 5)
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[df.index <= signal_ts]  # lookahead 차단


def _build_market_context(
    btc_df: pd.DataFrame,
    fg_series: pd.Series,
    signal_ts: pd.Timestamp,
    coin_df: pd.DataFrame,
    is_btc: bool,
) -> MarketContext:
    """ml_build_dataset.py와 동일 로직 (학습/추론 정합)."""
    btc_window = btc_df[btc_df.index <= signal_ts].tail(180)  # 30일 = 4h × 180
    btc_trend = 0.0
    btc_corr = 0.0
    if len(btc_window) >= 50:
        btc_trend = float((btc_window["close"].iloc[-1] / btc_window["close"].iloc[0]) - 1.0)
        coin_window = coin_df[coin_df.index <= signal_ts].tail(180)
        join = pd.concat(
            [coin_window["close"].rename("c"), btc_window["close"].rename("b")],
            axis=1, join="inner",
        )
        if len(join) >= 30:
            ret = join.pct_change().dropna()
            if len(ret) >= 20:
                corr = ret["c"].corr(ret["b"])
                btc_corr = float(corr) if pd.notna(corr) else 0.0

    fg_val = 50
    if not fg_series.empty:
        sub = fg_series[fg_series.index <= signal_ts]
        if not sub.empty:
            fg_val = int(sub.iloc[-1])

    return MarketContext(
        btc_trend_30d=btc_trend,
        btc_dominance=50.0,                     # placeholder (외부 API 미연동)
        fear_greed=fg_val,
        btc_corr_30d=btc_corr,
        market_cap_rank=1 if is_btc else 99,
        days_since_listing=2000,
    )


def _load_btc_and_fg() -> tuple[pd.DataFrame, pd.Series]:
    """학습 데이터 컨텍스트와 동일 — cache.duckdb에서 BTC 4h + F&G 로드."""
    db_path = ROOT / "data" / "cache.duckdb"
    if not db_path.exists():
        log.warning("cache.duckdb 없음 — BTC 컨텍스트 0으로 대체")
        return pd.DataFrame(), pd.Series(dtype=float)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        btc_rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol='BTC/KRW' AND timeframe='4h' ORDER BY ts"
        ).fetchall()
        fg_rows = con.execute(
            "SELECT date, value FROM macro WHERE series_id='FEAR_GREED' ORDER BY date"
        ).fetchall()
    finally:
        con.close()

    btc_df = pd.DataFrame()
    if btc_rows:
        btc_df = pd.DataFrame(btc_rows, columns=["ts", "open", "high", "low", "close", "volume"])
        btc_df["ts"] = pd.to_datetime(btc_df["ts"], unit="ms", utc=True)
        btc_df = btc_df.set_index("ts")

    fg_series = pd.Series(dtype=float)
    if fg_rows:
        fg_series = pd.Series(
            data=[float(v) for _, v in fg_rows],
            index=pd.to_datetime([d for d, _ in fg_rows], utc=True),
        )
    return btc_df, fg_series


def convert_date(
    date_str: str,
    *,
    btc_df: pd.DataFrame,
    fg_series: pd.Series,
    ex: ccxt.upbit,
    commit: bool,
) -> dict:
    """단일 날짜 JSONL 변환. dry-run이면 카운트만, commit=True면 feature_store 저장."""
    path = SHADOW_LOG_DIR / f"{date_str}.jsonl"
    pairs = _load_decisions_with_outcome(path)
    if not pairs:
        return {"date": date_str, "decisions_with_outcome": 0, "converted": 0, "saved": 0}

    rows: list[dict] = []
    skipped = 0
    for p in pairs:
        sym = p["symbol"]
        try:
            sig_ts = pd.Timestamp(p["signal_ts"])
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.tz_localize("UTC")
        except Exception:
            skipped += 1
            continue

        ccxt_sym = _ccxt_symbol(sym)
        try:
            coin_df = _fetch_ohlcv_for_features(ex, ccxt_sym, sig_ts)
        except Exception as e:
            log.warning("OHLCV fetch 실패 %s @%s: %s", ccxt_sym, sig_ts, e)
            skipped += 1
            continue
        if len(coin_df) < 50:
            skipped += 1
            continue

        ctx = _build_market_context(
            btc_df, fg_series, sig_ts, coin_df, is_btc=("BTC" in sym),
        )

        try:
            feat = compute_features(sym.replace("/", "-"), coin_df, sig_ts, ctx)
        except ValueError as e:
            log.debug("feature 계산 skip %s @%s: %s", sym, sig_ts, e)
            skipped += 1
            continue

        rows.append({
            "symbol": sym.replace("/", "-"),
            "signal_ts": sig_ts,
            "label": int(p["reached_target"]),
            "label_horizon_bars": LABEL_HORIZON_BARS,
            **feat,
        })

    saved = 0
    if rows and commit:
        saved = feature_store.write_rows(rows)
    return {
        "date": date_str,
        "decisions_with_outcome": len(pairs),
        "converted": len(rows),
        "skipped": skipped,
        "saved": saved,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="시작일 YYYYMMDD (미지정 시 최근 7일)")
    p.add_argument("--until", default=None, help="종료일 YYYYMMDD (미지정 시 어제)")
    p.add_argument("--commit", action="store_true",
                   help="feature_store에 실제 저장 (미지정 시 dry-run)")
    p.add_argument("--dry-run", action="store_true", help="명시적 dry-run (--commit 없으면 어차피 dry)")
    args = p.parse_args()

    ensure_dirs()

    # 날짜 범위
    today_utc = datetime.now(timezone.utc).date()
    until_d = datetime.strptime(args.until, "%Y%m%d").date() if args.until else (today_utc - timedelta(days=1))
    since_d = datetime.strptime(args.since, "%Y%m%d").date() if args.since else (until_d - timedelta(days=6))
    if since_d > until_d:
        log.error("since(%s) > until(%s)", since_d, until_d)
        return 2

    log.info("범위 [%s ~ %s] commit=%s", since_d, until_d, args.commit)

    btc_df, fg_series = _load_btc_and_fg()
    log.info("BTC 4h %d봉, F&G %d일", len(btc_df), len(fg_series))
    ex = ccxt.upbit({"enableRateLimit": True})

    total_pairs = total_converted = total_saved = 0
    cur = since_d
    while cur <= until_d:
        date_str = cur.strftime("%Y%m%d")
        r = convert_date(date_str, btc_df=btc_df, fg_series=fg_series, ex=ex, commit=args.commit)
        log.info("  %s → %s", date_str, r)
        total_pairs += r["decisions_with_outcome"]
        total_converted += r["converted"]
        total_saved += r["saved"]
        cur += timedelta(days=1)

    log.info("=" * 60)
    log.info(
        "총 outcome 매칭쌍 %d, feature 변환 %d, 저장 %d (commit=%s)",
        total_pairs, total_converted, total_saved, args.commit,
    )
    if not args.commit:
        log.info("⚠️ dry-run — 저장 안 함. 실제 적재는 --commit 필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
