#!/usr/bin/env python3
"""Critical 헬스체크 — 매시 5분 cron, 인증·jarvis cron 항목만 점검.

실패(FAIL) 1건이라도 발생하면 즉시 텔레그램 경보.
30분 디바운스: /tmp/bata_critical_alert_flag로 중복 알람 차단.

사용:
    python scripts/critical_healthcheck.py            # cron 모드 (실패 시만 알람)
    python scripts/critical_healthcheck.py --force    # 디바운스 무시, 강제 알람

cron 등록:
    5 * * * * cd /home/ubuntu/BitCoin_Trade && \
      PYTHONUTF8=1 /home/ubuntu/BitCoin_Trade/.venv/bin/python \
      scripts/critical_healthcheck.py >> /var/log/critical_healthcheck.log 2>&1

배경: 2026-05-01 23:00 KST 인증실패 8h 무감지 사고 재발 방지.
관련 plan: workspace/plans/20260502_reporting_system_overhaul.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.healthcheck.runner import (  # noqa: E402
    check_auth, check_balance_fetch, check_jarvis_cron, FAIL, WARN, OK,
)

KST = timezone(timedelta(hours=9))
ALERT_FLAG = Path("/tmp/bata_critical_alert_flag")
DEBOUNCE_SEC = 1800  # 30분


def _send_telegram(msg: str) -> bool:
    """plan 20260503 P3-3: send_critical 사용 (실패 시 journalctl 자동 기록).

    cron 환경에서 sync 호출 위해 asyncio.run으로 감싼다.
    """
    import asyncio
    try:
        from services.alerting.notifier import send_critical
        return asyncio.run(send_critical(msg))
    except Exception as e:
        print(f"[critical] send_critical 실패 (fallback urllib): {e}", flush=True)
        # fallback: urllib 직접 호출 (notifier 자체 import 실패 등 극단 케이스)
        from dotenv import load_dotenv
        load_dotenv(ROOT / "services" / ".env")
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("[critical] TELEGRAM 환경변수 미설정 — 발송 스킵", flush=True)
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": "🚨 " + msg}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status == 200
        except Exception as ex:
            print(f"[critical] fallback 발송도 실패: {ex}", flush=True)
            return False


def _is_debounced() -> tuple[bool, int]:
    """30분 내 이미 알람 발송했으면 True 반환."""
    if not ALERT_FLAG.exists():
        return False, 0
    age = int(time.time() - ALERT_FLAG.stat().st_mtime)
    return age < DEBOUNCE_SEC, age


def _set_debounce_flag():
    ALERT_FLAG.touch()


def _clear_debounce_flag():
    """모두 정상 복구 시 디바운스 해제 → 다음 실패 시 즉시 알람."""
    if ALERT_FLAG.exists():
        ALERT_FLAG.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="디바운스 무시하고 강제 알람")
    ap.add_argument("--dry-run", action="store_true",
                    help="알람 발송 안 함 (로컬 검증용)")
    args = ap.parse_args()

    now_str = datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M KST")
    print(f"[critical] {now_str} 점검 시작", flush=True)

    auth = check_auth()
    # plan 20260503 P0 (AC9): 잔고 조회 별도 — 인증 OK + 잔고만 429 케이스 탐지
    balance = check_balance_fetch()
    jarvis = check_jarvis_cron(window_hours=2)
    results = [auth, balance, jarvis]

    # 결과 요약
    for r in results:
        print(f"  [{r['status']}] {r['name']}: {r['detail']}", flush=True)

    # 실패 항목만 추출
    fails = [r for r in results if r["status"] == FAIL]

    if not fails:
        # 모두 OK/WARN — 알람 없음, 디바운스 해제
        _clear_debounce_flag()
        print("[critical] 정상 (FAIL 0건) — 알람 없음", flush=True)
        return 0

    # FAIL 1건 이상 — 디바운스 체크
    debounced, age = _is_debounced()
    if debounced and not args.force:
        print(f"[critical] FAIL {len(fails)}건이지만 디바운스({age}s < {DEBOUNCE_SEC}s) — 알람 스킵",
              flush=True)
        return 0

    # 알람 발송
    fail_lines = [f"  ❌ {r['name']}: {r['detail']}" for r in fails]
    msg = (
        f"🚨 [BATA Critical] {now_str}\n\n"
        f"FAIL {len(fails)}건 감지 — 즉시 조치 필요\n\n"
        + "\n".join(fail_lines)
        + "\n\n자세한 내역: 18:00 KST 일일보고 또는\n"
        + "scripts/critical_healthcheck.py --force 재실행"
    )
    if args.dry_run:
        print("[critical] --dry-run: 알람 발송 스킵", flush=True)
        print(msg, flush=True)
        return 0
    ok = _send_telegram(msg)
    if ok:
        _set_debounce_flag()
        print(f"[critical] 알람 발송 완료 — 디바운스 30분 시작", flush=True)
        return 2  # FAIL exit code
    else:
        print("[critical] 알람 발송 실패 — 디바운스 미적용 (다음 회차 재시도)", flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
