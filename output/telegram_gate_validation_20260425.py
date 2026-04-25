"""04-25 토 신규매수 게이트 백테스트 검증 결과 텔레그램 보고."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402

MSG = """🔬 *신규매수 게이트 백테스트 검증* (04-25)

*배경*: BTC<EMA200 차단 기준의 합리성 의문에 대한 6 시나리오 동일조건 비교

*OOS 결과 (2024-01 ~ 2026-04)*
| 시나리오 | OPEN | Sharpe | Calmar |
|---|---|---|---|
| **S1 EMA200 (현행)** | 60.5% | **1.258** | **3.867** |
| S2 EMA150 | 56.7% | 1.202 | 3.275 |
| S3 SMA50 | 52.5% | 1.113 | 3.325 |
| S4 OFF | 100% | 1.170 | 3.641 |
| S5 OR\\_FG40 (완화) | 61.0% | 1.129 | 3.298 |
| S6 3DAY\\_CONSEC (강화) | 52.0% | 1.171 | 3.142 |

*결론* — **현행 EMA200 유지가 OOS 단독 1위**
• Sharpe·Calmar 모두 6개 중 최상위
• EMA200 미만 진입은 평균 −0.9~−7.4% 손실 (S3/S4/S5 BEAR-OOS)
• 임계값 변경(EMA150/SMA50) 모두 열등
• 완화(OR) 시 단발 −7.44% 손실로 Sharpe 하락
• 강화(3일 연속) 효과 미미

*한계 (cto review 반영)*
• OOS 거래수 9~12로 통계 유의성 미입증 → 방향성 결론
• 단일 BTC 자산 — 멀티 알트는 별도 검증 필요
• F&G 결합·BULL/BEAR 비대칭 시뮬은 후속 과제

*권장*
- 게이트 코드·파라미터 그대로 유지
- 후속 검증: 멀티 알트 + F&G 결합 (W18 후보)

*산출물*
- `workspace/reports/20260425_buy_gate_threshold_validation.md`
- `output/buy_gate_validation_summary.json`
- `scripts/backtest_buy_gate_validation.py`

— Jarvis"""


async def main():
    ok = await send(MSG)
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
