"""단타 전략 3종 병렬 백테스트 — 레짐 적응형.

전략:
  1. 변동성 돌파 (Larry Williams 변형) — 일봉
  2. 알트 펌프 서핑 — 4시간봉
  3. RSI 다이버전스 반전 — 4시간봉

기간:
  IS:  2023-01-01 ~ 2025-03-31
  OOS: 2025-04-01 ~ 2026-03-31
  하락장: 2025-09-01 ~ 2026-03-31

통과 기준:
  OOS Sharpe >= 0.6, MDD >= -20%, 승률 >= 40%
  하락장 승률 >= 35%, 월 거래 >= 5회, 평균수익 > 0.3%
"""
import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from datetime import datetime, timezone

from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv
from services.strategies.advanced import (
    make_strategy_volatility_breakout,
    make_strategy_alt_pump_surf,
    make_strategy_rsi_divergence,
)

# ── 코인 목록 ──
COINS = [
    'BTC/KRW', 'ETH/KRW', 'XRP/KRW', 'SOL/KRW', 'DOGE/KRW',
    'ADA/KRW', 'AVAX/KRW', 'LINK/KRW', 'DOT/KRW',
    'ATOM/KRW', 'NEAR/KRW', 'APT/KRW', 'ARB/KRW', 'OP/KRW',
    'SUI/KRW', 'SEI/KRW', 'AAVE/KRW', 'EOS/KRW',
]

# ── 기간 ──
WARMUP_START = '2022-06-01T00:00:00Z'
IS_START     = '2023-01-01T00:00:00Z'
IS_END       = '2025-03-31T00:00:00Z'
OOS_START    = '2025-04-01T00:00:00Z'
OOS_END      = '2026-03-31T00:00:00Z'
BEAR_START   = '2025-09-01T00:00:00Z'

def iso_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)

# ── 전략 정의 ──
STRATEGIES_1D = {
    'VB_bull':    make_strategy_volatility_breakout(k_bull=0.4, k_neutral=0.5, k_bear=0.7),
    'VB_bear':    make_strategy_volatility_breakout(k_bull=0.5, k_neutral=0.6, k_bear=0.8),
    'VB_auto':    make_strategy_volatility_breakout(),  # 기본값 (레짐 자동)
}

STRATEGIES_4H = {
    'Pump_bull':  make_strategy_alt_pump_surf(vol_mult_bull=4.0, vol_mult_bear=6.0),
    'Pump_bear':  make_strategy_alt_pump_surf(vol_mult_bull=5.0, vol_mult_bear=7.0, pct_threshold=0.03),
    'Pump_auto':  make_strategy_alt_pump_surf(),
    'Div_bull':   make_strategy_rsi_divergence(lookback=20, atr_mult=2.0),
    'Div_bear':   make_strategy_rsi_divergence(lookback=16, atr_mult=1.5, tp_pct=0.03, sl_pct=0.015),
    'Div_auto':   make_strategy_rsi_divergence(),
}


def run_backtest(engine, strategy_fn, df, label=""):
    """단일 백테스트 실행 + 결과 딕셔너리 반환."""
    try:
        result = engine.run(strategy_fn, df)
        m = result.metrics
        return {
            'sharpe': m.sharpe,
            'mdd': m.max_drawdown,
            'total_ret': m.total_return,
            'n_trades': m.n_trades,
            'win_rate': m.win_rate,
            'avg_ret': m.avg_trade_return,
        }
    except Exception as e:
        print(f"  [오류] {label}: {e}")
        return None


async def main():
    engine = BacktestEngine()
    all_results = []

    is_end_ms = iso_ms(IS_END)
    oos_start_ms = iso_ms(OOS_START)
    bear_start_ms = iso_ms(BEAR_START)

    # ═══════════════════════════════════════════
    # 1. 일봉 전략 (변동성 돌파)
    # ═══════════════════════════════════════════
    print("=" * 60)
    print("1. 변동성 돌파 (일봉) — 18코인")
    print("=" * 60)

    for strat_name, strat_fn in STRATEGIES_1D.items():
        agg = {'is': [], 'oos': [], 'bear': []}

        for coin in COINS:
            try:
                raw = await fetch_ohlcv(coin, '1d', WARMUP_START, OOS_END)
                df = pd.DataFrame(raw)
                if len(df) < 100:
                    continue
            except Exception as e:
                print(f"  {coin} 데이터 오류: {e}")
                continue

            df_is = df[df['ts'] <= is_end_ms].copy().reset_index(drop=True)
            df_oos = df[df['ts'] >= oos_start_ms].copy().reset_index(drop=True)
            df_bear = df[df['ts'] >= bear_start_ms].copy().reset_index(drop=True)

            for period_name, period_df in [('is', df_is), ('oos', df_oos), ('bear', df_bear)]:
                if len(period_df) < 30:
                    continue
                r = run_backtest(engine, strat_fn, period_df, f"{strat_name}/{coin}/{period_name}")
                if r:
                    r['coin'] = coin
                    agg[period_name].append(r)

        # 집계
        for period_name, results in agg.items():
            if not results:
                continue
            df_r = pd.DataFrame(results)
            total_trades = df_r['n_trades'].sum()
            avg_wr = df_r['win_rate'].mean() if total_trades > 0 else 0
            avg_sharpe = df_r['sharpe'].mean()
            avg_mdd = df_r['mdd'].mean()
            avg_ret = df_r['avg_ret'].mean()
            total_ret = df_r['total_ret'].mean()

            row = {
                'strategy': strat_name,
                'period': period_name,
                'coins': len(results),
                'total_trades': total_trades,
                'avg_sharpe': round(avg_sharpe, 3),
                'avg_mdd': round(avg_mdd, 3),
                'avg_total_ret': round(total_ret, 3),
                'avg_win_rate': round(avg_wr, 3),
                'avg_trade_ret': round(avg_ret, 4),
            }
            all_results.append(row)
            print(f"  {strat_name}/{period_name}: trades={total_trades}, "
                  f"sharpe={avg_sharpe:.3f}, mdd={avg_mdd:.1%}, "
                  f"wr={avg_wr:.0%}, avg={avg_ret:.2%}")

    # ═══════════════════════════════════════════
    # 2. 4시간봉 전략 (펌프 서핑 + RSI 다이버전스)
    # ═══════════════════════════════════════════
    print("\n" + "=" * 60)
    print("2. 알트 펌프 서핑 & RSI 다이버전스 (4시간봉) — 18코인")
    print("=" * 60)

    for strat_name, strat_fn in STRATEGIES_4H.items():
        agg = {'is': [], 'oos': [], 'bear': []}

        for coin in COINS:
            try:
                raw = await fetch_ohlcv(coin, '4h', WARMUP_START, OOS_END)
                df = pd.DataFrame(raw)
                if len(df) < 200:
                    continue
            except Exception as e:
                print(f"  {coin} 데이터 오류: {e}")
                continue

            df_is = df[df['ts'] <= is_end_ms].copy().reset_index(drop=True)
            df_oos = df[df['ts'] >= oos_start_ms].copy().reset_index(drop=True)
            df_bear = df[df['ts'] >= bear_start_ms].copy().reset_index(drop=True)

            for period_name, period_df in [('is', df_is), ('oos', df_oos), ('bear', df_bear)]:
                if len(period_df) < 50:
                    continue
                r = run_backtest(engine, strat_fn, period_df, f"{strat_name}/{coin}/{period_name}")
                if r:
                    r['coin'] = coin
                    agg[period_name].append(r)

        for period_name, results in agg.items():
            if not results:
                continue
            df_r = pd.DataFrame(results)
            total_trades = df_r['n_trades'].sum()
            avg_wr = df_r['win_rate'].mean() if total_trades > 0 else 0
            avg_sharpe = df_r['sharpe'].mean()
            avg_mdd = df_r['mdd'].mean()
            avg_ret = df_r['avg_ret'].mean()
            total_ret = df_r['total_ret'].mean()

            row = {
                'strategy': strat_name,
                'period': period_name,
                'coins': len(results),
                'total_trades': total_trades,
                'avg_sharpe': round(avg_sharpe, 3),
                'avg_mdd': round(avg_mdd, 3),
                'avg_total_ret': round(total_ret, 3),
                'avg_win_rate': round(avg_wr, 3),
                'avg_trade_ret': round(avg_ret, 4),
            }
            all_results.append(row)
            print(f"  {strat_name}/{period_name}: trades={total_trades}, "
                  f"sharpe={avg_sharpe:.3f}, mdd={avg_mdd:.1%}, "
                  f"wr={avg_wr:.0%}, avg={avg_ret:.2%}")

    # ═══════════════════════════════════════════
    # 3. 결과 종합
    # ═══════════════════════════════════════════
    print("\n" + "=" * 60)
    print("결과 종합")
    print("=" * 60)

    df_all = pd.DataFrame(all_results)
    if df_all.empty:
        print("결과 없음")
        return

    # OOS 결과만 필터
    df_oos = df_all[df_all['period'] == 'oos'].copy()
    df_bear = df_all[df_all['period'] == 'bear'].copy()

    print("\n【OOS 결과 (2025-04 ~ 2026-03)】")
    print(f"{'전략':<15} {'거래':>6} {'Sharpe':>8} {'MDD':>8} {'승률':>6} {'평균수익':>8} {'총수익':>8}")
    print("-" * 65)
    for _, row in df_oos.sort_values('avg_sharpe', ascending=False).iterrows():
        print(f"{row['strategy']:<15} {row['total_trades']:>6} "
              f"{row['avg_sharpe']:>8.3f} {row['avg_mdd']:>7.1%} "
              f"{row['avg_win_rate']:>5.0%} {row['avg_trade_ret']:>7.2%} "
              f"{row['avg_total_ret']:>7.1%}")

    print("\n【하락장 결과 (2025-09 ~ 2026-03)】")
    print(f"{'전략':<15} {'거래':>6} {'Sharpe':>8} {'MDD':>8} {'승률':>6} {'평균수익':>8} {'총수익':>8}")
    print("-" * 65)
    for _, row in df_bear.sort_values('avg_sharpe', ascending=False).iterrows():
        print(f"{row['strategy']:<15} {row['total_trades']:>6} "
              f"{row['avg_sharpe']:>8.3f} {row['avg_mdd']:>7.1%} "
              f"{row['avg_win_rate']:>5.0%} {row['avg_trade_ret']:>7.2%} "
              f"{row['avg_total_ret']:>7.1%}")

    # 통과 기준 판정
    print("\n【통과 판정】")
    for _, row in df_oos.iterrows():
        name = row['strategy']
        bear_row = df_bear[df_bear['strategy'] == name]
        bear_wr = bear_row['avg_win_rate'].values[0] if len(bear_row) > 0 else 0

        checks = [
            ('Sharpe>=0.6', row['avg_sharpe'] >= 0.6),
            ('MDD>=-20%', row['avg_mdd'] >= -0.20),
            ('승률>=40%', row['avg_win_rate'] >= 0.40),
            ('하락장승률>=35%', bear_wr >= 0.35),
            ('평균수익>0.3%', row['avg_trade_ret'] > 0.003),
        ]
        passed = all(v for _, v in checks)
        status = "PASS ✓" if passed else "FAIL"
        detail = ", ".join(f"{n}={'✓' if v else '✗'}" for n, v in checks)
        print(f"  {name}: {status} [{detail}]")

    # CSV 저장
    out_path = ROOT / 'output' / 'shortterm_backtest_results.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_all.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n결과 저장: {out_path}")


if __name__ == '__main__':
    asyncio.run(main())
