"""
200일 MA 트렌드 필터 적용 백테스트
- F&G 역추세 전략 (buy<=25/sell>=55) 에 트렌드 필터 추가
- 조건: close > MA200 일 때만 F&G 매수 신호 허용
- IS: 2023-01-01 ~ 2025-03-01
- OOS: 2025-03-01 ~ 2026-03-01
"""
import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv, fetch_fear_greed

IS_START  = "2023-01-01T00:00:00Z"
IS_END    = "2025-03-01T00:00:00Z"
OOS_START = "2025-03-01T00:00:00Z"
OOS_END   = "2026-03-01T00:00:00Z"
# MA200 계산을 위해 200일 이전 데이터도 가져옴
FETCH_START = "2022-06-01T00:00:00Z"


def make_fg_map(fg_df):
    return dict(zip(fg_df["date"], fg_df["value"]))


def make_strategy_fg_ma(fg_map, buy_thr=25, sell_thr=55, ma_period=200, use_filter=True):
    """
    F&G 역추세 + MA 트렌드 필터
    use_filter=False 이면 필터 없는 기존 전략
    """
    def strategy(df):
        close = df["close"]
        ma = close.rolling(ma_period, min_periods=ma_period).mean()

        signal = pd.Series(np.nan, index=df.index)
        pos = 0
        for i in range(len(df)):
            ts_ms = int(df["ts"].iloc[i])
            date_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            fg = fg_map.get(date_str, np.nan)
            ma_val = ma.iloc[i]

            if np.isnan(fg):
                signal.iloc[i] = pos
                continue

            # 트렌드 필터: MA200 아래면 신규 진입 금지, 기존 포지션은 청산
            trend_ok = (not use_filter) or (not np.isnan(ma_val) and close.iloc[i] > ma_val)

            if pos == 0 and fg <= buy_thr and trend_ok:
                pos = 1
            elif pos == 1 and (fg >= sell_thr or (use_filter and not trend_ok)):
                # 청산: F&G 상승 OR 트렌드 이탈
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


async def main():
    print("데이터 수집 중...")

    # MA200 계산 위해 충분한 이전 데이터 확보
    ohlcv_all_raw, fg_is_raw, fg_oos_raw = await asyncio.gather(
        fetch_ohlcv("BTC/KRW", "1d", FETCH_START, OOS_END),
        fetch_fear_greed(IS_START,  IS_END),
        fetch_fear_greed(OOS_START, OOS_END),
    )

    ohlcv_all = pd.DataFrame(ohlcv_all_raw)
    fg_map_is  = make_fg_map(pd.DataFrame(fg_is_raw))
    fg_map_oos = make_fg_map(pd.DataFrame(fg_oos_raw))

    # IS / OOS 슬라이스 (MA는 전체 기준으로 계산 후 구간 자름)
    is_start_ms  = int(pd.Timestamp(IS_START).timestamp()  * 1000)
    is_end_ms    = int(pd.Timestamp(IS_END).timestamp()    * 1000)
    oos_start_ms = int(pd.Timestamp(OOS_START).timestamp() * 1000)
    oos_end_ms   = int(pd.Timestamp(OOS_END).timestamp()   * 1000)

    ohlcv_is  = ohlcv_all[(ohlcv_all["ts"] >= is_start_ms)  & (ohlcv_all["ts"] <= is_end_ms)].reset_index(drop=True)
    ohlcv_oos = ohlcv_all[(ohlcv_all["ts"] >= oos_start_ms) & (ohlcv_all["ts"] <= oos_end_ms)].reset_index(drop=True)

    bh_is  = (ohlcv_is["close"].iloc[-1]  / ohlcv_is["close"].iloc[0])  - 1
    bh_oos = (ohlcv_oos["close"].iloc[-1] / ohlcv_oos["close"].iloc[0]) - 1

    print(f"IS: {len(ohlcv_is)}bars / OOS: {len(ohlcv_oos)}bars")

    engine = BacktestEngine()

    # ── 인샘플 비교: 필터 없음 vs MA200 필터 ──
    print("\n[IS] 기존 전략 (필터 없음)...")
    r_is_base = engine.run(make_strategy_fg_ma(fg_map_is, use_filter=False), ohlcv_is)

    print("[IS] MA200 필터 전략...")
    r_is_ma = engine.run(make_strategy_fg_ma(fg_map_is, use_filter=True), ohlcv_is)

    # ── 아웃오브샘플: 필터 없음 vs MA200 필터 ──
    # OOS는 전체 데이터로 MA 계산 후 OOS 구간만 실행 (MA 워밍업 해결)
    print("[OOS] 기존 전략 (필터 없음)...")
    r_oos_base = engine.run(make_strategy_fg_ma(fg_map_oos, use_filter=False), ohlcv_oos)

    print("[OOS] MA200 필터 전략 (전체 데이터 MA 계산)...")
    # OOS 구간 앞에 충분한 데이터를 붙여 MA 워밍업 후 OOS만 슬라이싱
    warmup_start_ms = oos_start_ms - (200 * 86400 * 1000)
    ohlcv_warmup = ohlcv_all[ohlcv_all["ts"] >= warmup_start_ms].reset_index(drop=True)

    # 전략 함수는 전체 warmup+OOS 데이터로 신호 생성, 엔진에도 동일하게 전달
    # 단, 성과 측정은 OOS 구간만 (엔진은 전달받은 df 전체 구간 실행)
    r_oos_ma = engine.run(make_strategy_fg_ma(fg_map_oos, use_filter=True), ohlcv_warmup)

    # Phase 3 판단 기준
    def phase3_check(m):
        return (m.sharpe >= 0.8 and m.max_drawdown >= -0.20
                and m.win_rate >= 0.5 and m.n_trades >= 2)

    # ── 결과 출력 ──
    def fmt(label, r, bh):
        m = r.metrics
        verdict = "[PASS]" if phase3_check(m) else "[FAIL]"
        print(f"\n  {label} {verdict}")
        print(f"    수익률: {m.total_return*100:+.1f}%  BnH: {bh*100:+.1f}%")
        print(f"    Sharpe: {m.sharpe:.3f}  Calmar: {m.calmar:.3f}  MDD: {m.max_drawdown*100:.1f}%")
        print(f"    거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%  평균: {m.avg_trade_return*100:.2f}%")

    print("\n" + "="*65)
    print("인샘플 비교 (2023-01-01 ~ 2025-03-01)")
    print("="*65)
    fmt("기존 F&G (필터 없음)", r_is_base, bh_is)
    fmt("F&G + MA200 필터    ", r_is_ma,   bh_is)

    print("\n" + "="*65)
    print("아웃오브샘플 비교 (2025-03-01 ~ 2026-03-01)")
    print("="*65)
    fmt("기존 F&G (필터 없음)", r_oos_base, bh_oos)
    fmt("F&G + MA200 필터    ", r_oos_ma,   bh_oos)

    oos_ma_m = r_oos_ma.metrics
    go = phase3_check(oos_ma_m)
    print(f"\n★ Phase 3 판단 (MA200 필터 OOS): {'GO [OK]' if go else 'HOLD [WAIT]'}")
    if not go:
        reasons = []
        if oos_ma_m.sharpe < 0.8:          reasons.append(f"Sharpe {oos_ma_m.sharpe:.2f}<0.8")
        if oos_ma_m.max_drawdown < -0.20:  reasons.append(f"MDD {oos_ma_m.max_drawdown*100:.1f}%<-20%")
        if oos_ma_m.win_rate < 0.5:        reasons.append(f"승률 {oos_ma_m.win_rate*100:.0f}%<50%")
        if oos_ma_m.n_trades < 2:          reasons.append(f"거래 {oos_ma_m.n_trades}회<2")
        print(f"  미충족: {', '.join(reasons)}")
    print("="*65)

    return {
        "is_base":   r_is_base.metrics.__dict__,
        "is_ma":     r_is_ma.metrics.__dict__,
        "oos_base":  r_oos_base.metrics.__dict__,
        "oos_ma":    r_oos_ma.metrics.__dict__,
        "bh_is":     round(bh_is, 4),
        "bh_oos":    round(bh_oos, 4),
        "phase3_go": go,
        "run_ids": {
            "is_base":  r_is_base.run_id,
            "is_ma":    r_is_ma.run_id,
            "oos_base": r_oos_base.run_id,
            "oos_ma":   r_oos_ma.run_id,
        }
    }


if __name__ == "__main__":
    import json
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_trend_filter_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
