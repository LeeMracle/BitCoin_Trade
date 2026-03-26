"""Phase 2 통합 스모크 테스트.

실행: python scripts/smoke_test.py
  1. BTC/KRW 1d 90일 OHLCV fetch (업비트 공개 API, 인증 불필요)
  2. 단순 MA 크로스오버 백테스트
  3. experiment_tracker에 결과 기록
  4. compare_runs 요약 출력
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# services/ 를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent / "services"))

from market_data.fetcher import fetch_ohlcv
from backtest.engine import BacktestEngine
from experiment_tracker.store import compare_runs, create_experiment, init_schema, log_run

import pandas as pd


def ma_crossover_strategy(df: pd.DataFrame) -> pd.Series:
    """단순 MA5 > MA20 → long, 아니면 flat."""
    close = df["close"]
    ma_fast = close.rolling(5).mean()
    ma_slow = close.rolling(20).mean()
    signal = (ma_fast > ma_slow).astype(int)
    return signal


async def main():
    end_dt = datetime.now(tz=timezone.utc)
    start_dt = end_dt - timedelta(days=90)
    start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[1] BTC/KRW 1d OHLCV fetch: {start} ~ {end}")
    ohlcv = await fetch_ohlcv("BTC/KRW", "1d", start, end, use_cache=True)
    print(f"    수신: {len(ohlcv)}개 캔들")
    assert len(ohlcv) > 20, "캔들 데이터 부족"

    df = pd.DataFrame(ohlcv)

    print("[2] MA 크로스오버 백테스트 실행")
    engine = BacktestEngine()
    result = engine.run(
        ma_crossover_strategy, df,
        params={"initial_capital": 10_000_000, "fee_rate": 0.0005, "slippage_bps": 5}
    )
    m = result.metrics
    print(f"    run_id : {result.run_id}")
    print(f"    수익률 : {m.total_return * 100:.2f}%")
    print(f"    Sharpe : {m.sharpe}")
    print(f"    MaxDD  : {m.max_drawdown * 100:.2f}%")
    print(f"    거래수 : {m.n_trades}")
    assert result.artifact_dir.exists(), "아티팩트 디렉토리 없음"

    print("[3] experiment_tracker에 결과 기록")
    db_path = Path(__file__).parent.parent / "data" / "experiments.db"
    init_schema(db_path)
    exp = create_experiment("smoke_test", "ma_crossover_v1", "Phase 2 스모크 테스트", db_path=db_path)
    artifact_paths = [str(p) for p in result.artifact_dir.iterdir()]
    log_run(
        experiment_id=exp["experiment_id"],
        run_id=result.run_id,
        params=result.config,
        metrics={
            "sharpe": m.sharpe,
            "calmar": m.calmar,
            "max_drawdown": m.max_drawdown,
            "total_return": m.total_return,
            "n_trades": m.n_trades,
            "win_rate": m.win_rate,
        },
        artifact_paths=artifact_paths,
        db_path=db_path,
    )

    print("[4] compare_runs 요약")
    summary = compare_runs(exp["experiment_id"], [], db_path=db_path)
    print(f"    best_run_id: {summary['best_run_id']}")
    print(f"    runs 수: {len(summary['runs'])}")

    print("\n[OK] Phase 2 스모크 테스트 완료")
    print(f"     아티팩트: {result.artifact_dir}")


if __name__ == "__main__":
    asyncio.run(main())
