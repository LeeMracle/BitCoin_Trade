"""VB(변동성 돌파) 전략 — 9년 전체 데이터 재검증 백테스트.

기간:
  IS  (In-Sample)     : 2017-10-01 ~ 2023-12-31
  OOS (Out-of-Sample) : 2024-01-01 ~ 2026-04-04

실행:
  PYTHONUTF8=1 python scripts/backtest_vb_9yr.py

출력:
  output/vb_9yr_backtest_result.md
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import duckdb

# ── 프로젝트 루트를 sys.path에 추가 ─────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics
from services.strategies.advanced import make_strategy_volatility_breakout

# ── 상수 ─────────────────────────────────────────────────────────────────────
DB_PATH   = ROOT / "data" / "cache.duckdb"
OUT_DIR   = ROOT / "output"
OUT_FILE  = OUT_DIR / "vb_9yr_backtest_result.md"

SYMBOL    = "BTC/KRW"
TIMEFRAME = "1d"

# 기간 경계
IS_START  = "2017-10-01"
IS_END    = "2023-12-31"
OOS_START = "2024-01-01"
OOS_END   = "2026-04-04"

# 워밍업: SMA(50) 계산용 — IS 시작 전 60일치 데이터 포함
WARMUP_DAYS = 60

# VB 파라미터 (config.py 기준)
K_BULL     = 0.4
K_NEUTRAL  = 0.5
K_BEAR     = 0.7
SL_PCT     = 0.020   # -2.0% 손절
SMA_PERIOD = 50


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _date_to_ms(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_ohlcv_from_db(start: str, end: str) -> pd.DataFrame:
    """DuckDB에서 BTC/KRW 일봉 데이터를 로드한다."""
    start_ms = _date_to_ms(start)
    end_ms   = _date_to_ms(end) + 86_400_000 - 1  # 종료일 포함

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = con.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE exchange='upbit' AND symbol=? AND timeframe=?
          AND ts >= ? AND ts <= ?
        ORDER BY ts
        """,
        [SYMBOL, TIMEFRAME, start_ms, end_ms],
    ).fetchall()
    con.close()

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df


def ts_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def run_backtest(df_full: pd.DataFrame, period_start: str, period_end: str) -> dict:
    """워밍업 포함 데이터로 백테스트를 실행하고, 해당 기간의 결과만 추출한다.

    engine 설계:
      - signal[i]=1 → i+1 봉 시가에 진입
      - signal[i]=0 → i+1 봉 시가에 청산
      - equity_rows는 i+1 봉 기준으로 기록됨

    워밍업 기간 이전 데이터를 앞에 붙여서 엔진에 전달하고,
    결과 equity_curve / trade_log에서 실제 기간만 필터링한다.
    """
    engine = BacktestEngine()
    strategy_fn = make_strategy_volatility_breakout(
        k_bull=K_BULL,
        k_neutral=K_NEUTRAL,
        k_bear=K_BEAR,
        sl_pct=SL_PCT,
        sma_period=SMA_PERIOD,
    )

    result = engine.run(strategy_fn, df_full)

    period_start_ms = _date_to_ms(period_start)
    period_end_ms   = _date_to_ms(period_end) + 86_400_000 - 1

    # equity_curve 필터
    eq = result.equity_curve.copy()
    eq_period = eq[(eq["ts"] >= period_start_ms) & (eq["ts"] <= period_end_ms)].reset_index(drop=True)

    # trade_log 필터 (진입 시점 기준)
    tl = result.trade_log.copy()
    tl_period = tl[(tl["entry_ts"] >= period_start_ms) & (tl["entry_ts"] <= period_end_ms)].reset_index(drop=True)

    return {
        "equity": eq_period,
        "trades": tl_period,
        "full_result": result,
    }


def compute_yearly_metrics(trades: pd.DataFrame) -> dict[int, dict]:
    """연도별 거래 집계."""
    if trades.empty:
        return {}

    trades = trades.copy()
    trades["year"] = trades["entry_ts"].apply(
        lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year
    )

    yearly = {}
    for year, grp in trades.groupby("year"):
        n = len(grp)
        wr = (grp["return_pct"] > 0).mean()
        avg_r = grp["return_pct"].mean()
        total_r = (1 + grp["return_pct"]).prod() - 1
        yearly[int(year)] = {
            "n_trades": n,
            "win_rate": round(float(wr), 4),
            "avg_trade_pct": round(float(avg_r) * 100, 3),
            "total_return_pct": round(float(total_r) * 100, 2),
        }
    return yearly


def period_summary(label: str, backtest: dict) -> dict:
    """기간 요약 메트릭 계산."""
    eq  = backtest["equity"]
    tl  = backtest["trades"]

    if eq.empty:
        return {"label": label, "error": "equity data empty"}

    metrics = compute_metrics(eq, tl)

    # 월 평균 거래수
    if not tl.empty:
        first_ms = tl["entry_ts"].min()
        last_ms  = tl["entry_ts"].max()
        months = max((last_ms - first_ms) / (1000 * 86400 * 30.44), 1)
        monthly_avg = len(tl) / months
    else:
        monthly_avg = 0.0

    return {
        "label": label,
        "sharpe":            metrics.sharpe,
        "mdd":               metrics.max_drawdown,
        "total_return":      metrics.total_return,
        "n_trades":          metrics.n_trades,
        "win_rate":          metrics.win_rate,
        "avg_trade_return":  metrics.avg_trade_return,
        "monthly_avg_trades": round(monthly_avg, 2),
        "calmar":            metrics.calmar,
    }


def build_report(is_sum: dict, oos_sum: dict,
                 is_yearly: dict, oos_yearly: dict,
                 is_start: str, is_end: str,
                 oos_start: str, oos_end: str) -> str:
    """마크다운 보고서 생성."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def pct(v: float) -> str:
        return f"{v * 100:.2f}%"

    def row(d: dict) -> str:
        return (
            f"| {d['label']} "
            f"| {d['sharpe']:.4f} "
            f"| {pct(d['mdd'])} "
            f"| {pct(d['total_return'])} "
            f"| {d['n_trades']} "
            f"| {pct(d['win_rate'])} "
            f"| {pct(d['avg_trade_return'])} "
            f"| {d['monthly_avg_trades']:.1f} "
            f"| {d['calmar']:.4f} |"
        )

    # 연도별 테이블 빌드
    all_years = sorted(set(list(is_yearly.keys()) + list(oos_yearly.keys())))
    yearly_rows = []
    for yr in all_years:
        segment = "IS" if yr <= 2023 else "OOS"
        data = is_yearly.get(yr) or oos_yearly.get(yr)
        if data:
            yearly_rows.append(
                f"| {yr} | {segment} "
                f"| {data['n_trades']} "
                f"| {data['win_rate']*100:.1f}% "
                f"| {data['avg_trade_pct']:.3f}% "
                f"| {data['total_return_pct']:.2f}% |"
            )

    yearly_table = "\n".join(yearly_rows) if yearly_rows else "| — | — | — | — | — | — |"

    report = f"""# VB(변동성 돌파) 전략 9년 재검증 백테스트 결과

생성일시: {now}

## 전략 파라미터

| 항목 | 값 |
|------|-----|
| K_bull (상승장) | {K_BULL} |
| K_neutral (중립) | {K_NEUTRAL} |
| K_bear (하락장) | {K_BEAR} |
| 손절 (sl_pct) | {SL_PCT * 100:.1f}% |
| SMA 기간 | {SMA_PERIOD} |
| 수수료 | 0.05% (왕복) |
| 슬리피지 | 5bp |

진입 조건: close > open + 전일(high−low) × K (레짐별 K 자동조절)
청산 조건: 1봉 보유 후 다음날 시가 청산 (또는 −{SL_PCT*100:.1f}% 손절)

## IS / OOS 요약

| 구간 | Sharpe | MDD | 총수익률 | 거래수 | 승률 | 평균거래 | 월평균거래 | Calmar |
|------|--------|-----|---------|--------|------|---------|-----------|--------|
{row(is_sum)}
{row(oos_sum)}

- IS  ({is_start} ~ {is_end}): {is_sum['n_trades']}건
- OOS ({oos_start} ~ {oos_end}): {oos_sum['n_trades']}건

## 연도별 성과

| 연도 | 구간 | 거래수 | 승률 | 평균거래수익 | 연간수익률 |
|------|------|--------|------|------------|----------|
{yearly_table}

## 해석 가이드

- **Sharpe > 1.0** : 위험 대비 수익 양호
- **MDD > -25%** : 최대낙폭 허용 범위
- OOS Sharpe가 IS 대비 0.7 이상이면 과적합 가능성 낮음
- 연도별 성과에서 일관성 확인 (특정 연도 쏠림 여부)

---
*백테스트 엔진: services/backtest/engine.py (bar-close 실행, 다음 봉 시가 체결)*
*데이터: DuckDB cache.duckdb — BTC/KRW 1d 업비트*
"""
    return report


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("VB 변동성 돌파 전략 — 9년 재검증 백테스트")
    print("=" * 60)

    # 1. 데이터 로드 ──────────────────────────────────────────────
    # IS용: 워밍업(60일) 포함해서 로드
    # 워밍업 기준점: IS_START - 60 영업일 ≈ 2017-10-01에서 역산
    # DuckDB 최초 데이터가 2017-10-01이므로, 워밍업 없이 전체 로드 후 사용
    print(f"\n[1] 데이터 로드 중...")
    print(f"    IS  : {IS_START} ~ {IS_END}")
    print(f"    OOS : {OOS_START} ~ {OOS_END}")

    # 전체 데이터 로드 (IS + OOS 통합 — 워밍업 분리 처리용)
    df_all = load_ohlcv_from_db(IS_START, OOS_END)
    print(f"    전체 데이터: {len(df_all)}행  ({ts_to_date(df_all['ts'].iloc[0])} ~ {ts_to_date(df_all['ts'].iloc[-1])})")

    # 2. IS 백테스트 ──────────────────────────────────────────────
    print("\n[2] IS 백테스트 실행 중...")

    is_end_ms = _date_to_ms(IS_END) + 86_400_000 - 1
    df_is_full = df_all[df_all["ts"] <= is_end_ms].reset_index(drop=True)
    print(f"    IS 데이터: {len(df_is_full)}행")

    is_bt = run_backtest(df_is_full, IS_START, IS_END)
    is_sum = period_summary(f"IS ({IS_START}~{IS_END})", is_bt)
    is_yearly = compute_yearly_metrics(is_bt["trades"])
    print(f"    IS 완료 — Sharpe={is_sum['sharpe']:.4f}, MDD={is_sum['mdd']*100:.2f}%, 거래={is_sum['n_trades']}건")

    # 3. OOS 백테스트 ─────────────────────────────────────────────
    print("\n[3] OOS 백테스트 실행 중...")

    oos_start_ms = _date_to_ms(OOS_START)
    # OOS는 IS 마지막 구간(SMA 워밍업)을 앞에 붙여서 실행
    # SMA(50) 계산을 위해 OOS_START 이전 WARMUP_DAYS 행 포함
    oos_idx_start = df_all[df_all["ts"] >= oos_start_ms].index.min()
    warmup_idx = max(0, oos_idx_start - WARMUP_DAYS)
    df_oos_full = df_all.iloc[warmup_idx:].reset_index(drop=True)
    print(f"    OOS 데이터 (워밍업 {WARMUP_DAYS}일 포함): {len(df_oos_full)}행")

    oos_bt = run_backtest(df_oos_full, OOS_START, OOS_END)
    oos_sum = period_summary(f"OOS ({OOS_START}~{OOS_END})", oos_bt)
    oos_yearly = compute_yearly_metrics(oos_bt["trades"])
    print(f"    OOS 완료 — Sharpe={oos_sum['sharpe']:.4f}, MDD={oos_sum['mdd']*100:.2f}%, 거래={oos_sum['n_trades']}건")

    # 4. 보고서 저장 ──────────────────────────────────────────────
    print("\n[4] 보고서 생성 중...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    report_md = build_report(
        is_sum, oos_sum,
        is_yearly, oos_yearly,
        IS_START, IS_END,
        OOS_START, OOS_END,
    )

    OUT_FILE.write_text(report_md, encoding="utf-8")
    print(f"    저장 완료: {OUT_FILE}")

    # 5. 콘솔 요약 출력 ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("최종 요약")
    print("=" * 60)
    print(f"{'구간':<40} {'Sharpe':>8} {'MDD':>9} {'총수익':>10} {'거래':>6} {'승률':>7}")
    print("-" * 60)
    for s in [is_sum, oos_sum]:
        print(
            f"{s['label']:<40} "
            f"{s['sharpe']:>8.4f} "
            f"{s['mdd']*100:>8.2f}% "
            f"{s['total_return']*100:>9.2f}% "
            f"{s['n_trades']:>6} "
            f"{s['win_rate']*100:>6.1f}%"
        )
    print("=" * 60)

    print("\n연도별 거래 요약:")
    print(f"{'연도':<6} {'구간':<5} {'거래':>5} {'승률':>7} {'평균수익':>10} {'연간수익':>10}")
    print("-" * 50)
    all_years = sorted(set(list(is_yearly.keys()) + list(oos_yearly.keys())))
    for yr in all_years:
        segment = "IS" if yr <= 2023 else "OOS"
        data = is_yearly.get(yr) or oos_yearly.get(yr)
        if data:
            print(
                f"{yr:<6} {segment:<5} "
                f"{data['n_trades']:>5} "
                f"{data['win_rate']*100:>6.1f}% "
                f"{data['avg_trade_pct']:>9.3f}% "
                f"{data['total_return_pct']:>9.2f}%"
            )

    print(f"\n보고서 경로: {OUT_FILE}")


if __name__ == "__main__":
    main()
