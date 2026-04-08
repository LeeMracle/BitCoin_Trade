# jarvis_executor cron 미등록 — BTC 분할매도 2일 중단

- **발생일**: 2026-04-08 (발견)
- **심각도**: HIGH
- **카테고리**: 운영 / 스케줄링

## 증상

2026-04-05 20:07 UTC 이후 `scripts/jarvis_executor.py`가 한 번도 실행되지 않음. 그 사이 BTC 분할매도 TP1 조건(price > SMA20)이 충족되었으나 매도가 자동 발동되지 않아 익절 기회를 놓칠 뻔함. 발견 시점(04-08 03:00 UTC) 수동 실행으로 TP1 30% 매도(0.00055193 BTC) 정상 체결.

## 원인

`jarvis_executor.py` 파일 헤더 주석에는 "cron 예시: 0 * * * *"가 명시되어 있었으나 **실제로 user crontab, root crontab, systemd timer 어디에도 등록되지 않았음**. Stock_Trade 프로젝트의 cron은 정상 등록되어 있어 혼동이 있었던 것으로 추정.

`scripts/pre_deploy_check.py`도 jarvis_executor의 자동화 등록 여부를 검증하지 않음.

## 수정

- [x] user crontab에 매시 정각 실행 등록:
  ```
  0 * * * * cd /home/ubuntu/BitCoin_Trade && .venv/bin/python scripts/jarvis_executor.py >> /var/log/jarvis_executor.log 2>&1
  ```
- [x] 수동 1회 실행으로 TP1 즉시 발동 (주문 ID: bfc3a322-ae86-45ff-b323-ac6a707136d5)
- [ ] `pre_deploy_check.py`에 "jarvis_executor cron 등록 여부" 검증 규칙 추가 (이어지는 작업)

## 검증 규칙

1. `crontab -l | grep jarvis_executor` 결과가 1줄 이상이어야 함
2. `/var/log/jarvis_executor.log` 의 mtime이 최근 2시간 이내여야 함 (매시 정각 실행 전제)
3. `workspace/jarvis_strategies.json`에 `active: true` 전략이 있을 때는 위 1, 2가 강제됨

## 교훈

스크립트 헤더 주석에 "cron 예시"를 적어두는 것만으로는 배포되지 않는다. 자동화가 전제인 스크립트는 (1) 배포 스크립트가 cron/systemd를 등록하거나, (2) pre_deploy_check가 등록 여부를 검증하거나, 둘 중 하나가 반드시 있어야 한다. 특히 BTC 분할매도처럼 조건 충족 시점이 예측 불가능한 전략은 자동화 누락이 곧 기회 손실이다.
