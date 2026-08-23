#!/usr/bin/env python3
"""lessons #45 일회성 보정 — 기존 포지션의 실현손익 backfill + 유령 포지션 청산.

## 왜 필요한가

`realized_pl_krw` 누적은 이번 배포부터 시작된다. 그런데 현재 보유 중인 4개 포지션은
**이미 TP1(일부는 TP2)을 매도한 상태**이고 그 실현분이 state에 없다.
그대로 두면 이들이 트레일링스탑으로 나갈 때

    return_pct = (마지막 매도분 손익) / (최초 진입금 전액)

이 되어 TP에서 확보한 이익이 통째로 누락된다. 즉 보정을 안 하면 새 코드가
구 포지션에 대해서는 **기존보다 더 부정확**해진다.

또한 SPK/KRW는 TP 전 단계 매도를 마쳤는데도 포지션이 제거되지 않은 유령 상태다
(거래소 실보유 0). 슬롯 1칸을 영구 점유하고 `closed_trades`에도 안 남는다.

## 근거 데이터

`journalctl -u btc-trader | grep '부분 익절 체결'` 의 실측 체결 손익(gross).
체결가는 `entry_price + gross_pl / sold_qty` 로 역산한다 — SPK TP2로 검산 완료:
`(27.1 - 24.2) * 1038.84811162 = 3012.66` = daily_pl_state.json 기록과 일치.

## 사용법

    python scripts/backfill_realized_pl.py            # dry-run (기본)
    python scripts/backfill_realized_pl.py --apply    # 실제 반영
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.execution.config import TP_LEVELS  # noqa: E402
from services.execution.position_pnl import (  # noqa: E402
    position_return_pct,
    record_realized,
)

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"

# ── 실측 체결 기록 ─────────────────────────────────────
# (journal 시각 KST, TP 단계(1-based), gross_pl_krw, sold_qty 또는 None)
#   gross_pl_krw: journal `pl=+NNNN` (정수 반올림 — 5만원 기준 오차 ±0.002%p)
#   sold_qty    : None이면 entry_qty * TP_LEVELS[단계-1]["sell_ratio"] 로 산출
OBSERVED_FILLS: dict[str, list[tuple[str, int, float, float | None]]] = {
    "JUP/KRW":   [("2026-08-22 05:08", 1, 1662.0, None)],
    "TAIKO/KRW": [("2026-08-22 05:08", 1, 1426.0, None)],
    "POL/KRW":   [("2026-08-22 11:00", 1, 1334.0, None)],
    "SPK/KRW":   [("2026-08-22 05:08", 1, 1676.0, None),
                  ("2026-08-23 14:03", 2, 3012.66, 1038.84811162)],
}

# 유령 포지션: TP 전 단계 완료 + 거래소 실보유 0 (2026-08-24 확인)
GHOST_SYMBOLS = ["SPK/KRW"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 state 파일에 반영")
    ap.add_argument("--state", default=str(STATE_PATH))
    args = ap.parse_args()

    path = Path(args.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    positions = state.get("positions", {})

    print(f"state: {path}")
    print(f"보유 {len(positions)}종목 / closed_trades {len(state.get('closed_trades', []))}건\n")

    # ── 1. 실현손익 backfill ──
    for symbol, fills in OBSERVED_FILLS.items():
        pos = positions.get(symbol)
        if pos is None:
            print(f"  [skip] {symbol} — 보유 중 아님")
            continue
        if pos.get("realized_pl_krw") is not None:
            print(f"  [skip] {symbol} — 이미 realized_pl_krw 있음 (중복 반영 방지)")
            continue
        entry = float(pos.get("entry_price") or 0)
        entry_qty = float(pos.get("entry_qty") or 0)
        if entry <= 0 or entry_qty <= 0:
            print(f"  [warn] {symbol} — entry_price/entry_qty 없음, 건너뜀")
            continue
        for ts, level, gross_pl, qty in fills:
            if qty is None:
                qty = entry_qty * TP_LEVELS[level - 1]["sell_ratio"]
            exec_price = entry + gross_pl / qty
            net = record_realized(pos, exec_price, qty)
            print(
                f"  [fill] {symbol} TP{level} @{ts}  "
                f"qty={qty:.8f} exec={exec_price:,.4f} gross={gross_pl:+,.0f} net={net:+,.0f}"
            )
        # 교훈 #12: .get default는 값이 None이면 무시된다 — 포매팅 전 float() 고정
        basis = float(pos.get("entry_amount_krw") or 0)
        print(
            f"    → realized_pl_krw={pos['realized_pl_krw']:+,.1f} "
            f"({position_return_pct(pos):+.2f}% of entry {basis:,.0f})\n"
        )

    # ── 2. 유령 포지션 청산 ──
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    for symbol in GHOST_SYMBOLS:
        pos = positions.get(symbol)
        if pos is None:
            print(f"  [skip] {symbol} — 이미 제거됨")
            continue
        sold = set(pos.get("tp_sold_levels", []))
        if len(sold) < len(TP_LEVELS):
            print(f"  [abort] {symbol} — TP 미완료({sorted(sold)}), 유령 아님. 수동 확인 필요")
            return 1
        # 전량 청산이므로 exit_price 는 마지막 TP 체결가
        last_ts, last_level, last_gross, last_qty = OBSERVED_FILLS[symbol][-1]
        if last_qty is None:
            last_qty = float(pos["entry_qty"]) * TP_LEVELS[last_level - 1]["sell_ratio"]
        exit_price = float(pos["entry_price"]) + last_gross / last_qty
        ret_pct = position_return_pct(pos, fallback_price=exit_price)
        state.setdefault("closed_trades", []).append({
            "symbol": symbol,
            "entry_date": pos.get("entry_date", ""),
            "entry_price": pos.get("entry_price", 0),
            "exit_date": now,
            "exit_price": exit_price,
            "return_pct": round(ret_pct, 2),
            "exit_reason": "tp_complete_backfill",
        })
        positions.pop(symbol, None)
        print(
            f"  [close] {symbol} 유령 포지션 청산 — return={ret_pct:+.2f}% "
            f"exit_price={exit_price:,.4f} → 슬롯 반환"
        )

    print(f"\n결과: 보유 {len(positions)}종목 / "
          f"closed_trades {len(state.get('closed_trades', []))}건")

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
