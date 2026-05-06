"""Shadow JSONL 의사결정에 대한 outcome 자동 매칭.

흐름:
    1. N일 전 shadow JSONL 로드 (kind != "outcome", will_buy 무관 — 모든 결정)
    2. 각 결정의 신호 시점 + 24h 윈도우의 OHLCV fetch (ccxt 4h 6봉)
    3. high가 entry × (1 + LABEL_TARGET_PCT + slippage) 도달 여부 → reached_target
    4. shadow.log_outcome() 호출 (같은 날짜 JSONL에 kind=outcome 라인 append)

핵심 정책:
    - 매칭은 "결정 시점으로부터 horizon 봉 후"가 fully 지난 결정만
    - 즉 매일 03:00 KST cron이면 어제(또는 N일 전) 데이터 매칭
    - idempotent: 이미 매칭된 결정은 skip (signal_ts + symbol 키)

ccxt 호출은 read-only (fetch_ohlcv) — 인증 불필요, fail-safe.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd

from services.ml import shadow
from services.ml.config import LABEL_HORIZON_BARS, LABEL_SLIPPAGE_PCT, LABEL_TARGET_PCT, SHADOW_LOG_DIR

log = logging.getLogger(__name__)

# 4h 봉 기준 — 24h 윈도우 = 6봉
DEFAULT_HORIZON_HOURS = 24
DEFAULT_TIMEFRAME = "4h"


def _exchange() -> ccxt.upbit:
    """공개 시세 전용 — 인증 불필요, 매번 새 인스턴스 (cron 단독 실행이라 OK)."""
    return ccxt.upbit({"enableRateLimit": True})


def _ccxt_symbol(symbol: str) -> str:
    """KRW-BTC → BTC/KRW (ccxt 형식)."""
    if "/" in symbol:
        return symbol
    if "-" in symbol:
        a, b = symbol.split("-", 1)
        # KRW-BTC → BTC/KRW (업비트는 quote가 KRW)
        return f"{b}/{a}" if a in ("KRW", "USDT", "BTC") else f"{a}/{b}"
    return symbol


def _load_decisions(date_str: str) -> list[dict]:
    """{YYYYMMDD}.jsonl 의 의사결정 로드 (outcome 라인 제외)."""
    path = SHADOW_LOG_DIR / f"{date_str}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") != "outcome":
            out.append(rec)
    return out


def _already_matched(date_str: str) -> set[tuple[str, str]]:
    """이미 outcome 라인이 있는 (signal_ts, symbol) 집합 — idempotent 체크."""
    path = SHADOW_LOG_DIR / f"{date_str}.jsonl"
    if not path.exists():
        return set()
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") == "outcome":
            seen.add((rec.get("signal_ts", ""), rec.get("symbol", "")))
    return seen


def fetch_high_in_window(
    ex: ccxt.upbit,
    ccxt_sym: str,
    signal_ts: pd.Timestamp,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> tuple[Optional[float], int]:
    """signal_ts 직후 horizon_hours 동안의 최고가 + 사용된 봉 수.

    Returns: (high, n_bars). high=None 시 fetch 실패.
    """
    since_ms = int(signal_ts.timestamp() * 1000)
    # 4h 6봉 + 안전 여유 2봉
    n_request = horizon_hours // 4 + 2
    try:
        bars = ex.fetch_ohlcv(ccxt_sym, timeframe=timeframe, since=since_ms, limit=n_request)
    except Exception as e:
        log.warning("fetch_ohlcv 실패 %s @%s: %s", ccxt_sym, signal_ts, e)
        return None, 0

    if not bars:
        return None, 0

    end_ms = since_ms + horizon_hours * 3600 * 1000
    in_window = [b for b in bars if since_ms < b[0] <= end_ms]
    if not in_window:
        return None, 0
    highs = [b[2] for b in in_window]  # OHLCV bar = [ts, open, high, low, close, volume]
    return max(highs), len(in_window)


def match_date(
    date_str: str,
    *,
    target_pct: float = LABEL_TARGET_PCT,
    slippage_pct: float = LABEL_SLIPPAGE_PCT,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
) -> dict:
    """{YYYYMMDD}의 결정에 대해 outcome 매칭. 통계 dict 반환."""
    decisions = _load_decisions(date_str)
    if not decisions:
        return {"date": date_str, "n_decisions": 0, "matched": 0, "skipped": 0}

    seen = _already_matched(date_str)
    ex = _exchange()
    n_matched = n_skip = n_fail = 0
    n_reached = 0

    for rec in decisions:
        sig_ts_str = rec.get("signal_ts", "")
        symbol = rec.get("symbol", "")
        key = (sig_ts_str, symbol)
        if key in seen:
            n_skip += 1
            continue

        try:
            sig_ts = pd.Timestamp(sig_ts_str)
        except Exception:
            n_fail += 1
            continue
        if sig_ts.tzinfo is None:
            sig_ts = sig_ts.tz_localize("UTC")

        # signal_ts + horizon이 아직 미래면 skip (충분히 시간 안 지남)
        if sig_ts + pd.Timedelta(hours=horizon_hours) > pd.Timestamp.now(tz="UTC"):
            n_skip += 1
            continue

        # 매수 의사 시점에 OHLCV로 entry_price (signal_ts 봉의 close 근사)
        # 단순화: 매수 시도 시점은 신호 발생 시점이라고 가정 → 직전 봉 close ≈ entry_price
        # 실제 체결가는 다를 수 있지만 outcome 판정에는 충분
        ccxt_sym = _ccxt_symbol(symbol)
        try:
            entry_bars = ex.fetch_ohlcv(ccxt_sym, timeframe="4h",
                                          since=int((sig_ts - pd.Timedelta(hours=4)).timestamp() * 1000),
                                          limit=2)
        except Exception as e:
            log.warning("entry fetch 실패 %s: %s", ccxt_sym, e)
            n_fail += 1
            continue
        if not entry_bars:
            n_fail += 1
            continue
        entry_price = float(entry_bars[-1][4])  # close of last bar at/before signal_ts

        high, n_bars = fetch_high_in_window(ex, ccxt_sym, sig_ts, horizon_hours)
        if high is None:
            n_fail += 1
            continue

        target = entry_price * (1.0 + target_pct + slippage_pct)
        reached = high >= target
        outcome_pct = (high - entry_price) / entry_price

        shadow.log_outcome(
            symbol=symbol, signal_ts=sig_ts,
            outcome_pct=outcome_pct, reached_target=reached,
            horizon_bars_actual=n_bars,
        )
        n_matched += 1
        if reached:
            n_reached += 1

    return {
        "date": date_str,
        "n_decisions": len(decisions),
        "matched": n_matched,
        "reached_target": n_reached,
        "skipped": n_skip,
        "fail": n_fail,
    }


def match_recent_days(days: int = 3) -> list[dict]:
    """최근 N일에 대해 매칭 시도 (이미 매칭된 건은 skip — idempotent)."""
    today_utc = datetime.now(timezone.utc)
    results = []
    for i in range(1, days + 1):
        d = (today_utc - timedelta(days=i)).strftime("%Y%m%d")
        r = match_date(d)
        results.append(r)
        log.info("match_date %s → %s", d, r)
    return results
