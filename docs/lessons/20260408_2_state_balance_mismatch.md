# vb_state.json ↔ 거래소 잔고 정합성 깨짐 (가짜 ORDER 포지션)

- **발생일**: 2026-04-08 (발견)
- **심각도**: MEDIUM
- **카테고리**: 코드 / 상태 관리

## 증상

`workspace/vb_state.json`의 `positions` 필드에 `ORDER/KRW` 포지션(entry_price=88, 2026-04-08 00:03)이 기록되어 있었으나, 업비트 `fetch_balance()` 조회 결과 ORDER 코인은 실제로 **보유하지 않음**.

`btc-trader.service` 로그에는 `[서킷브레이커] 발동 중 — ORDER/KRW 매수 차단` 메시지가 반복 출력됨 → 매수 시도는 CB가 차단했으나, **state에는 이미 진입한 것처럼 기록됨**.

## 원인 (추정)

VB 일일 회전 로직이 "신호 발생 → state 먼저 기록 → 주문 전송" 순서로 동작하거나, 주문 실패/차단 시 state 롤백이 없어 불일치 발생. CB 차단이 발생한 시점에 state에만 포지션이 기록된 것으로 보임.

## 수정

- [x] 임시 조치: `vb_state.json`에서 `positions.ORDER/KRW` 제거 (백업: `vb_state.json.bak.20260408`)
- [ ] 근본 수정: `services/execution/multi_trader.py` (또는 VB 진입 경로)에서 주문 **체결 확인 후**에만 state 기록하도록 순서 수정
- [ ] CB 발동 시 차단된 주문은 state에 기록하지 않도록 가드 추가

## 검증 규칙

1. VB/Composite 진입 직후 `state.positions`의 각 심볼이 `fetch_balance()` 결과에 존재하는지 일일 검증
2. 진입 기록과 실제 체결 주문 ID가 매칭되는지 확인 (state에 order_id 저장 필수)
3. CB 차단 로그가 찍힌 심볼은 state에 없어야 함

## 교훈

상태 파일은 "거래소의 미러"여야지 "의도의 기록"이면 안 된다. `state ≠ balance`는 즉시 경보. 교훈 #8(모니터링 평가금액 알트 누락)과 같은 뿌리 — 모든 잔고 관련 수치는 거래소 API가 진실의 원천(Source of Truth).
