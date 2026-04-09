"""VB(변동성 돌파) 개선 필터 — P5-28 구현.

P4-06 NO-GO 판단(2026-04-09) 후속 개선안 A~D의 핵심 로직을 순수 함수로
분리하여 단위 테스트 가능성과 재사용성을 확보한다.

구현된 필터:
    A. 하락장 필터    — realtime_monitor 측 `_btc_above_ema` 플래그 직접 사용
    B. 데드 종목 블랙리스트 — `compute_dead_symbols`
    C. 종목 집중도 캡  — `iso_week`, `weekly_count_exceeded`, `bump_weekly_count`
    D. 연패 쿨다운    — `recent_consecutive_losses`, `is_in_loss_cooldown`,
                        `set_loss_cooldown`

참조:
    docs/00.보고/20260409_일일작업.md (P4-05/06 DoD + P5-28 개선안)
    docs/lessons/ (향후 P5-28 재검증 후 추가 예정)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any


# ─── B. 데드 종목 블랙리스트 ─────────────────────────────

def compute_dead_symbols(history: list[dict], threshold: int = 3) -> list[str]:
    """연속 N회 이상 0% 수익으로 청산된 종목을 데드 목록으로 반환.

    "데드 트레이드"는 진입·청산가가 같아 수수료만 손실로 남는 케이스
    (ELF/KRW 3회 반복 0% 같은 패턴). 거래 순서를 유지한 history에서
    종목별 최근 N건이 모두 return_pct == 0 이면 데드로 판정.

    Parameters
    ----------
    history : list[dict]
        vb_state["history"] — 최신순이 마지막
    threshold : int
        연속 0% 건수 임계값 (기본 3)

    Returns
    -------
    list[str]
        데드 처리된 심볼 목록 (정렬)
    """
    if threshold <= 0:
        return []

    # 종목별 거래 수익률 시퀀스 (발생 순서 유지)
    seq: dict[str, list[float]] = {}
    for h in history:
        sym = h.get("symbol")
        if not sym:
            continue
        rp = h.get("return_pct", 0) or 0
        seq.setdefault(sym, []).append(float(rp))

    dead = []
    for sym, rets in seq.items():
        if len(rets) < threshold:
            continue
        # 마지막 N건이 모두 0% 이면 데드
        if all(r == 0 for r in rets[-threshold:]):
            dead.append(sym)
    return sorted(dead)


# ─── C. 종목 집중도 캡 ───────────────────────────────────

def iso_week(dt: datetime | None = None) -> str:
    """ISO 8601 주차 문자열 반환 (예: 2026-W15)."""
    if dt is None:
        dt = datetime.now(tz=timezone.utc)
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def weekly_count_exceeded(
    weekly_count: dict[str, dict[str, int]],
    symbol: str,
    limit: int,
    dt: datetime | None = None,
) -> bool:
    """심볼이 현재 ISO 주에 `limit` 회 이상 진입했는지 확인.

    weekly_count 스키마: {"2026-W15": {"POLYX/KRW": 3, ...}, ...}
    """
    week = iso_week(dt)
    bucket = weekly_count.get(week, {})
    return bucket.get(symbol, 0) >= limit


def bump_weekly_count(
    weekly_count: dict[str, dict[str, int]],
    symbol: str,
    dt: datetime | None = None,
) -> None:
    """주간 카운트 +1 (in-place). 현재 주 이전 기록은 2주 이전까지만 유지."""
    week = iso_week(dt)
    weekly_count.setdefault(week, {})
    weekly_count[week][symbol] = weekly_count[week].get(symbol, 0) + 1

    # 오래된 주 정리 (현재 주 기준 3주 이전 버킷 제거)
    if dt is None:
        dt = datetime.now(tz=timezone.utc)
    cutoff_dt = dt - timedelta(weeks=3)
    cutoff_week = iso_week(cutoff_dt)
    for k in list(weekly_count.keys()):
        if k < cutoff_week:
            weekly_count.pop(k, None)


# ─── D. 연패 쿨다운 ──────────────────────────────────────

def recent_consecutive_losses(history: list[dict]) -> int:
    """history 꼬리에서 연속 손절(return_pct < 0) 건수 카운트.

    이유(reason)가 "손절"을 포함하는 기록만 연패로 간주. "1일 회전"은
    손실이더라도 구조적 청산이므로 제외 (P4-05 집계에서 회전 로스는
    개별 신호 실패가 아님).
    """
    n = 0
    for h in reversed(history):
        reason = h.get("reason", "") or ""
        rp = h.get("return_pct", 0) or 0
        if "손절" in reason and rp < 0:
            n += 1
        else:
            break
    return n


def is_in_loss_cooldown(
    cooldown_until_iso: str | None,
    now: datetime | None = None,
) -> bool:
    """cooldown_until_iso가 현재 시각 이후면 쿨다운 중."""
    if not cooldown_until_iso:
        return False
    try:
        until = datetime.fromisoformat(cooldown_until_iso)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(tz=timezone.utc)
    return now < until


def set_loss_cooldown(
    hours: int,
    now: datetime | None = None,
) -> str:
    """현재 시각 + hours 시간 후를 ISO 문자열로 반환."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    until = now + timedelta(hours=hours)
    return until.isoformat()
