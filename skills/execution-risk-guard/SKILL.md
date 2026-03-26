---
name: execution-risk-guard
description: 업비트 BTC/KRW 현물 페이퍼/실전 거래 안전 관리. 주문 전 점검, IP·권한·Rate Limit 확인, 포지션 정합성, 인시던트 처리. Phase 3 이후 사용.
---

# Execution Risk Guard

주문을 실행하기 전에 반드시 통과해야 하는 게이트.

## 업비트 환경 제약

| 항목 | 값 |
| --- | --- |
| Rate Limit (기본) | 29 req/sec — 응답 헤더 `Remaining-Req` 확인 |
| Rate Limit (주문) | 4 req/sec |
| 포지션 | long/flat only — 숏·레버리지 없음 |
| IP 정책 | API 키에 등록된 공인 IP에서만 호출 가능 |

## 거래 시작 전 점검 목록

1. **IP 확인** — 현재 공인 IP가 업비트 API 키 허용 목록에 있는지 확인
2. **권한 확인** — API 키에 `출금하기` 권한이 없어야 함 (있으면 즉시 키 재발급)
3. **시크릿 확인** — `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY` 환경변수 존재
4. **연결 확인** — `get_ticker("BTC/KRW")` 호출 성공 여부
5. **미체결 주문** — 잔여 미체결 주문 없음 확인

## 런타임 점검

- 주문 후 체결 확인 (acknowledgement)
- 체결 수량이 의도한 포지션과 일치하는지 대조
- 일일 손실 한도 초과 시 자동 중단
- 연속 주문 거부 3회 → 알림 발생 후 동결

## 중단 조건 (즉시 실행)

- 시세 데이터 5분 이상 갱신 없음
- 포지션 수량 불일치
- IP 변경으로 인한 401/403 오류
- heartbeat 누락

## 인시던트 처리

- **cancel-and-freeze 우선** — 불확실한 상태에서 추가 주문 금지
- 인시던트 기록: `workspace/runs/{날짜}/incident_log.json`
- 타임스탬프·심볼·오류코드 반드시 포함

## 제약

- `exchange_execution` MCP 미구현 동안 이 스킬은 점검 정책 정의에만 사용
- 실전 거래 전 PM Orchestrator 최종 승인 필수
