"""04-25 MAX_POSITIONS 5→7 배포 완료 알림."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402

MSG = """🚀 *MAX\\_POSITIONS 5→7 배포 완료* (04-25 16:21 KST)

*변경 파일 (양쪽 동시 수정)*
✅ `services/execution/config.py:56`
✅ `services/execution/multi_trader.py:33` (자체 하드코딩 동기화)

*사후 헬스체크 — 모두 PASS*
• 서비스 active (PID 27464, 16:21:43 active 전환)
• 양쪽 파일 MAX\\_POSITIONS=7 동기화 확인
• 5분간 error 0건
• 메모리 581/911MB (정상)
• RVN/TRX 2종목 보전 → 현재 *2/7*
• v2 필터 정상 (F&G=31 허용, BTC>EMA200=False)

*안전장치*
• HARD\\_STOP 10% 캡 — 7슬롯 동시손절 시 -9.5% (CB -20% 안전)
• 메모리 영향 없음 (감시 풀 동일)
• 현재 BEAR라 신규 매수는 게이트로 차단 — 효과는 BULL 복귀 후 발현

*신규 lesson #19 등재*
• `lessons/20260425_2_config_constant_self_definition.md`
• 핵심: config 상수의 자체 정의 패턴 → 향후 import 통일 권장
• CLAUDE.md 교훈 #19 추가

*모니터링 개시*
• 1주간 거래 빈도·평균 슬리피지·승률 추적
• 동시 보유 신호 5개 초과 발생 시 새 슬롯 활용 확인

— Jarvis"""


async def main():
    ok = await send(MSG)
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
