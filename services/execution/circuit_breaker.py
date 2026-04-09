"""계좌 레벨 서킷브레이커 — 2단계 보호.

L1 (신규 차단):
  - 계좌 전체 평가금액 < 초기자본 * (1 + L1_THRESHOLD) → 발동
  - 모든 전략(Composite + VB)의 신규 매수 차단
  - 기존 포지션은 유지, 트레일링스탑 계속 작동
  - 해제: 자동(95% 회복) 또는 수동 (state.triggered=false)

L2 (전량 청산):
  - ADR 20260408_1 기반 2차 안전장치
  - 계좌 전체 평가금액 < 초기자본 * (1 + L2_THRESHOLD) → 발동
  - 모든 보유 포지션 시장가 전량 청산 (호출자가 실행)
  - 해제: 수동만 (state.l2_triggered=false)

상태 파일: workspace/circuit_breaker_state.json
스키마:
  {
    "triggered": bool,              # L1
    "triggered_at": str|None,
    "total_krw_at_trigger": float|None,
    "initial_capital": float,
    "threshold": float,
    "loss_pct": float,
    "l2_triggered": bool,           # L2
    "l2_triggered_at": str|None,
    "l2_total_krw_at_trigger": float|None,
    "l1_auto_resumed_at": str|None, # L1 자동 해제 기록
  }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "circuit_breaker_state.json"


def _default_state() -> dict:
    return {
        "triggered": False,
        "triggered_at": None,
        "total_krw_at_trigger": None,
        "l2_triggered": False,
        "l2_triggered_at": None,
        "l2_total_krw_at_trigger": None,
        "l1_auto_resumed_at": None,
    }


def _load_state() -> dict:
    """상태 파일에서 서킷브레이커 상태 로드."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 기존(L1 only) 스키마 → L2 필드 보강
                base = _default_state()
                base.update(data)
                return base
        except (json.JSONDecodeError, OSError):
            pass
    return _default_state()


def _save_state(state: dict) -> None:
    """서킷브레이커 상태를 파일로 저장."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def is_triggered() -> bool:
    """L1 서킷브레이커 발동 여부."""
    return _load_state().get("triggered", False)


def is_l2_triggered() -> bool:
    """L2 서킷브레이커 발동 여부."""
    return _load_state().get("l2_triggered", False)


def trigger(total_krw: float, initial_capital: float, threshold: float) -> None:
    """L1 서킷브레이커 발동 — 상태 파일에 기록."""
    state = _load_state()
    state.update({
        "triggered": True,
        "triggered_at": _now_iso(),
        "total_krw_at_trigger": total_krw,
        "initial_capital": initial_capital,
        "threshold": threshold,
        "loss_pct": (total_krw - initial_capital) / initial_capital * 100,
    })
    _save_state(state)


def trigger_l2(total_krw: float, initial_capital: float, threshold: float) -> None:
    """L2 서킷브레이커 발동 — 상태 파일에 기록."""
    state = _load_state()
    state.update({
        "l2_triggered": True,
        "l2_triggered_at": _now_iso(),
        "l2_total_krw_at_trigger": total_krw,
        "l2_initial_capital": initial_capital,
        "l2_threshold": threshold,
        "l2_loss_pct": (total_krw - initial_capital) / initial_capital * 100,
    })
    _save_state(state)


def check_and_trigger(total_krw: float) -> bool:
    """계좌 평가금액을 확인하여 L1 발동 여부 결정.

    Returns:
        True  — L1이 이번 호출로 새로 발동됨
        False — 이미 발동 중이거나 미발동
    """
    from services.execution.config import (
        CIRCUIT_BREAKER_ENABLED,
        CIRCUIT_BREAKER_THRESHOLD,
        CIRCUIT_BREAKER_INITIAL_CAPITAL,
    )

    if not CIRCUIT_BREAKER_ENABLED:
        return False

    state = _load_state()
    if state.get("triggered"):
        return False

    floor = CIRCUIT_BREAKER_INITIAL_CAPITAL * (1 + CIRCUIT_BREAKER_THRESHOLD)
    if total_krw <= floor:
        trigger(total_krw, CIRCUIT_BREAKER_INITIAL_CAPITAL, CIRCUIT_BREAKER_THRESHOLD)
        return True

    return False


def check_and_trigger_l2(total_krw: float) -> bool:
    """계좌 평가금액을 확인하여 L2 발동 여부 결정.

    Returns:
        True  — L2가 이번 호출로 새로 발동됨 (호출자는 전량 청산 실행 필수)
        False — 이미 L2 발동 중이거나 미발동
    """
    from services.execution.config import (
        CIRCUIT_BREAKER_ENABLED,
        CIRCUIT_BREAKER_L2_THRESHOLD,
        CIRCUIT_BREAKER_INITIAL_CAPITAL,
    )

    if not CIRCUIT_BREAKER_ENABLED:
        return False

    state = _load_state()
    if state.get("l2_triggered"):
        return False

    floor = CIRCUIT_BREAKER_INITIAL_CAPITAL * (1 + CIRCUIT_BREAKER_L2_THRESHOLD)
    if total_krw <= floor:
        trigger_l2(total_krw, CIRCUIT_BREAKER_INITIAL_CAPITAL, CIRCUIT_BREAKER_L2_THRESHOLD)
        return True

    return False


def check_l1_auto_resume(total_krw: float) -> bool:
    """L1 자동 해제 체크 — 총자산이 초기자본의 N% 이상 회복되면 해제.

    L2가 발동 중이면 절대 해제하지 않음 (L2는 수동만).

    Returns:
        True  — L1이 이번 호출로 자동 해제됨
        False — 해제 조건 미달 또는 L2 발동 중
    """
    from services.execution.config import (
        CIRCUIT_BREAKER_ENABLED,
        CIRCUIT_BREAKER_INITIAL_CAPITAL,
        CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT,
    )

    if not CIRCUIT_BREAKER_ENABLED:
        return False

    state = _load_state()
    if not state.get("triggered"):
        return False
    if state.get("l2_triggered"):
        return False  # L2 발동 중이면 L1 자동 해제 금지

    resume_floor = CIRCUIT_BREAKER_INITIAL_CAPITAL * CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT
    if total_krw >= resume_floor:
        state["triggered"] = False
        state["l1_auto_resumed_at"] = _now_iso()
        state["l1_auto_resumed_total_krw"] = total_krw
        _save_state(state)
        return True
    return False


def get_status() -> dict:
    """서킷브레이커 현재 상태 딕셔너리 반환 (로깅/알림용)."""
    return _load_state()
