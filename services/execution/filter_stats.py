"""필터 차단 이벤트 카운터 + 영구화.

용도:
  - F&G / EMA200 / ATR% / CB-L1 / CB-L2 / VB-A(하락장) 등 필터별 차단 건수 집계
  - 일일 자정 기준 롤오버
  - workspace/filter_stats.json 영구화

호출 지점:
  record_block(reason: str, symbol: str | None) — 차단 발생 시 1회 호출
  snapshot() -> dict — 현재 누적 반환
  reset_today() — 자정 롤오버
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

# 상태 파일 경로 (vb_state.json 관례에 맞춰 상대경로)
_STATS_FILE = Path("workspace/filter_stats.json")
_HISTORY_FILE = Path("workspace/filter_stats_history.jsonl")

# 1분 간격 flush (성능 — fsync 없음)
_FLUSH_INTERVAL_SEC = 60.0

# 허용 reason 키 목록
VALID_REASONS = frozenset({
    "fg_gate",          # per-symbol 매수 차단 (스케일: 틱 빈도)
    "fg_gate_daily",    # 레벨 갱신 시 1회 스냅샷 (스케일: 1/일)
    "ema200_filter",
    "atr_filter",
    "cb_l1",
    "cb_l2",
    "vb_gate_a_bearish",
    "vb_gate_b_deadlist",
    "vb_gate_c_weekly3",
    "vb_gate_d_cooldown",
})

# ── 내부 상태 ──
_state: dict = {}          # {"date": str, "counters": dict, "by_symbol": dict}
_last_flush: float = 0.0   # monotonic 기준 마지막 flush 시각
_loaded: bool = False      # 최초 로드 여부


def _today_str() -> str:
    return date.today().isoformat()


def _empty_state() -> dict:
    return {
        "date": _today_str(),
        "counters": {},
        "by_symbol": {},
    }


def _ensure_loaded() -> None:
    """최초 1회 파일에서 상태를 로드한다."""
    global _state, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if _STATS_FILE.exists():
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 날짜가 달라지면 롤오버
            if data.get("date") != _today_str():
                _append_history(data)
                _state = _empty_state()
            else:
                _state = data
        else:
            _state = _empty_state()
    except Exception:
        _state = _empty_state()


def _flush(force: bool = False) -> None:
    """상태를 파일에 기록한다. 1분 간격으로 throttle."""
    global _last_flush
    now = time.monotonic()
    if not force and (now - _last_flush < _FLUSH_INTERVAL_SEC):
        return
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(_STATS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
        Path(tmp).replace(_STATS_FILE)
        _last_flush = now
    except Exception:
        pass  # silent — 통계 실패가 서비스를 멈추지 않음


def _append_history(old_state: dict) -> None:
    """이전 날짜 데이터를 history.jsonl에 append."""
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_state, ensure_ascii=False) + "\n")
    except Exception:
        pass  # silent


def record_block(reason: str, symbol: Optional[str]) -> None:
    """차단 이벤트를 기록한다.

    Args:
        reason: 차단 사유 키 (VALID_REASONS 중 하나)
        symbol: 종목 심볼 (없으면 None)
    """
    try:
        _ensure_loaded()

        # 날짜 롤오버 확인
        if _state.get("date") != _today_str():
            reset_today()

        # reason 정규화 — 미지원 키는 "unknown" 처리
        key = reason if reason in VALID_REASONS else f"unknown_{reason}"

        counters = _state.setdefault("counters", {})
        counters[key] = counters.get(key, 0) + 1

        if symbol is not None:
            by_symbol = _state.setdefault("by_symbol", {})
            sym_map = by_symbol.setdefault(key, {})
            sym_map[symbol] = sym_map.get(symbol, 0) + 1

        _flush()
    except Exception:
        pass  # silent — 통계 실패가 서비스를 멈추지 않음


def snapshot() -> dict:
    """현재 누적 카운터를 반환한다."""
    try:
        _ensure_loaded()
        return {
            "date": _state.get("date", _today_str()),
            "counters": dict(_state.get("counters", {})),
            "by_symbol": {
                k: dict(v) for k, v in _state.get("by_symbol", {}).items()
            },
        }
    except Exception:
        return {"date": _today_str(), "counters": {}, "by_symbol": {}}


def reset_today() -> None:
    """자정 롤오버: 이전 날짜 결과를 history에 저장하고 초기화."""
    global _state
    try:
        _ensure_loaded()
        if _state:
            _append_history(_state)
        _state = _empty_state()
        _flush(force=True)
    except Exception:
        _state = _empty_state()
