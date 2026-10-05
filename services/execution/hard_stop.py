"""포지션별 하드 손절 바닥 — 단일 계산 지점 (ADR 20260930-1, 교차검증 지적 반영).

왜 별도 모듈인가:
    realtime_monitor 안에만 두면 수동 보정 스크립트(fix_entry_price_from_fills 등)가
    config 의 **현행** 캡을 직접 곱해 이미 열린 포지션의 손절선을 소급으로 끌어올린다
    (2026-10-05 독립 리뷰 적발). 바닥은 반드시 이 함수로만 계산한다.

규칙:
    - 신규 진입은 `HARD_STOP_LOSS_PCT` 를 포지션에 `hard_stop_pct` 로 기록한다
      (realtime_monitor._execute_buy — **유일하게** 현행 캡을 읽는 곳).
    - 그 이후 모든 바닥 계산은 포지션이 기록한 캡을 읽는다. config 를 바꿔도
      열려 있는 포지션은 움직이지 않는다.
    - 필드가 없거나 값이 비정상이면 LEGACY(0.10). 비정상 값이 0 이면 바닥이 진입가가 되어
      즉시 청산되므로, 어떤 입력도 **좁은 쪽**으로 해석하지 않는다.
"""
from __future__ import annotations

from services.execution.config import LEGACY_HARD_STOP_LOSS_PCT


def pos_hard_cap(pos: dict) -> float:
    """포지션의 하드 손절 캡(0~1). 필드 없음/비정상 → LEGACY."""
    raw = pos.get("hard_stop_pct")
    try:
        cap = float(raw)
    except (TypeError, ValueError):
        return LEGACY_HARD_STOP_LOSS_PCT
    # NaN 은 모든 비교가 False → not(...) 로 LEGACY. 0/음수/1 이상도 비정상.
    if not (0.0 < cap < 1.0):
        return LEGACY_HARD_STOP_LOSS_PCT
    return cap


def pos_hard_floor(pos: dict, entry_price: float | None = None) -> float:
    """하드 손절 바닥 = 진입가 × (1 - 그 포지션의 캡).

    entry_price 를 주면 그 값을 기준으로 한다(체결가 보정처럼 진입가가 바뀌는 경로용).
    캡은 항상 포지션 자신의 것이다.
    """
    base = pos["entry_price"] if entry_price is None else entry_price
    return base * (1 - pos_hard_cap(pos))
