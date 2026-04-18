#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""레짐 자동 판정 스크립트 (P5-04 DRY-RUN 훅).

동작:
  - BTC 일봉으로 EMA200/SMA50 계산 + F&G 조회
  - services.execution.regime_switcher.decide_regime 호출
  - update_with_decision으로 workspace/regime_state.json 갱신
  - 전환 발생(should_notify) 시 텔레그램 알림 (판정만, 실거래 변경 없음)

실거래 스위칭은 REGIME_SWITCH_ENABLED=True일 때만 활성화 (현재 False).

사용:
  python scripts/regime_check.py            # 판정 + state 갱신 (알림 없음)
  python scripts/regime_check.py --notify   # 전환 시 텔레그램 알림까지
  python scripts/regime_check.py --dry      # 상태 미저장 (리허설)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import ccxt
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from services.execution.regime_switcher import (  # noqa: E402
    decide_regime,
    load_state,
    save_state,
    update_with_decision,
    should_notify,
    format_notification,
)

_FG_API = "https://api.alternative.me/fng/?limit=1"


def fetch_btc_closes(limit: int = 260) -> list[float]:
    """BTC/KRW 일봉 close 시계열."""
    ex = ccxt.upbit()
    ohlcv = ex.fetch_ohlcv("BTC/KRW", timeframe="1d", limit=limit)
    return [float(c[4]) for c in ohlcv]


def fetch_fg() -> int:
    """Fear&Greed 지수 현재값(1~100)."""
    import urllib.request
    import json
    try:
        with urllib.request.urlopen(_FG_API, timeout=10) as r:
            data = json.loads(r.read().decode())
        return int(data["data"][0]["value"])
    except Exception:
        return 50  # 조회 실패 시 중립


def run(notify: bool = False, dry: bool = False) -> dict:
    closes = fetch_btc_closes()
    s = pd.Series(closes, dtype="float64")
    ema200 = float(s.ewm(span=200, adjust=False).mean().iloc[-1])
    sma50 = float(s.rolling(50).mean().iloc[-1])
    btc_close = float(closes[-1])
    fg = fetch_fg()

    decision = decide_regime(btc_close, sma50, ema200, fg)
    print(f"[레짐] BTC={btc_close:,.0f} EMA200={ema200:,.0f} SMA50={sma50:,.0f} "
          f"F&G={fg} → {decision.regime.value} ({decision.reason})", flush=True)

    prev = load_state()
    new_state = update_with_decision(decision) if not dry else prev

    if not dry:
        save_state(new_state)

    if notify and should_notify(prev, new_state):
        msg = format_notification(prev.get("current", "UNKNOWN"),
                                  new_state.get("current", "UNKNOWN"),
                                  decision.reason)
        try:
            from services.execution.telegram_bot import send_message
            asyncio.run(send_message(msg))
            print(f"[레짐] 전환 알림 발송: {msg[:80]}", flush=True)
        except Exception as e:
            print(f"[레짐] 알림 실패: {e}", flush=True)

    return new_state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    run(notify=args.notify, dry=args.dry)


if __name__ == "__main__":
    main()
