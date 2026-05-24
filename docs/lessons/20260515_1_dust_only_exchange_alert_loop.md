# lessons #29 — state↔exchange dust 잔고 알람 무한 루프

- **날짜**: 2026-05-15 (KST)
- **참조**: lessons #28 (state↔balance mirror), 5/14 세션 로그(TAO/RVN dust)

## 현상

5/15 텔레그램에 동일 알람 3시간 간격 반복:

```
⚠️ State ↔ Exchange 불일치 (3회 연속 확인)
거래소에만 존재: RVN/KRW, TAO/KRW
자동 보정 없음 — 수동 확인 필요 (state 32405s 전 갱신, 3회 연속)
```

01:32 / 04:32 / 07:32 — 9시간 동안 동일 메시지. 디바운스(3회 연속) 의도가 작동하지만 차집합 자체가 영구 유지라 매번 N회 누적 → 발사 → 재누적 무한 반복.

## 근본 원인

1. 5/14 세션에서 RVN/TAO를 `multi_trading_state.json` `positions`에서 제거 (의미 없는 dust 정리)
2. **거래소 잔고는 그대로** — RVN 7,472원 / TAO 9,164원 (업비트 최소주문 미달분 또는 수동 매도 보류)
3. `realtime_monitor._hourly_sync()`의 dust 임계가 **5,000원**(`if alt_coins[c] * price > 5000`)이라 7~9천원 dust가 `exchange_coins`에 진입
4. state에는 없고 거래소엔 있으니 매 cycle `only_exchange = {RVN, TAO}` 차집합 발생 → 3회 누적 → 알람
5. 사용자가 거래소에서 dust를 매도하지 않는 한 영구 알람

## 핵심 교훈

- **"수동 매도 외 처리 불가한 잔여 dust"에 대한 알람 정책 부재** — lessons #28("state ↔ balance 불일치 즉시 자동 정리")의 반대 케이스. 봇은 이미 정리(state)했으나 거래소가 따라오지 않은 비대칭 상태.
- **임계값 단일 게이트(5천원)는 정책 없음** — "이 종목이 봇 관리 대상인가?"라는 컨텍스트 없이 가치만 봐서 false alarm 양산.
- **알람 디바운스(3회 연속)만으로는 영구 차집합 문제 해결 불가** — 차집합이 사라지지 않으면 디바운스도 매번 채워짐. 차집합 자체를 정제해야 함.

## 수정

### 코드 변경
- `services/execution/config.py`: `DUST_IGNORE_THRESHOLD_KRW = 50_000` 추가 (5만원 미만)
- `services/execution/realtime_monitor.py:_hourly_sync()`:
  - `exchange_value` 딕셔너리 추가 (종목별 평가액 기록)
  - 차집합 계산 직후 `only_exchange` 중 가치 < `DUST_IGNORE_THRESHOLD_KRW`인 종목은 분리 → 알람 silence + 콘솔 로그만
  - reload 차집합 경로(`_check_circuit_breaker_periodic` 동선)에도 동일 dust 필터 적용
- `only_state`(state에 있는데 잔고 없음)는 매도 누락 의심이라 임계 적용 **안 함** — 진짜 사고 케이스 알람 유지

### 정책
| 케이스 | 처리 |
|--------|------|
| only_exchange + 가치 < 5만원 + state에 없음 | dust 무시 (콘솔 로그만) |
| only_exchange + 가치 ≥ 5만원 | 기존 알람 (정말 봇 모르는 자금) |
| only_state | 기존 알람 (매도 누락/주문 실패 의심) |

## 검증규칙 (pre_deploy_check 추가 후보)

- `realtime_monitor.py`에 `DUST_IGNORE_THRESHOLD_KRW` import + dust 필터 코드 존재 확인
- `config.py`에 `DUST_IGNORE_THRESHOLD_KRW` 상수 존재 + 0 < 값 < `MIN_ORDER_KRW * 20` 범위
- 테스트: dust(<5만원, state에 없음) 거래소 보유 시 9시간 후에도 동일 알람 발사 안 됨

## 사용자 액션 (선택)

- **즉시**: 업비트 앱에서 RVN/TAO를 BTC로 교환(작은 수량) 또는 강제매도 — dust 자체 제거가 가장 깔끔
- **선택**: dust 자동 매도 정책은 도입 안 함 (오작동 시 손실, 이번 사고와 동급의 위험)

## 관련 lessons

- #6 매수 경로 누락 — "모든 경로 grep" 원칙은 알람 경로에도 적용 필요
- #10 state↔balance mirror — 일관성 원칙은 유지하되 dust 예외 명시
- #28 자동 정리 — 잔고 0 dust는 자동 정리 가능, 잔고 dust는 자동 매도 위험으로 silence만
