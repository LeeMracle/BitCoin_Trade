"""검증 기준선 리셋 (ADR 20260823-1, P1).

무엇을 하는가:
    multi_trading_state.json 의 검증 창을 새 기준일로 옮긴다.
      strategy_start          → 새 기준일 (이후 청산 거래만 성과 집계)
      consec_loss_floor_date  → 새 기준일 (연패 산정도 같은 창으로 정렬)
      regime_open_days        → 0 (거래 가능일 카운터 초기화)

    closed_trades 는 **삭제하지 않는다**. 창 밖으로 밀어낼 뿐이며 이력은 보존된다.

왜 필요한가:
    2026-08-22 이전 통계는 두 가지 이유로 신뢰할 수 없다.
      (1) 실행 버그 4건 (lessons #40~#43) — 매수 100% 차단, 손절 미평가,
          트레일스탑 미저장, 체결가 대신 신호가 기록.
          "승률 19%"는 전략 성적이 아니라 버그 성적이다.
      (2) 그 기간 전략 파라미터가 반복 변경되어 서로 다른 전략의 성적을 합산.
          DC 50→20→15→10→15→12 / MIN_VOLUME 5억→3억→5억 /
          레짐필터 OFF→ON / VOL 1.0→1.5 / ATR 0.10→0.07

기준일을 2026-08-23 으로 잡은 이유:
    마지막 매매 로직 수정(lessons #43 체결가 확정)이 2026-08-22 06:47 UTC 배포.
    그 이후 청산된 거래는 없으므로 08-23 이 깨끗한 절단점이다.
    (08-22 로 잡으면 버그 시절 XRP/SUI -10% 2건이 새 창에 섞인다)

주의 (문서화된 한계):
    현재 보유 5종목은 기준일 **이전에 진입**했다. 진입가는 fix_entry_price_from_fills.py
    로 실체결가 보정을 마쳤지만, 진입 *선택* 자체는 버그 시절 필터를 통과한 것이다.
    따라서 새 창의 첫 5건은 이 오염을 안고 있다 — 판정 시 감안해야 한다.

사용:
    python scripts/reset_validation_baseline.py                 # 미리보기
    python scripts/reset_validation_baseline.py --apply         # 실제 적용
    python scripts/reset_validation_baseline.py --date 2026-08-23 --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"
DEFAULT_DATE = "2026-08-23"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=DEFAULT_DATE, help="새 기준일 (YYYY-MM-DD)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"날짜 형식 오류: {args.date} (YYYY-MM-DD)")
        return 1

    if not STATE_PATH.exists():
        print(f"상태 파일 없음: {STATE_PATH}")
        return 1

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    closed = state.get("closed_trades", [])

    old_start = state.get("strategy_start")
    old_floor = state.get("consec_loss_floor_date")
    old_open = state.get("regime_open_days", 0)

    in_old = [t for t in closed if str(t.get("exit_date", "")) >= str(old_start or "")]
    in_new = [t for t in closed if str(t.get("exit_date", "")) >= args.date]

    def _summary(ts: list) -> str:
        if not ts:
            return "0건"
        wins = sum(1 for t in ts if (t.get("return_pct") or 0) > 0)
        avg = sum((t.get("return_pct") or 0) for t in ts) / len(ts)
        return f"{len(ts)}건 승률 {wins/len(ts)*100:.0f}% 평균 {avg:+.2f}%"

    print("=" * 62)
    print("검증 기준선 리셋")
    print("=" * 62)
    print(f"  strategy_start         : {old_start} → {args.date}")
    print(f"  consec_loss_floor_date : {old_floor} → {args.date}")
    print(f"  regime_open_days       : {old_open} → 0")
    print()
    print(f"  기존 창 성과 : {_summary(in_old)}")
    print(f"  새 창 성과   : {_summary(in_new)}")
    print(f"  closed_trades: {len(closed)}건 보존 (삭제 없음)")

    dropped = [t for t in in_old if t not in in_new]
    if dropped:
        print(f"\n  창 밖으로 밀려나는 거래 {len(dropped)}건 (이력은 유지):")
        for t in dropped[-5:]:
            print(f"    {t.get('symbol','?'):<11} {t.get('exit_date')} "
                  f"{(t.get('return_pct') or 0):+.2f}%")
        if len(dropped) > 5:
            print(f"    ... 외 {len(dropped)-5}건")

    if not args.apply:
        print("\n[DRY-RUN] 실제 적용: --apply")
        return 0

    backup = STATE_PATH.with_suffix(
        f".json.bak_baseline_{datetime.now(tz=timezone.utc):%Y%m%d_%H%M%S}"
    )
    shutil.copy(STATE_PATH, backup)

    state["strategy_start"] = args.date
    state["consec_loss_floor_date"] = args.date
    state["regime_open_days"] = 0
    state.pop("regime_open_last_date", None)
    state["baseline_reset_note"] = (
        f"ADR 20260823-1: lessons #40~#43 실행버그 + 파라미터 반복변경으로 "
        f"{old_start} 창 폐기. 이력은 closed_trades에 보존."
    )
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n적용 완료. 백업: {backup.name}")
    print("봇 재시작 후 반영됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
