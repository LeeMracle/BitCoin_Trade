# 20260329_3: 하이브리드 실행 — 진입 4시간, 청산 실시간

## 결정
vol_reversal 전략의 실행 방식을 하이브리드로 구현:
- **진입**: 4시간 주기 스캔 (봉 마감 확인 후 — 가짜 돌파 방지)
- **청산**: 실시간 웹소켓 (손절 지연 방지)

## 배경
daytrading 실전 실패 원인 = 실시간 틱 진입으로 가짜 돌파에 속음.
그러나 청산은 빨라야 함 — 4시간 후 확인하면 -2% 손절이 -5%가 될 수 있음.

## 구현 방식

### 기존 구조
```
composite (실전) → realtime_monitor 웹소켓
vol_reversal (DRY-RUN) → cron 4시간 스캔 (별도)
```

### 변경 구조
```
realtime_monitor 웹소켓:
  ├── composite 진입/청산 (기존)
  └── vol_reversal 보유종목 청산 감시 (신규)
      - TP +3%, Trail 1.5%, SL -2%

vol_reversal 진입:
  └── cron 4시간 스캔 (봉 마감 후 진입, 기존 유지)
```

### 핵심 변경
- `realtime_monitor._handle_tick()`에서 vol_reversal 보유 종목도 청산 감시
- vol_reversal 보유 종목을 웹소켓 구독 목록에 추가
- `vol_reversal_dryrun_state.json`을 모니터가 읽어서 청산 판단

## QA 체크리스트
- [ ] composite 매매에 영향 없음
- [ ] vol_reversal 진입은 4시간 cron에서만 발생
- [ ] vol_reversal 청산은 웹소켓에서 실시간 처리
- [ ] 두 전략의 state 파일이 분리되어 간섭 없음

## 리스크
- vol_reversal이 DRY-RUN이므로 실제 청산은 발생하지 않음
- 실전 전환 시 이 구조를 그대로 사용 가능
