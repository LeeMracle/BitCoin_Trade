"""04-25 거래 빈도 증대 검증 결과 텔레그램 보고."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402

MSG = """📈 *거래 빈도 + 수익 증대 검증* (04-25)

*핵심 발견*
🔍 BTC 단일 백테스트에서 RSI 50→40·Vol 1.5→1.0 6 조합 **모두 동일** (총수익 102%, 거래수 9, Sharpe 1.258)
→ BTC DC20 돌파 180회 중 RSI>50 = **180/180 (100%)** ⇒ *임계값 변경은 BTC에선 무효*

*결론* — 거래 빈도 증대의 본질은 **슬롯 한도**와 **종목 다변화**에 있다

*권장 (사용자 승인 대기)*

1️⃣ *즉시 (1순위)*
- `MAX_POSITIONS 5 → 7` 변경
- ⚠️ 두 파일 동시 수정 필수 (cto HIGH):
  • `services/execution/config.py:56`
  • `services/execution/multi_trader.py:33` (자체 하드코딩!)
- 효과: 동시 매수 신호가 5개 초과하는 날에만 +N건 (평균 보유 2/5라 매일 효과는 아님)
- 위험: 자본 1/7 분할(슬롯당 14%↓), HARD\\_STOP 캡 10%로 7슬롯 동시손절도 -9.5% (CB -20% 안전)

2️⃣ *보류*
- VB LIVE 승격 — BEAR 필터로 어차피 거래 0, BULL 복귀 후 검토

3️⃣ *후속 plan*
- 알트 멀티 백테스트 (1차 top3~5 단일 합산 1~2일, 2차 풀 엔진 1주)
- 알트 환경에서 RSI/Vol 완화 효과 별도 검증

*교차검증* — cto review 조건부 PASS → HIGH 반영 후 PASS
*산출물*
- `workspace/reports/20260425_2_increase_trade_frequency.md`
- `output/entry_relaxation_summary.json`

승인 시 즉시 적용 가능합니다. — Jarvis"""


async def main():
    ok = await send(MSG)
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
