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


# ── 검증 기준선 (ADR 20260823-1, P1 재설정) ────────────────────────────
# 2026-08-22 이전 통계는 신뢰할 수 없어 폐기했다. 두 가지 이유:
#   (1) 실행 버그 4건 (lessons #40~#43) — 매수 100% 차단, 손절 미평가,
#       트레일스탑 미저장, 체결가 대신 신호가 기록. 성과가 전략이 아닌 버그의 산물.
#   (2) 그 기간 전략 파라미터가 반복 변경 — DC 50→20→15→10→15→12,
#       MIN_VOLUME 5억→3억→5억, 레짐필터 OFF→ON, VOL 1.0→1.5, ATR 0.10→0.07.
#       서로 다른 전략의 성적을 하나로 합산하고 있었다.
_DEFAULT_STRATEGY_START = "2026-08-23"

# 목표치는 현재 config 백테스트 실측에서 도출 (scripts/calibrate_strategy_baseline.py)
#   측정: 506건 / 54종목 / 700일, 수수료 0.05%x2 반영
#         승률 59.9% / 평균 +1.50% / PF 1.51 / 평균보유 5.6일
#   목표는 기대의 하한으로 잡는다 — 기대치 그대로 목표면 구조적으로 절반이 FAIL.
#   승률은 기대의 80%, 평균수익은 50% 수준.
# (이전 값 35% / +0.5% 는 TP(+5%/+12%) 도입 이전 설정의 잔재로 추정 — 현재와 불일치)
_BACKTEST_TARGET_WINRATE = 48     # %  (기대 59.9%의 80%)
_BACKTEST_TARGET_AVG_RET = 0.75   # %  (기대 +1.50%의 50%)

# 판정에 필요한 최소 표본. 승률 60% vs 35%를 구분하려면 15건으로는 부족하다.
_VERDICT_MIN_TRADES = 30
_INTERIM_MIN_TRADES = 15


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
    # ── 연패 카운트 floor (lessons #38, 2026-06-07) ──
    # 누적 통계(n/wins)는 strategy_start 기준 유지하되, 연패(consec) 산정만
    # consec_loss_floor_date 이후(>) 거래로 한정. 옛 원인(저유동성 알트 등)
    # 제거 후 cooldown을 근본 해제할 때 사용 — cooldown_until만 리셋하면
    # 매 cycle closed_trades 재계산으로 재설정되는 함정 차단.
    floor = state.get("consec_loss_floor_date")
    consec_pool = current
    if floor:
        consec_pool = [t for t in current if t.get("exit_date", "") > floor]
    consec = 0
    for t in reversed(consec_pool):
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
        # 기준일이 아직 오지 않았으면(리셋 직후 몇 시간) 음수가 되어
        # "검증 -1일차"로 표시된다 — 0으로 클램프.
        days_elapsed = max(0, (datetime.now(tz=timezone.utc) - start_dt).days)
    except Exception:
        days_elapsed = -1

    current = [t for t in closed if t.get("exit_date", "") >= strategy_start]
    n = len(current)
    wins = sum(1 for t in current if t.get("return_pct", 0) > 0)
    win_rate = wins / n * 100 if n > 0 else 0
    avg_ret = sum(t.get("return_pct", 0) for t in current) / n if n > 0 else 0
    consec, _, _ = check_consec_loss(state, strategy_default_start)

    # 거래 "가능"했던 날 — 레짐 게이트(BTC>EMA200)가 열려 있던 일수.
    # 달력 일수로 검증 진척을 재면 착시가 생긴다: 2026년은 Q1/Q2 레짐 통과율 0%,
    # 최근 180일 1.1%로 전략이 애초에 거래할 수 없었다.
    # "검증 146일차 16건"은 그중 거래 가능일이 거의 없었다는 사실을 감춘다.
    open_days = state.get("regime_open_days", 0)

    lines = [
        f"검증 {days_elapsed}일차 (거래가능 {open_days}일) — 거래 {n}건 "
        f"(목표: 승률 {_BACKTEST_TARGET_WINRATE}%+, 평균 +{_BACKTEST_TARGET_AVG_RET}%+)",
        f"실제: 승률 {win_rate:.0f}% | 평균 {avg_ret:+.1f}% | 연속손실 {consec}건",
    ]

    # ── 판정은 달력이 아니라 표본 수 기준 ──
    # 레짐 게이트가 닫혀 있으면 시간만 흐르고 거래는 안 쌓인다. 시간 기반 판정은
    # "거래 0건인데 7일 지났으니 FAIL" 같은 무의미한 결론을 낳는다.
    if n >= _VERDICT_MIN_TRADES:
        verdict = "PASS" if (win_rate >= _BACKTEST_TARGET_WINRATE
                             and avg_ret >= _BACKTEST_TARGET_AVG_RET) else "FAIL"
        lines.append(f"🏁 최종판정 ({n}건 표본): {verdict}")
    elif n >= _INTERIM_MIN_TRADES:
        tone = "양호" if win_rate >= _BACKTEST_TARGET_WINRATE else "미달"
        lines.append(
            f"📊 중간점검 {n}/{_VERDICT_MIN_TRADES}건 — {tone} "
            f"(최종판정까지 {_VERDICT_MIN_TRADES - n}건)"
        )
    elif n >= 5 and win_rate == 0:
        lines.append(f"🚨 긴급: 전패 ({n}건 0승) → 중단 검토")
    else:
        lines.append(f"📊 표본 축적 중 {n}/{_VERDICT_MIN_TRADES}건 (거래가능 {open_days}일)")

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
