"""Shadow Mode 로거 — 차단/허용 결정과 실제 결과를 추후 비교하기 위한 기록.

3개월 누적 후 분석:
    - "ML이 차단했지만 실제 +5% 도달한 케이스" = false negative
    - "ML이 허용했지만 손절 케이스" = false positive
    - 이 비교가 LIVE 모드 전환 근거.

저장 형식: JSONL (workspace/ml_shadow/{YYYYMMDD}.jsonl)
    한 줄당 1 의사결정 + 향후 outcome merge 가능 (signal_ts + symbol = key).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from services.ml.config import SHADOW_LOG_DIR, ensure_dirs

log = logging.getLogger(__name__)

_write_lock = threading.Lock()


def _path_for(ts: datetime) -> Path:
    return SHADOW_LOG_DIR / f"{ts.strftime('%Y%m%d')}.jsonl"


def log_decision(
    *,
    symbol: str,
    signal_ts: pd.Timestamp,
    signal_type: str,
    score: float,
    threshold: float,
    will_buy: bool,
    ml_active: bool,
    extra: Optional[dict] = None,
) -> None:
    """의사결정 1건 기록 (multi_trader 매수 분기에서 호출)."""
    ensure_dirs()
    rec = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "signal_ts": signal_ts.isoformat() if hasattr(signal_ts, "isoformat") else str(signal_ts),
        "signal_type": signal_type,
        "score": round(float(score), 4),
        "threshold": round(float(threshold), 4),
        "will_buy": bool(will_buy),
        "ml_active": bool(ml_active),
    }
    if extra:
        rec["extra"] = extra
    path = _path_for(datetime.now(timezone.utc))
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_outcome(
    *,
    symbol: str,
    signal_ts: pd.Timestamp,
    outcome_pct: float,
    reached_target: bool,
    horizon_bars_actual: int,
) -> None:
    """추후 (배치) 진입 결과를 같은 파일에 append — outcome 라인은 'kind=outcome' 마킹."""
    ensure_dirs()
    rec = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "kind": "outcome",
        "symbol": symbol,
        "signal_ts": signal_ts.isoformat() if hasattr(signal_ts, "isoformat") else str(signal_ts),
        "outcome_pct": round(float(outcome_pct), 4),
        "reached_target": bool(reached_target),
        "horizon_bars_actual": int(horizon_bars_actual),
    }
    sig_dt = pd.Timestamp(signal_ts).to_pydatetime() if not isinstance(signal_ts, datetime) else signal_ts
    path = _path_for(sig_dt if sig_dt.tzinfo else sig_dt.replace(tzinfo=timezone.utc))
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def summary(days: int = 30) -> dict:
    """최근 N일 shadow 로그 요약 (의사결정 분포 등)."""
    ensure_dirs()
    cutoff = datetime.now(timezone.utc) - pd.Timedelta(days=days)
    n_decisions = n_buy = n_block = 0
    score_sum = 0.0
    for path in sorted(SHADOW_LOG_DIR.glob("*.jsonl")):
        try:
            stamp = datetime.strptime(path.stem, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < cutoff:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "outcome":
                continue
            n_decisions += 1
            score_sum += rec.get("score", 0.0)
            if rec.get("will_buy"):
                n_buy += 1
            else:
                n_block += 1
    return {
        "days": days,
        "decisions": n_decisions,
        "buys": n_buy,
        "blocks": n_block,
        "block_rate": round(n_block / n_decisions, 3) if n_decisions else 0.0,
        "mean_score": round(score_sum / n_decisions, 4) if n_decisions else 0.0,
    }
