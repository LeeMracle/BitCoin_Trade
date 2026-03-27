"""페이퍼 트레이딩 일일 러너.

매일 1회 실행하여:
  1. 최신 OHLCV 데이터 수집 (공개 API, 인증 불필요)
  2. 전략 신호 확인 (Donchian 50 + ATR 3.0)
  3. 가상 매매 실행 및 상태 저장
  4. 텔레그램 알림 발송

실행 방법:
  python -m services.paper_trading.runner          (일일 체크)
  python -m services.paper_trading.runner --status  (현재 상태 조회)
  python -m services.paper_trading.runner --reset   (상태 초기화)
"""
from __future__ import annotations

import asyncio
import sys
import io
from datetime import datetime, timedelta, timezone

import pandas as pd

from services.market_data.fetcher import fetch_ohlcv
from services.paper_trading.strategy import (
    check_entry, check_exit, get_strategy_info,
    DONCHIAN_PERIOD, ATR_PERIOD,
)
from services.paper_trading.state import load_state, save_state, PaperState
from services.alerting.notifier import send, notify_trade, notify_daily_summary, notify_error


# 데이터 수집에 필요한 최소 일수 (워밍업)
MIN_BARS = max(DONCHIAN_PERIOD, ATR_PERIOD) + 10


async def fetch_recent_ohlcv(days: int = MIN_BARS + 5) -> pd.DataFrame:
    """최근 N일 OHLCV 수집."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    raw = await fetch_ohlcv(
        "BTC/KRW", "1d",
        start.strftime("%Y-%m-%dT00:00:00Z"),
        end.strftime("%Y-%m-%dT00:00:00Z"),
        use_cache=False,  # 항상 최신 데이터
    )
    return pd.DataFrame(raw)


async def run_daily():
    """일일 페이퍼 트레이딩 체크."""
    state = load_state()
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    strategy_info = get_strategy_info()

    print(f"[{today}] 페이퍼 트레이딩 일일 체크")
    print(f"  전략: {strategy_info['name']}")

    try:
        df = await fetch_recent_ohlcv()
    except Exception as e:
        msg = f"데이터 수집 실패: {e}"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    if len(df) < MIN_BARS:
        msg = f"데이터 부족: {len(df)}일 < 필요 {MIN_BARS}일"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    latest_close = float(df["close"].iloc[-1])
    latest_date = pd.Timestamp(int(df["ts"].iloc[-1]), unit="ms", tz="UTC").strftime("%Y-%m-%d")
    state.last_close = latest_close

    print(f"  최신 데이터: {latest_date}, BTC/KRW {latest_close:,.0f}")
    print(f"  현재 상태: {'보유중' if state.is_holding else '대기중'}")

    action = None

    if not state.is_holding:
        # 진입 확인
        if check_entry(df):
            state.open_position(latest_close, latest_date)
            action = "BUY"
            print(f"  *** 매수 신호! 가격: {latest_close:,.0f} KRW ***")
            await notify_trade("BUY", latest_close, state.equity, f"paper_{latest_date}")
        else:
            # Donchian 상단까지 거리 표시
            from services.paper_trading.strategy import calc_donchian_upper
            upper = calc_donchian_upper(df)
            if not pd.isna(upper.iloc[-1]):
                dist = (upper.iloc[-1] - latest_close) / latest_close * 100
                print(f"  Donchian 상단: {upper.iloc[-1]:,.0f} (현재가 대비 {dist:+.1f}%)")
            print("  신호 없음 — 대기 유지")

    else:
        # 청산 확인
        should_exit, new_stop = check_exit(df, state.highest_since_entry)

        # 고점 갱신
        if latest_close > state.highest_since_entry:
            state.highest_since_entry = latest_close
        state.trailing_stop = new_stop

        unrealized = (latest_close / state.entry_price - 1) * 100
        print(f"  진입가: {state.entry_price:,.0f}  미실현: {unrealized:+.1f}%")
        print(f"  고점: {state.highest_since_entry:,.0f}  트레일링스탑: {new_stop:,.0f}")

        if should_exit:
            state.close_position(latest_close, latest_date)
            action = "SELL"
            last_trade = state.trades[-1]
            ret = last_trade["return_pct"] * 100
            print(f"  *** 매도 신호! 가격: {latest_close:,.0f} KRW, 수익: {ret:+.1f}% ***")
            await notify_trade("SELL", latest_close, state.equity, f"paper_{latest_date}")
        else:
            print("  보유 유지")

    # 상태 저장
    save_state(state)

    # 일일 요약
    equity = state.equity
    daily_return = state.total_return * 100
    print(f"\n  평가금액: {equity:,.0f} KRW  총수익률: {daily_return:+.1f}%")
    print(f"  거래: {state.n_trades}회  승률: {state.win_rate*100:.0f}%")

    await notify_daily_summary(today, equity, daily_return)

    return action


def show_status():
    """현재 페이퍼 트레이딩 상태 출력."""
    state = load_state()
    strategy_info = get_strategy_info()

    print("=" * 60)
    print(f"페이퍼 트레이딩 상태")
    print("=" * 60)
    print(f"  전략: {strategy_info['name']}")
    print(f"  마지막 업데이트: {state.last_updated}")
    print(f"  포지션: {'보유중' if state.is_holding else '대기중'}")

    if state.is_holding:
        print(f"  진입가: {state.entry_price:,.0f} KRW")
        print(f"  고점: {state.highest_since_entry:,.0f} KRW")
        print(f"  트레일링스탑: {state.trailing_stop:,.0f} KRW")
        if state.last_close > 0:
            unrealized = (state.last_close / state.entry_price - 1) * 100
            print(f"  최신 종가: {state.last_close:,.0f} KRW  미실현: {unrealized:+.1f}%")

    print(f"\n  초기 자본: {state.initial_capital:,.0f} KRW")
    print(f"  현재 평가: {state.equity:,.0f} KRW")
    print(f"  총 수익률: {state.total_return*100:+.1f}%")
    print(f"  완료 거래: {state.n_trades}회  승률: {state.win_rate*100:.0f}%")

    if state.trades:
        print(f"\n  거래 내역:")
        for i, t in enumerate(state.trades, 1):
            status = t["status"]
            entry = f"{t['entry_date']} @ {t['entry_price']:,.0f}"
            if status == "closed":
                ret = t["return_pct"] * 100
                exit_info = f" → {t['exit_date']} @ {t['exit_price']:,.0f} ({ret:+.1f}%)"
            else:
                exit_info = " (보유중)"
            print(f"    #{i} {entry}{exit_info}")

    print("=" * 60)


def reset_state():
    """상태 초기화."""
    state = PaperState()
    save_state(state)
    print("페이퍼 트레이딩 상태가 초기화되었습니다.")


if __name__ == "__main__":
    import sys as _sys
    args = _sys.argv[1:]

    if "--status" in args:
        show_status()
    elif "--reset" in args:
        reset_state()
    else:
        asyncio.run(run_daily())
