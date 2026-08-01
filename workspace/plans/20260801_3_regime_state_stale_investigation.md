# regime_state.json 갱신 부재 원인 조사

- **작성일(KST)**: 2026-08-01 22:15
- **작성자/세션**: bata-pm (CTO review 후속)
- **예상 소요**: 30~60분
- **관련 이슈/결정문서**: lessons #37 (regime_notify 누락) hotfix 배포 실측 중 발견

## 1. 목표

`workspace/regime_state.json`의 `last_decided_ts=1776471362` = **2026-04-18 09:16 KST**로 **약 4개월 갱신 부재** 상태 확인. 원인 파악 후 근본 수정 + lessons 추가.

## 2. 성공기준 (Acceptance Criteria)

- [ ] AWS 서버 crontab에 `CRON_REGIME` 실등록 여부 확인 (`ssh ... crontab -l | grep regime_check.py`)
- [ ] `/var/log/regime_check.log` 최근 30일 실행 이력 확인 — 매일 09:30 KST 발화 여부
- [ ] 서버 `workspace/regime_state.json`의 실제 `last_decided_ts` 확인 (로컬 파일이 stale인 것과 서버가 stale인 것 구분)
- [ ] 원인이 (a) cron 미등록 (b) 실행 예외 (c) venv 경로 drift 중 어느 것인지 특정
- [ ] 근본 수정 후 다음 09:30 KST 갱신 확인
- [ ] lessons #39 신설 + CLAUDE.md 등재
- [ ] pre_deploy_check 룰 추가 (예: `check_regime_state_freshness` — 26h 미갱신 시 warning)

## 3. 단계

1. AWS 서버 SSH → `crontab -l | grep regime_check.py` 실측
2. `/var/log/regime_check.log`로 최근 실행 이력 확인
3. 서버 `workspace/regime_state.json`의 `last_decided_ts` 확인 + 로컬과 차이 정합
4. `ssh ... cd /home/ubuntu/BitCoin_Trade && PYTHONUTF8=1 PYTHONPATH=/home/ubuntu/BitCoin_Trade .venv/bin/python scripts/regime_check.py --dry` 수동 실행 → 예외 여부 확인
5. 원인 특정 후 수정 (cron 재등록 / 예외 처리 / 경로 갱신)
6. lessons + 룰 등재

## 4. 리스크 & 사전 확인사항

- 원인이 (a) cron 미등록이면 lessons #36 회귀 (Stock_Trade 덮어쓰기 재발 의심) — deploy_to_aws.sh 사후 게이트가 오늘 배포로 통과했으므로 최근 소실 가능성은 낮음
- 원인이 (b) 실행 예외이면 `fetch_btc_closes`의 ccxt 예외 → try/except 없음(regime_check.py:64) → 매일 조용히 실패 가능성. `scripts/regime_check.py`에 상단 try/except + 텔레그램 실패 알림 필요
- 원인이 (c) venv drift이면 lessons #18 회귀 — `.venv` 경로 검증 필요
- 오늘 아침 브리핑에 노출됐다는 사실 자체가 브리핑이 발견 툴로 잘 작동함을 증명 (lessons #38 hotfix 효과)

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [x] 옵션 A — 별도 세션 (다음 세션 이월)
- [ ] 옵션 C — 자동 검증

## 6. 회고 (작업 종료 후 작성)

- **결과**:
- **원인 귀속**:
- **한 줄 회고**:
- **후속 조치**:
