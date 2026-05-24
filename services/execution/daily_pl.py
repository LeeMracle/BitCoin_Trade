"""일일 실현 손익 추적 + 한도 체크 (decision 20260504-1, plan 20260504_2 C).

매도 시 호출하여 KRW 손익 누적, 매수 직전 한도 도달 여부 체크.
매일 09:00 KST 자동 reset (daily_live.py 시작 + _refresh_levels 호출 시).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
_STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "daily_pl_state.json"


def _today_str() -> str:
    return datetime.now(tz=KST).strftime("%Y-%m-%d")


def _load() -> dict:
    if not _STATE_FILE.exists():
        return {"date": _today_str(), "realized_pl_krw": 0.0, "blocked": False, "events": []}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"date": _today_str(), "realized_pl_krw": 0.0, "blocked": False, "events": []}


def _save(state: dict) -> None:
    """atomic write (lessons #24)."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(_STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE_FILE)


def reset_if_new_day() -> None:
    """daily_live.py 시작 + _refresh_levels 시점에 호출. 오늘 날짜와 다르면 reset."""
    state = _load()
    today = _today_str()
    if state.get("date") != today:
        new_state = {
            "date": today,
            "realized_pl_krw": 0.0,
            "blocked": False,
            "events": [],
            "previous_day_pl_krw": state.get("realized_pl_krw", 0.0),
            "previous_day_date": state.get("date", "?"),
        }
        _save(new_state)
        return True
    return False


def record_sell(symbol: str, pl_krw: float, exec_price: float, sold_qty: float) -> dict:
    """매도 체결 시 호출 — 실현 손익 누적.

    Args:
        pl_krw: (exec_price - entry_price) * sold_qty (이미 계산된 KRW 손익)

    Returns:
        갱신된 state
    """
    state = _load()
    today = _today_str()
    if state.get("date") != today:
        # 자동 reset 후 누적
        state = {
            "date": today,
            "realized_pl_krw": 0.0,
            "blocked": False,
            "events": [],
            "previous_day_pl_krw": state.get("realized_pl_krw", 0.0),
            "previous_day_date": state.get("date", "?"),
        }
    state["realized_pl_krw"] = round(state.get("realized_pl_krw", 0.0) + pl_krw, 2)
    state.setdefault("events", []).append({
        "ts": datetime.now(tz=KST).isoformat(),
        "symbol": symbol,
        "pl_krw": round(pl_krw, 2),
        "exec_price": exec_price,
        "sold_qty": sold_qty,
    })
    # 이벤트 50개 제한 (성능)
    if len(state["events"]) > 50:
        state["events"] = state["events"][-50:]
    _save(state)
    return state


def is_daily_loss_blocked(limit_pct: float, base_krw: float) -> tuple[bool, float, float]:
    """매수 직전 호출 — 일일 손실 한도 도달 여부.

    Returns:
        (blocked, current_loss_pct, limit_pct)
    """
    state = _load()
    today = _today_str()
    if state.get("date") != today:
        return False, 0.0, limit_pct  # 자동 reset 케이스

    pl = state.get("realized_pl_krw", 0.0)
    if pl >= 0:
        return False, 0.0, limit_pct

    loss_pct = abs(pl) / base_krw
    blocked = loss_pct >= limit_pct
    if blocked and not state.get("blocked"):
        # 첫 발동 — flag 갱신
        state["blocked"] = True
        state["blocked_at"] = datetime.now(tz=KST).isoformat()
        _save(state)
    return blocked, loss_pct, limit_pct


def get_state() -> dict:
    """헬스체크 또는 보고용 — 현재 상태 조회."""
    return _load()
