"""04-25 토 운영 감시 종합 보고 텔레그램 발송."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402

MSG = """🔍 *BATA 운영 감시 종합* (04-25 토 KST)

✅ *정상 (4건)*
• 서버: `btc-trader.service` active, PID 523
• 메모리 576/911Mi, 스왑 64% (가용 334Mi)
• 다중 프로젝트 동거 정상 (BTC/Stock/Blog)
• filter\\_stats W17 정상 누적 (오늘 ema200=62,455)
• 잔고 로그 throttle 회귀 없음 (3일치)
• 스윙 2/5 (RVN, TRX), VB DRY-RUN 0종목

🚨 *위험 — 긴급 조치 2건*

*1) regime\\_state 7일간 stale*
- 04-18 17:25 이후 미갱신
- 원인: crontab `venv/bin/python` 경로 (실제 `.venv/bin/python`)
- `/var/log/regime_check.log` 175줄 전부 No such file
- 차선책: `enabled=false`라 거래 차단 → 즉시 손실 없음
- 교훈 #4 (CLAUDE.md↔config↔서버 동기화) 위반

*2) VB `vb_recheck_trigger` cron 미등록*
- 09:15 KST 자동 실행 누락
- 스크립트는 존재, cron 등록만 누락
- 교훈 #9 (자동화 cron 등록 + pre\\_deploy\\_check) 위반

📊 *P5-02/03 BULL 트리거*
• 현재 BEAR, 최근 5신호 BEAR×5 (히스테리시스 0/5)
• regime stale → 측정 무효, 복구 후 재측정 필요

⏭️ *제안 — 자비스 승인 대기*
1. `crontab -e` venv → .venv (1글자)
2. VB cron 등록 + pre\\_deploy\\_check 검증규칙 추가
3. 두 항목 lessons 신규 기록

— Jarvis"""


async def main():
    ok = await send(MSG)
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
