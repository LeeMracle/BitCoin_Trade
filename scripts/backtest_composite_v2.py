# -*- coding: utf-8 -*-
"""composite v2 백테스트 비교 스크립트.

OOS 기간(2024-01~2026-03) BTC/KRW 단일 종목 기준으로 4가지 변형을 비교:
  v1  : 기존 composite (F&G 게이트 없음, BTC 필터 없음)
  v2a : F&G < 20 게이트만 추가
  v2b : BTC SMA(20) 필터만 추가
  v2c : F&G + BTC 둘 다 추가

실행:
  PYTHONUTF8=1 python scripts/backtest_composite_v2.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from services.market_data.fetcher import fetch_ohlcv, fetch_fear_greed
from services.strategies import get_strategy
from services.backtest.engine import BacktestEngine

# ── 설정 ──────────────────────────────────────────────────────────────────────
SYMBOL = "BTC/KRW"
OOS_START = "2024-01-01T00:00:00Z"
OOS_END   = "2026-03-31T23:59:59Z"
# F&G 히스토리 최대 조회일 (2023-05 이후 약 1000일)
FG_LIMIT_DAYS = 1100
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "workspace" / "runs" / "composite_v2_backtest.json"

# composite 기본 파라미터 (config.py 기준값과 동일하게 유지)
BASE_KWARGS = dict(
    dc_period=20,
    rsi_period=10,
    rsi_threshold=50.0,
    vol_ma=20,
    vol_mult=1.5,
    atr_period=14,
    vol_lookback=60,
)

BTC_SMA_PERIOD = 20


# ── F&G 히스토리 조회 ─────────────────────────────────────────────────────────
async def _fetch_fg_history() -> dict[str, float]:
    """날짜(YYYY-MM-DD) → F&G 값 딕셔너리 반환. 오류 시 빈 딕셔너리."""
    try:
        import aiohttp
        url = f"https://api.alternative.me/fng/?limit={FG_LIMIT_DAYS}&format=json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
        result = {}
        for item in data.get("data", []):
            d = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d")
            result[d] = float(item["value"])
        print(f"  F&G 히스토리 로드: {len(result)}일치")
        return result
    except Exception as e:
        print(f"  [경고] F&G 히스토리 조회 실패: {e} — F&G 게이트 비활성화")
        return {}


# ── BTC SMA(20) 위/아래 시리즈 계산 ──────────────────────────────────────────
def _calc_btc_sma_filter(df: pd.DataFrame, period: int = BTC_SMA_PERIOD) -> pd.Series:
    """각 날짜에 대해 BTC close > SMA(period)이면 True, 아니면 False.

    SMA가 아직 계산되지 않은 초기 봉은 True (매매 허용, 보수적 폴백).
    """
    sma = df["close"].rolling(window=period, min_periods=period).mean()
    above = df["close"] > sma
    # SMA 계산 불가 구간(nan)은 True로 채움
    above = above.fillna(True)
    return above


# ── 날짜 기반 신호 마스킹 (F&G + BTC SMA) ─────────────────────────────────────
def _apply_filters_to_signals(
    signal: pd.Series,
    df: pd.DataFrame,
    fg_history: dict[str, float],
    use_fg: bool,
    use_btc_sma: bool,
    btc_above_series: "pd.Series | None",
) -> pd.Series:
    """strategy_fn이 생성한 신호에 F&G 게이트와 BTC SMA 필터를 사후 적용한다.

    진입 신호(이전 봉 0 → 현재 봉 1)만 필터링하고,
    이미 보유 중인(연속 1) 신호는 유지한다. 이는 v1 전략과의 공정한 비교를 위해
    '진입 시점' 필터만 적용하는 방식이다.

    Returns:
        필터 적용 후 신호 Series (0 또는 1)
    """
    sig = signal.copy().values.astype(np.int8)
    n = len(sig)

    # ts → 날짜 문자열 매핑
    dates = [
        datetime.fromtimestamp(int(df["ts"].iloc[i]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for i in range(n)
    ]

    in_position = False
    for i in range(n):
        if sig[i] == 1 and not in_position:
            # 신규 진입 시도 — 필터 적용
            d = dates[i]
            blocked = False

            if use_fg and fg_history:
                fg_val = fg_history.get(d)
                if fg_val is not None and fg_val < 20.0:
                    blocked = True

            if not blocked and use_btc_sma and btc_above_series is not None:
                if not bool(btc_above_series.iloc[i]):
                    blocked = True

            if blocked:
                sig[i] = 0
            else:
                in_position = True

        elif sig[i] == 0 and in_position:
            in_position = False
        elif sig[i] == 1 and in_position:
            pass  # 보유 유지 — 필터 적용 안 함

    return pd.Series(sig, index=signal.index, dtype=int)


# ── 전략 래퍼: 신호 생성 후 필터 적용 ─────────────────────────────────────────
def _make_filtered_strategy(
    fg_history: dict[str, float],
    use_fg: bool,
    use_btc_sma: bool,
    btc_above_series: "pd.Series | None",
):
    """필터 적용된 strategy_fn을 반환한다."""
    base_fn = get_strategy("composite", **BASE_KWARGS)

    def strategy(df: pd.DataFrame) -> pd.Series:
        raw_signal = base_fn(df)
        return _apply_filters_to_signals(
            raw_signal, df, fg_history, use_fg, use_btc_sma, btc_above_series
        )

    return strategy


# ── 단일 백테스트 실행 ─────────────────────────────────────────────────────────
def _run_single(
    name: str,
    ohlcv_oos: pd.DataFrame,
    strategy_fn,
) -> dict:
    """BacktestEngine으로 OOS 백테스트 실행 후 요약 딕셔너리 반환."""
    engine = BacktestEngine()
    result = engine.run(strategy_fn, ohlcv_oos)
    m = result.metrics
    trades = result.trade_log

    win_rate = float((trades["return_pct"] > 0).mean()) if len(trades) > 0 else 0.0

    return {
        "name": name,
        "sharpe": m.sharpe,
        "max_drawdown": round(m.max_drawdown * 100, 2),
        "total_return": round(m.total_return * 100, 2),
        "n_trades": len(trades),
        "win_rate": round(win_rate * 100, 2),
        "calmar": m.calmar,
    }


# ── 메인 ──────────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("composite v2 백테스트 비교")
    print(f"  심볼: {SYMBOL}")
    print(f"  OOS: {OOS_START[:10]} ~ {OOS_END[:10]}")
    print("=" * 60)

    # 1. BTC/KRW OHLCV 조회 (OOS 전체 + SMA 워밍업 여유분)
    from datetime import timedelta
    oos_start_dt = datetime.fromisoformat(OOS_START.replace("Z", "+00:00"))
    fetch_start = (oos_start_dt - timedelta(days=BTC_SMA_PERIOD + 30)).strftime("%Y-%m-%dT00:00:00Z")

    print(f"\n[1/4] BTC/KRW OHLCV 조회 ({fetch_start[:10]} ~ {OOS_END[:10]})...")
    raw = await fetch_ohlcv(SYMBOL, "1d", fetch_start, OOS_END, use_cache=True)
    df_full = pd.DataFrame(raw)
    print(f"  로드: {len(df_full)}봉")

    if len(df_full) < BTC_SMA_PERIOD + 10:
        print("[오류] 데이터 부족 — 종료")
        return

    # OOS 기간만 추출 (백테스트 입력)
    oos_start_ms = int(oos_start_dt.timestamp() * 1000)
    df_oos = df_full[df_full["ts"] >= oos_start_ms].reset_index(drop=True)
    print(f"  OOS 구간: {len(df_oos)}봉")

    # 전체 데이터 기반 BTC SMA(20) 위/아래 계산 → OOS 구간으로 슬라이싱
    btc_above_full = _calc_btc_sma_filter(df_full, BTC_SMA_PERIOD)
    btc_above_oos = btc_above_full[df_full["ts"] >= oos_start_ms].reset_index(drop=True)
    btc_above_oos.index = df_oos.index
    above_count = int(btc_above_oos.sum())
    print(f"  BTC>SMA20 일수: {above_count}/{len(df_oos)} ({above_count/len(df_oos)*100:.1f}%)")

    # 2. F&G 히스토리 조회
    print("\n[2/4] F&G 히스토리 조회...")
    fg_history = await _fetch_fg_history()
    if fg_history:
        fg_series = [
            fg_history.get(
                datetime.fromtimestamp(int(df_oos["ts"].iloc[i]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            )
            for i in range(len(df_oos))
        ]
        extreme_fear_days = sum(1 for v in fg_series if v is not None and v < 20)
        print(f"  극공포(F&G<20) 일수: {extreme_fear_days}/{len(df_oos)}")
    else:
        extreme_fear_days = 0

    # 3. 4가지 전략 백테스트
    print("\n[3/4] 백테스트 실행...")

    variants = [
        {
            "name": "v1 (기준)",
            "use_fg": False,
            "use_btc_sma": False,
            "desc": "필터 없음 (기존 composite)",
        },
        {
            "name": "v2a (F&G 게이트)",
            "use_fg": True,
            "use_btc_sma": False,
            "desc": "F&G<20 진입 차단",
        },
        {
            "name": "v2b (BTC SMA)",
            "use_fg": False,
            "use_btc_sma": True,
            "desc": "BTC<SMA20 진입 차단",
        },
        {
            "name": "v2c (F&G + BTC SMA)",
            "use_fg": True,
            "use_btc_sma": True,
            "desc": "F&G<20 AND BTC<SMA20 진입 차단",
        },
    ]

    results = []
    for v in variants:
        print(f"  [{v['name']}] {v['desc']}...")
        fn = _make_filtered_strategy(
            fg_history=fg_history,
            use_fg=v["use_fg"],
            use_btc_sma=v["use_btc_sma"],
            btc_above_series=btc_above_oos if v["use_btc_sma"] else None,
        )
        r = _run_single(v["name"], df_oos, fn)
        r["desc"] = v["desc"]
        results.append(r)
        print(
            f"    Sharpe={r['sharpe']:.3f} | MDD={r['max_drawdown']:.1f}% "
            f"| 거래={r['n_trades']}건 | 승률={r['win_rate']:.1f}% "
            f"| 총수익={r['total_return']:.1f}%"
        )

    # 4. 결과 저장
    print("\n[4/4] 결과 저장...")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "symbol": SYMBOL,
            "oos_start": OOS_START[:10],
            "oos_end": OOS_END[:10],
            "oos_bars": len(df_oos),
            "btc_above_sma_days": above_count,
            "extreme_fear_days": extreme_fear_days,
            "base_kwargs": BASE_KWARGS,
            "btc_sma_period": BTC_SMA_PERIOD,
            "run_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
        "results": results,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  저장 완료: {OUTPUT_PATH}")

    # 5. 비교 표 출력
    print("\n" + "=" * 76)
    print(f"{'전략':<22} {'Sharpe':>7} {'MDD':>8} {'거래수':>6} {'승률':>7} {'총수익':>8}")
    print("-" * 76)
    for r in results:
        print(
            f"{r['name']:<22} "
            f"{r['sharpe']:>7.3f} "
            f"{r['max_drawdown']:>7.1f}% "
            f"{r['n_trades']:>6} "
            f"{r['win_rate']:>6.1f}% "
            f"{r['total_return']:>7.1f}%"
        )
    print("=" * 76)

    # 최선 전략 추천
    best = max(results, key=lambda x: x["sharpe"])
    print(f"\n최고 Sharpe: {best['name']} ({best['sharpe']:.3f})")


if __name__ == "__main__":
    asyncio.run(main())
