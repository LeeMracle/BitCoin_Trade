"""포지션 손익 산식 — 단일 진실 원천 (lessons #45).

## 왜 별도 모듈인가

매도 경로가 셋이다: `realtime_monitor._check_tp_levels`(부분 익절),
`realtime_monitor._execute_sell`(트레일링스탑), `multi_trader.run_daily_cycle`(일일 회전).
셋이 각자 `return_pct`를 계산하면 반드시 갈라진다(교훈 #19).
`multi_trader`는 `realtime_monitor`가 import하므로 역방향 import가 불가능 —
그래서 양쪽 모두가 의존할 수 있는 중립 모듈에 둔다.

## 왜 money-weighted인가

`return_pct`를 **마지막 체결가만으로** 계산하면 부분 익절로 확보한 이익이 통계에서
사라진다. 예: +5%에 절반을 실현한 뒤 +1%에 트레일링스탑 이탈 → `+1%`로 기록.
실제 성과는 `(0.5 × 5%) + (0.5 × 1%) = +3%`다.

ADR 20260823-1의 판정 지표(승률 48% / 평균 +0.75% / 표본 30건)가 이 값 위에 서 있으므로,
실현손익 KRW 합계 ÷ 진입금 으로 통일한다.
"""
from __future__ import annotations

from services.execution.config import FEE_RATE


def record_realized(pos: dict, exec_price: float, qty: float) -> float:
    """매도 1건의 순손익(편도 수수료 2회 반영)을 포지션에 누적하고 그 값을 반환한다.

    Args:
        pos:        state["positions"][symbol] — 제자리에서 갱신된다
        exec_price: 확정 평균 체결가 (lessons #43 — 신호가 아님)
        qty:        이번에 매도한 수량
    """
    entry = float(pos.get("entry_price") or 0)
    net = exec_price * qty * (1 - FEE_RATE) - entry * qty * (1 + FEE_RATE)
    pos["realized_pl_krw"] = float(pos.get("realized_pl_krw") or 0.0) + net
    pos["realized_qty"] = float(pos.get("realized_qty") or 0.0) + qty
    return net


def position_return_pct(pos: dict, fallback_price: float = 0.0) -> float:
    """포지션 전체 수익률(%) — 부분 익절 실현분까지 합산한 money-weighted 값.

    실현손익이 추적되지 않은 구버전 포지션은 `fallback_price` 기준 단순 수익률로
    폴백한다(수수료는 반영). 폴백 값도 없으면 0.0.
    """
    entry = float(pos.get("entry_price") or 0)
    realized = pos.get("realized_pl_krw")
    basis = float(pos.get("entry_amount_krw") or 0)
    if basis <= 0 and entry > 0:
        basis = entry * float(pos.get("entry_qty") or 0)
    if realized is not None and basis > 0:
        return float(realized) / basis * 100.0
    if entry > 0 and fallback_price > 0:
        return (fallback_price * (1 - FEE_RATE)) / (entry * (1 + FEE_RATE)) * 100.0 - 100.0
    return 0.0
