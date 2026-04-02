# vol_reversal DRY-RUN 병행 운영 (2026-03-29)

## 현재 운영 체계

### 실전 (composite DC20)
- 전략: composite DC20 (일봉, 실시간 웹소켓)
- 목적: 자본 보전, 하락장 방어
- 예상 빈도: 월 1~2회
- 리스크: 낮음 (거래 빈도 낮음)

### 가상 (vol_reversal DRY-RUN)
- 전략: 거래량 반전 단타 (4시간봉, cron 4시간 스캔)
- 목적: 하락장 전략 실전 검증
- 예상 빈도: 월 5회
- 리스크: 0 (가상 매매)

## vol_reversal 전략 상세

- 진입: 거래량 3배 급증 + RSI(14)<35 + 양봉 + 이전봉 음봉
- 청산: +3% 익절 / 1.5% 트레일링 / -2% 손절 / 32시간 제한
- 하락장 6개월 백테스트: 31건, 71% 승률, +1.78% 평균, MDD -5.2%

## QA 개선

- scripts/qa_validate.py 도입 (배포 전 자동 검증)
- 레벨 계산 lookback 부족 버그 수정
- 전략 전환 시 이전 거래 기록 분리

## 검증 플랜

vol_reversal DRY-RUN 기준:
- 3일: 방향 확인
- 5일: 10건+ 시 1차 판단
- 7일: 최종 — 승률 50%+, 평균 +1%+면 실전 전환

## 파일

- 전략: services/strategies/advanced.py → make_strategy_vol_reversal()
- DRY-RUN: scripts/dryrun_vol_reversal.py
- 상태: workspace/vol_reversal_dryrun_state.json
- cron: 매 4시간 05분 (UTC 0/4/8/12/16/20)
- 로그: /var/log/vol_reversal_dryrun.log
