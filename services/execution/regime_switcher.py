# -*- coding: utf-8 -*-
"""레짐 자동 전환 모듈 (P5-04).

BTC SMA50 / EMA200 / F&G 조합으로 BULL / BEAR / SIDEWAYS를 판정하고
히스테리시스(연속 3회 동일 신호)를 적용해 전환을 결정한다.

DRY-RUN 전제: REGIME_SWITCH_ENABLED=False 상태에서는 판정 로직만 가동하며
실거래 정책에 영향을 주지 않는다.

참조:
- workspace/plans/20260417_block3_vb_recheck.md (설계 문서)
- services/execution/config.py (REGIME_SWITCH_ENABLED 등 상수)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

# 상태 파일 기본 경로 (프로젝트 루트 기준 workspace/)
_DEFAULT_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "workspace" / "regime_state.json"
)

# recent_signals 보관 최대 길이
_MAX_SIGNALS = 5

# 히스테리시스 전환 판단에 사용하는 연속 신호 수
_HYSTERESIS_COUNT = 3


# ══════════════════════════════════════════════════════════
# 도메인 타입
# ══════════════════════════════════════════════════════════

class Regime(str, Enum):
    """레짐 열거형."""

    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeDecision:
    """단일 레짐 판정 결과."""

    regime: Regime
    reason: str           # 예: "BTC<EMA200 AND F&G=21"
    btc_close: float
    ema200: float
    sma50: float
    fg: int
    decided_at_ts: int    # UTC Unix timestamp (초)


# ══════════════════════════════════════════════════════════
# 순수 판정 함수 (I/O 없음)
# ══════════════════════════════════════════════════════════

def decide_regime(
    btc_close: float,
    sma50: float,
    ema200: float,
    fg: int,
) -> RegimeDecision:
    """BTC 종가·이동평균·공포탐욕지수로 레짐을 판정한다.

    규칙:
    - BULL  : btc_close > ema200 AND fg >= 40
    - BEAR  : btc_close < ema200 OR fg < 20
    - SIDEWAYS: 그 외 (두 조건에 걸리지 않는 경우)

    BEAR 조건이 BULL 조건보다 우선 평가된다(보수적 설계).

    예외 발생 시 SIDEWAYS를 기본값으로 반환한다.
    """
    try:
        ts = int(time.time())

        # BEAR 먼저 평가 (보수적 우선)
        bear_ema = btc_close < ema200
        bear_fg = fg < 20
        if bear_ema or bear_fg:
            parts = []
            if bear_ema:
                parts.append(f"BTC({btc_close:,.0f})<EMA200({ema200:,.0f})")
            if bear_fg:
                parts.append(f"F&G={fg}<20")
            return RegimeDecision(
                regime=Regime.BEAR,
                reason=" AND ".join(parts),
                btc_close=btc_close,
                ema200=ema200,
                sma50=sma50,
                fg=fg,
                decided_at_ts=ts,
            )

        # BULL 평가
        bull_ema = btc_close > ema200
        bull_fg = fg >= 40
        if bull_ema and bull_fg:
            return RegimeDecision(
                regime=Regime.BULL,
                reason=f"BTC({btc_close:,.0f})>EMA200({ema200:,.0f}) AND F&G={fg}>=40",
                btc_close=btc_close,
                ema200=ema200,
                sma50=sma50,
                fg=fg,
                decided_at_ts=ts,
            )

        # SIDEWAYS (나머지)
        return RegimeDecision(
            regime=Regime.SIDEWAYS,
            reason=f"BTC({btc_close:,.0f}) EMA200({ema200:,.0f}) F&G={fg} — 중립",
            btc_close=btc_close,
            ema200=ema200,
            sma50=sma50,
            fg=fg,
            decided_at_ts=ts,
        )

    except Exception as exc:  # noqa: BLE001
        # 예외 발생 시 SIDEWAYS 기본 반환 (실거래 영향 최소화)
        return RegimeDecision(
            regime=Regime.SIDEWAYS,
            reason=f"예외 발생 — 기본 SIDEWAYS 반환: {exc}",
            btc_close=float(btc_close) if btc_close else 0.0,
            ema200=float(ema200) if ema200 else 0.0,
            sma50=float(sma50) if sma50 else 0.0,
            fg=int(fg) if fg else 0,
            decided_at_ts=int(time.time()),
        )


# ══════════════════════════════════════════════════════════
# 상태 파일 I/O
# ══════════════════════════════════════════════════════════

def _default_state() -> dict:
    """초기 기본 상태를 반환한다."""
    return {
        "current": "UNKNOWN",
        "since_ts": 0,
        "prev": "UNKNOWN",
        "last_decided_ts": 0,
        "recent_signals": [],
        "enabled": False,
    }


def load_state(path: Path | None = None) -> dict:
    """workspace/regime_state.json 에서 상태를 읽어 반환한다.

    파일이 없거나 파싱에 실패하면 기본 상태를 반환한다.
    """
    target = Path(path) if path else _DEFAULT_STATE_PATH
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        # 필수 키 보정 (이전 버전 호환)
        merged = _default_state()
        merged.update(data)
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_state()


def save_state(state: dict, path: Path | None = None) -> None:
    """상태 dict를 workspace/regime_state.json 에 저장한다."""
    target = Path(path) if path else _DEFAULT_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════
# 히스테리시스 적용 전환 로직
# ══════════════════════════════════════════════════════════

def update_with_decision(
    decision: RegimeDecision,
    state: dict | None = None,
    path: Path | None = None,
) -> dict:
    """히스테리시스를 적용해 current 레짐을 갱신하고 저장된 상태를 반환한다.

    - recent_signals에 최신 판정을 추가 (최대 _MAX_SIGNALS 개 보관).
    - 마지막 _HYSTERESIS_COUNT 개가 모두 동일하고 current와 다르면 전환한다.
    - state 인수를 넘기지 않으면 파일에서 읽는다.
    """
    if state is None:
        state = load_state(path)

    # recent_signals 갱신
    signals: list = list(state.get("recent_signals", []))
    signals.append(decision.regime.value)
    if len(signals) > _MAX_SIGNALS:
        signals = signals[-_MAX_SIGNALS:]
    state["recent_signals"] = signals
    state["last_decided_ts"] = decision.decided_at_ts

    # 히스테리시스 전환 판단
    if len(signals) >= _HYSTERESIS_COUNT:
        last_n = signals[-_HYSTERESIS_COUNT:]
        if len(set(last_n)) == 1:  # 마지막 N개가 모두 동일
            new_regime = last_n[0]
            if new_regime != state.get("current"):
                state["prev"] = state.get("current", "UNKNOWN")
                state["current"] = new_regime
                state["since_ts"] = decision.decided_at_ts

    save_state(state, path)
    return state


# ══════════════════════════════════════════════════════════
# 알림 판단 & 포맷
# ══════════════════════════════════════════════════════════

def should_notify(prev_state: dict, new_state: dict) -> bool:
    """current 레짐이 바뀌었을 때 True를 반환한다."""
    return prev_state.get("current") != new_state.get("current")


def format_notification(old: str, new: str, reason: str) -> str:
    """레짐 전환 텔레그램 알림 메시지를 반환한다.

    예시:
        [BATA] 레짐 전환: BEAR → BULL
        사유: BTC(120,000,000)>EMA200(110,000,000) AND F&G=45>=40
    """
    return (
        f"[BATA] 레짐 전환: {old} → {new}\n"
        f"사유: {reason}"
    )
