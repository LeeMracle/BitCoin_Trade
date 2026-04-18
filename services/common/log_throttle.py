"""경량 로그 스로틀 헬퍼.

동일한 key 로그가 반복되는 상황(잔고 조회마다 같은 경고 출력 등)에서
interval_sec 경과 시에만 실제 print를 수행하고, 억제된 횟수를 집계한다.

배경: lessons/20260410_1_cb_log_spam.md — 이벤트 루프 내 로그는 throttle 필수.
      CB 로그(realtime_monitor.py)와 동일한 패턴을 공통 헬퍼로 추출.

사용 예:
    from services.common.log_throttle import throttled_print
    throttled_print("balance_noMarket_ONG", "  [잔고] ONG/KRW 마켓 없음 — 평가액 제외")
"""
from __future__ import annotations

import time
from typing import Dict

# 모듈 전역 상태: {key: last_printed_monotonic_ts}
_last_ts: Dict[str, float] = {}

# 억제(출력 건너뜀) 횟수 집계: {key: suppressed_count}
_suppressed: Dict[str, int] = {}


def throttled_print(key: str, msg: str, interval_sec: float = 60.0) -> None:
    """interval_sec 이내에 같은 key로 이미 출력했으면 억제한다.

    Args:
        key:          throttle 식별 키 (예: "balance_noMarket_ONG")
        msg:          출력할 메시지 문자열
        interval_sec: 같은 key 재출력 허용 간격 (기본 60초)
    """
    now = time.monotonic()
    last = _last_ts.get(key, 0.0)
    if now - last >= interval_sec:
        # 억제에서 해제 — 실제 출력
        print(msg, flush=True)
        _last_ts[key] = now
    else:
        # 출력 억제 — 억제 횟수 누적
        _suppressed[key] = _suppressed.get(key, 0) + 1


def get_throttle_counters() -> Dict[str, int]:
    """key별 억제 횟수 현황을 반환한다.

    Returns:
        {key: suppressed_count} — 한 번도 억제되지 않은 key는 포함되지 않는다.
    """
    return dict(_suppressed)


def reset_throttle_state() -> None:
    """전역 throttle 상태를 초기화한다.

    테스트 격리 목적으로 사용. 프로덕션 코드에서는 호출하지 말 것.
    """
    _last_ts.clear()
    _suppressed.clear()
