"""계좌 레벨 서킷브레이커 — 전체 손실 -20% 시 신규 매수 자동 차단.

동작 원칙:
  - 계좌 전체 평가금액 < 초기자본 * (1 + THRESHOLD) → 서킷브레이커 발동
  - 발동 시 모든 전략(Composite + VB)의 신규 매수 차단
  - 기존 포지션은 유지, 트레일링스탑은 계속 작동
  - 해제: 수동만 가능 (circuit_breaker_state.json 삭제 또는 triggered=false 설정)
  - 상태 파일 기반 저장 → 봇 재시작 시에도 차단 유지

상태 파일: workspace/circuit_breaker_state.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "circuit_breaker_state.json"


def _load_state() -> dict:
    """상태 파일에서 서킷브레이커 상태 로드."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"triggered": False, "triggered_at": None, "total_krw_at_trigger": None}


def _save_state(state: dict) -> None:
    """서킷브레이커 상태를 파일로 저장."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_triggered() -> bool:
    """현재 서킷브레이커 발동 여부 반환."""
    return _load_state().get("triggered", False)


def trigger(total_krw: float, initial_capital: float, threshold: float) -> None:
    """서킷브레이커 발동 — 상태 파일에 기록."""
    state = {
        "triggered": True,
        "triggered_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_krw_at_trigger": total_krw,
        "initial_capital": initial_capital,
        "threshold": threshold,
        "loss_pct": (total_krw - initial_capital) / initial_capital * 100,
    }
    _save_state(state)


def check_and_trigger(total_krw: float) -> bool:
    """계좌 평가금액을 확인하여 서킷브레이커 발동 여부를 결정.

    Args:
        total_krw: 현재 계좌 전체 평가금액 (KRW, 코인 환산 포함)

    Returns:
        True  — 서킷브레이커가 이번 호출로 새로 발동됨
        False — 이미 발동 중이었거나 미발동
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
        return False  # 이미 발동 중

    floor = CIRCUIT_BREAKER_INITIAL_CAPITAL * (1 + CIRCUIT_BREAKER_THRESHOLD)
    if total_krw <= floor:
        trigger(total_krw, CIRCUIT_BREAKER_INITIAL_CAPITAL, CIRCUIT_BREAKER_THRESHOLD)
        return True

    return False


def get_status() -> dict:
    """서킷브레이커 현재 상태 딕셔너리 반환 (로깅/알림용)."""
    return _load_state()
