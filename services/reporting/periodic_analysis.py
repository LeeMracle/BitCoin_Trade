"""정기 분석 + 누적 성적 빌더.

plan 20260503 P1 (AC16 — cto 2차 #5 부분 해소):
realtime_monitor.py의 _send_periodic_report와 daily_report.py에 분산되어 있던
누적 성적/체크포인트 산식을 한 곳으로 추출. lessons #19(config 자체정의) 패턴 방지.

Pure function (state만 입력) — 단위 테스트 용이.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone


_DEFAULT_STRATEGY_START = "2026-03-29"
_BACKTEST_TARGET_WINRATE = 35     # %
_BACKTEST_TARGET_AVG_RET = 0.5    # %


def check_consec_loss(state: dict,
                      strategy_default_start: str = _DEFAULT_STRATEGY_START) -> tuple[int, int, int]:
    """현재 전략 기간 기준 연속 손실 확인.

    Returns:
        (consec_loss, total_trades, wins) — 모두 현재 전략 기간 한정
    """
    closed = state.get("closed_trades", [])
    strategy_start = state.get("strategy_start", strategy_default_start)
    current = [t for t in closed if t.get("exit_date", "") >= strategy_start]
    n = len(current)
    wins = sum(1 for t in current if t.get("return_pct", 0) > 0)
    consec = 0
    for t in reversed(current):
        if t.get("return_pct", 0) <= 0:
            consec += 1
        else:
            break
    return consec, n, wins


def build_strategy_summary(state: dict,
                           strategy_default_start: str = _DEFAULT_STRATEGY_START) -> str:
    """현재 전략 기간 기준 누적 성과 + 백테스트 대비 + 체크포인트.

    Returns:
        멀티라인 텍스트 (3~4 줄)

    예시:
        검증 35일차 — 거래 4건 (백테스트 목표: 승률 35%+, 평균 +0.5%+)
        실제: 승률 25% | 평균 -7.0% | 연속손실 2건
        📅 7일+ 거래 4건 (15건 미달, 계속 관찰)
    """
    closed = state.get("closed_trades", [])
    strategy_start = state.get("strategy_start", strategy_default_start)
    try:
        start_dt = datetime.strptime(strategy_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_elapsed = (datetime.now(tz=timezone.utc) - start_dt).days
    except Exception:
        days_elapsed = -1

    current = [t for t in closed if t.get("exit_date", "") >= strategy_start]
    n = len(current)
    wins = sum(1 for t in current if t.get("return_pct", 0) > 0)
    win_rate = wins / n * 100 if n > 0 else 0
    avg_ret = sum(t.get("return_pct", 0) for t in current) / n if n > 0 else 0
    consec, _, _ = check_consec_loss(state, strategy_default_start)

    lines = [
        f"검증 {days_elapsed}일차 — 거래 {n}건 "
        f"(백테스트 목표: 승률 {_BACKTEST_TARGET_WINRATE}%+, 평균 +{_BACKTEST_TARGET_AVG_RET}%+)",
        f"실제: 승률 {win_rate:.0f}% | 평균 {avg_ret:+.1f}% | 연속손실 {consec}건",
    ]

    # 체크포인트 판정 (정기 분석과 동일 기준)
    if days_elapsed >= 7 and n >= 15:
        verdict = "PASS" if win_rate >= _BACKTEST_TARGET_WINRATE else "FAIL"
        lines.append(f"🏁 7일 최종판정: {verdict}")
    elif days_elapsed >= 7:
        lines.append(f"📅 7일+ 거래 {n}건 (15건 미달, 계속 관찰)")
    elif days_elapsed >= 5 and n >= 10:
        verdict = "OK" if win_rate >= 30 else "경고"
        lines.append(f"📅 5일 중간점검: {verdict}")
    elif days_elapsed >= 3 and n >= 5 and win_rate == 0:
        lines.append(f"🚨 3일 긴급: 전패 ({n}건 0승) → 중단 검토")

    return "\n".join(lines)


def build_market_snapshot() -> dict:
    """BTC 시세 + F&G — 외부 API 의존, 실패 시 부분 빈 dict.

    Returns:
        {"btc_price": float, "btc_chg": float, "fg_value": int, "fg_label": str}
        실패한 키는 None
    """
    snap: dict = {
        "btc_price": None, "btc_chg": None,
        "fg_value": None, "fg_label": None,
    }
    try:
        import ccxt
        ex = ccxt.upbit({"enableRateLimit": True})
        t = ex.fetch_ticker("BTC/KRW")
        snap["btc_price"] = float(t["last"])
        snap["btc_chg"] = float(t.get("percentage") or 0)
    except Exception:
        pass
    try:
        with urllib.request.urlopen(
            "https://api.alternative.me/fng/?limit=1", timeout=5
        ) as r:
            data = json.loads(r.read().decode())
        snap["fg_value"] = int(data["data"][0]["value"])
        snap["fg_label"] = data["data"][0]["value_classification"]
    except Exception:
        pass
    return snap
