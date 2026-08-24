#!/usr/bin/env python3
"""실현손익을 거래소 체결 이력 기준으로 정합화한다 (lessons #45 후속).

## 왜 필요한가

`backfill_realized_pl.py`(2026-08-24)는 **journal 로그의 `pl=+NNNN`** 을 근거로
기존 포지션의 실현손익을 채웠다. 그런데 그 값들 자체가 오염돼 있었다 —
08-22 05:08 UTC 의 TP 체결은 **lessons #43 수정(06:47 UTC 배포) 이전**이라
봇이 체결가가 아니라 **신호가**로 손익을 계산해 출력했기 때문이다.

거래소 `fetch_order(uuid)` 실측 대조:

| 종목 | journal 기준 | 거래소 실체결 | 차이 |
|---|---:|---:|---:|
| SPK (마감) | +9.18% | **+8.77%** | -0.41%p |
| JUP | 1,636.2 | **1,485.5** | -150.7 |
| TAIKO | 1,399.6 | **924.1** | -475.5 |
| POL | 1,305.7 | 1,305.4 | -0.3 |
| STX | 1,455.9 | 1,455.9 | 0 (#43 수정 이후 체결) |

TAIKO 는 35% 과대계상이었다. 실제 TP1 체결가가 113.0 으로, 트리거(114.45)보다
낮게 체결됐다(얇은 호가창에서 시장가 매도가 호가를 훑고 내려간 것).

## 무엇을 하는가

1. **보유 포지션**: `realized_pl_krw` / `realized_qty` 를 거래소 체결 이력으로 재계산
2. **`closed_trades`**: 추정 기반으로 기록된 항목(`exit_reason` 이 `*_backfill`)의
   `return_pct` 를 재계산

절대값 재계산이므로 **여러 번 돌려도 안전**하다(증분 누적이 아님).

## 사용법

    python scripts/reconcile_realized_pnl.py            # dry-run
    python scripts/reconcile_realized_pnl.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.execution.position_pnl import (  # noqa: E402
    position_return_pct,
    rebuild_realized_from_exchange,
)
from services.execution.upbit_client import _create_exchange  # noqa: E402

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"

# 추정 근거로 기록돼 재계산 대상인 exit_reason
ESTIMATED_REASONS = {"tp_complete_backfill"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 state 파일에 반영")
    ap.add_argument("--state", default=str(STATE_PATH))
    args = ap.parse_args()

    path = Path(args.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    positions = state.get("positions", {})
    closed = state.get("closed_trades", [])
    ex = _create_exchange()

    print(f"state: {path}")
    print(f"보유 {len(positions)}종목 / closed_trades {len(closed)}건\n")

    changed = 0

    # ── 1. 보유 포지션 ──
    print("── 보유 포지션 ──")
    for symbol, pos in positions.items():
        before = pos.get("realized_pl_krw")
        try:
            rebuilt = rebuild_realized_from_exchange(ex, symbol, pos)
        except Exception as e:
            print(f"  [skip] {symbol} — 조회 오류: {e}")
            continue
        if not rebuilt:
            print(f"  [skip] {symbol} — 매도 체결 이력 없음 (TP 미발동)")
            continue
        after = rebuilt["realized_pl_krw"]
        delta = after - (before or 0.0)
        mark = "=" if abs(delta) < 0.5 else "→"
        print(
            f"  {mark} {symbol:11} {('없음' if before is None else f'{before:>10,.1f}')} "
            f"{mark} {after:>10,.1f}  (차이 {delta:+,.1f}, 체결 {rebuilt['n_orders']}건)"
        )
        if abs(delta) >= 0.5 or before is None:
            pos["realized_pl_krw"] = after
            pos["realized_qty"] = rebuilt["realized_qty"]
            changed += 1

    # ── 2. 추정 기반 closed_trades ──
    print("\n── closed_trades (추정 기반 항목) ──")
    targets = [t for t in closed if t.get("exit_reason") in ESTIMATED_REASONS]
    if not targets:
        print("  대상 없음")
    for t in targets:
        symbol = t.get("symbol", "")
        entry_price = float(t.get("entry_price") or 0)
        probe = {"entry_price": entry_price, "entry_date": t.get("entry_date", "")}
        try:
            rebuilt = rebuild_realized_from_exchange(ex, symbol, probe)
        except Exception as e:
            print(f"  [skip] {symbol} — 조회 오류: {e}")
            continue
        if not rebuilt:
            print(f"  [skip] {symbol} — 매도 체결 이력 없음")
            continue
        # 전량 청산된 거래이므로 진입 원금 = 진입가 x 총 매도수량
        probe["entry_amount_krw"] = entry_price * rebuilt["realized_qty"]
        probe["realized_pl_krw"] = rebuilt["realized_pl_krw"]
        new_ret = round(position_return_pct(probe), 2)
        old_ret = t.get("return_pct")
        if abs(new_ret - float(old_ret or 0)) < 0.005:
            print(f"  = {symbol:11} {old_ret:+.2f}% (변화 없음)")
            continue
        print(
            f"  → {symbol:11} {old_ret:+.2f}% → {new_ret:+.2f}%  "
            f"(exit {t.get('exit_price')} → {rebuilt['last_exec_price']:,.4g}, "
            f"체결 {rebuilt['n_orders']}건)"
        )
        t["return_pct"] = new_ret
        t["exit_price"] = rebuilt["last_exec_price"]
        t["exit_reason"] = "tp_complete"  # 추정 딱지 제거 — 이제 거래소 실측이다
        changed += 1

    print(f"\n변경 대상 {changed}건")
    if not changed:
        print("정합 상태 — 반영할 것 없음")
        return 0
    if not args.apply:
        print("\n[dry-run] --apply 를 붙이면 실제 반영합니다.")
        return 0

    backup = path.with_suffix(path.suffix + f".bak_{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[적용 완료] 백업: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
