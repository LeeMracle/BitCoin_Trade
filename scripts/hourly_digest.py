#!/usr/bin/env python3
"""매시 통합 다이제스트 — jarvis 결과 + regime 변화 + critical 상태 1건 (skeleton).

plan 20260503 P3-4 (cto 우려로 cron 미등록 상태):
  - 본 스크립트는 실행 가능하지만 cron 등록 안 됨
  - 향후 jarvis(매시 정각) + regime(매시 25분) cron과 중복 발송 방지 로직
    설계 후 별도 plan(20260504_2 가칭)에서 통합 마이그레이션 진행
  - 현재는 skeleton만 — `python scripts/hourly_digest.py --dry-run`으로 메시지 미리보기

향후 통합 시 필요한 추가 작업:
  1. jarvis_executor 매수/매도 결과 캐싱 (workspace/jarvis_last_report.json)
  2. regime_check 마지막 판정 캐싱 (workspace/regime_state.json은 이미 존재)
  3. 동일 거래 중복 보고 회피 로직 (last_reported_ts 비교)
  4. jarvis/regime cron 비활성화 + hourly_digest cron 등록 (deploy_to_aws.sh)

사용:
  python scripts/hourly_digest.py --dry-run    # 메시지 미리보기 (텔레그램 발송 안 함)
  python scripts/hourly_digest.py              # 실제 발송 (현재 cron 미등록 상태)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KST = timezone(timedelta(hours=9))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_digest() -> str:
    """jarvis + regime + critical 상태 통합 메시지."""
    now_kst = datetime.now(tz=KST)
    lines = [f"매시 다이제스트 ({now_kst:%H:%M KST})"]

    # 1. 자비스 매시 매매 (workspace/jarvis_log.jsonl 마지막 1h)
    jarvis_log = ROOT / "workspace" / "jarvis_log.jsonl"
    recent_trades = []
    if jarvis_log.exists():
        cutoff = now_kst - timedelta(hours=1)
        try:
            with jarvis_log.open("r", encoding="utf-8") as f:
                lines_log = f.readlines()
            for raw in lines_log[-100:]:
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                ts_str = entry.get("logged_at") or entry.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=KST)
                    if ts >= cutoff and not entry.get("error"):
                        recent_trades.append(entry)
                except Exception:
                    continue
        except Exception:
            pass

    if recent_trades:
        lines.append(f"\n[자비스] 1h {len(recent_trades)}건")
        for t in recent_trades[-3:]:
            sym = t.get("symbol", "?")
            sid = t.get("step_id", "?")
            mode = t.get("mode", "?")
            lines.append(f"  {sym} {sid} ({mode})")
    else:
        lines.append("\n[자비스] 1h 매매 없음")

    # 2. 레짐 상태 (workspace/regime_state.json)
    regime = _load_json(ROOT / "workspace" / "regime_state.json")
    cur = regime.get("current", "UNKNOWN")
    lines.append(f"\n[레짐] {cur}")
    history = regime.get("history", [])
    if history:
        last_change = history[-1]
        lines.append(f"  마지막 전환: {last_change}")

    # 3. critical 상태 요약 (인증·잔고·jarvis cron)
    try:
        from services.healthcheck.runner import (
            check_auth, check_balance_fetch, check_jarvis_cron,
        )
        chk_results = [check_auth(), check_balance_fetch(), check_jarvis_cron(window_hours=2)]
        fails = [r for r in chk_results if r["status"] == "FAIL"]
        warns = [r for r in chk_results if r["status"] == "WARN"]
        if fails:
            lines.append(f"\n[critical] FAIL {len(fails)}건")
            for r in fails:
                lines.append(f"  ❌ {r['name']}: {r['detail']}")
        elif warns:
            lines.append(f"\n[critical] WARN {len(warns)}건, FAIL 0")
        else:
            lines.append(f"\n[critical] 모두 OK")
    except Exception as e:
        lines.append(f"\n[critical] 체크 실패: {type(e).__name__}")

    return "\n".join(lines)


_HEARTBEAT = Path("/tmp/bata_hourly_digest_heartbeat")
_REGIME_LAST_REPORTED = Path("/tmp/bata_hourly_digest_regime_last")


def _should_send(msg: str) -> tuple[bool, str]:
    """침묵 모드 판정 (cto P4 review #1 — 진짜 critical만 발송).

    발송 조건 OR:
      1. critical FAIL 1건 이상
      2. 직전 1h 매매 5건 이상 (요약 가치)
      3. regime 전환 발생 (이전 보고와 다름)
    """
    reasons = []
    if "[critical] FAIL" in msg:
        reasons.append("critical_fail")

    # 매매 N건 추출
    import re
    m = re.search(r"\[자비스\] 1h (\d+)건", msg)
    if m and int(m.group(1)) >= 5:
        reasons.append(f"trades_{m.group(1)}")

    # regime 전환 감지
    m = re.search(r"\[레짐\] (\w+)", msg)
    if m:
        cur = m.group(1)
        prev = ""
        if _REGIME_LAST_REPORTED.exists():
            try:
                prev = _REGIME_LAST_REPORTED.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        if prev and prev != cur:
            reasons.append(f"regime_{prev}->{cur}")
        _REGIME_LAST_REPORTED.write_text(cur, encoding="utf-8")

    return (bool(reasons), ",".join(reasons))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="메시지만 출력, 텔레그램 발송 안 함")
    ap.add_argument("--force", action="store_true",
                    help="침묵 모드 무시하고 강제 발송")
    args = ap.parse_args()

    # heartbeat (cto P4 review #1) — 침묵 여부 무관, digest 자체 죽음 감지
    try:
        _HEARTBEAT.touch()
    except Exception:
        pass

    msg = build_digest()
    print(msg)

    if args.dry_run:
        print("\n[dry-run] 텔레그램 발송 스킵")
        return 0

    should, reason = _should_send(msg)
    if not should and not args.force:
        print(f"\n[침묵] 발송 조건 미충족 — heartbeat만 갱신")
        return 0
    print(f"\n[발송] 사유: {reason or 'force'}")

    from services.alerting.notifier import send_report
    ok = await send_report(msg, parse_mode=None)
    print(f"발송 결과: {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
