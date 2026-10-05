# 서버 ML 모듈 동기화 (services/ml/config.py)

- **작성일(KST)**: 2026-10-05 11:30
- **작성자/세션**: 에이전트(Claude) + 사용자 지시("서버 ML 모듈도 최신으로 배포해줘")
- **예상 소요**: 20분
- **관련 이슈/결정문서**: [plan 20261005_1](20261005_1_hard_stop6_grandfather_deploy.md) 잔여 이슈 · 커밋 b3b09f7 · lessons #31, #33

## 1. 목표

서버에 미배포였던 ML 변경을 반영해 서버 `pre_deploy_check` 의 ML-count 오류 3건을 해소한다.
서버·로컬 전수 비교(줄바꿈 정규화) 결과 **내용이 다른 파일은 `services/ml/config.py` 1개(+20줄, 상수 3개 추가)** 뿐이다.

## 2. 성공기준 (Acceptance Criteria)

- [ ] 서버 `services/ml/config.py` 가 로컬(LF 정규화)과 md5 일치
- [ ] 서버 `pre_deploy_check` 오류 0건 (ML-count 3건 해소, 새 오류 없음)
- [ ] `btc-trader` 무중단 (`NRestarts` 불변, 재시작 하지 않음) — 이 상수는 서비스 코드가 import 하지 않는다
- [x] 서버 전체 코드 트리(services/ scripts/)와 로컬 간 내용 차이 — 양쪽 LF 정규화 후 117개 중 116 동일, **1개 잔존**(`scripts/slippage_intraday.py`, 분석용 한 줄 `line_buffering=True`, 서비스·타이머 미실행 → 의도적으로 미배포)

## 3. 단계

1. services/ml/config.py 를 LF 로 전송, md5 대조
2. 서버 pre_deploy_check 재실행
3. 앞서(plan 1) CRLF 로 올라간 .py 4개를 LF 로 정규화 (내용 불변, md5 비교 가능성 회복)
4. 서버 트리 재비교

## 4. 리스크 & 사전 확인사항

- 라이브 ML 경로(`inference.py`·`shadow.py`)는 변경 대상 아님(내용 동일 확인)
- 재시작 없음 → 실행 중 프로세스는 구 모듈 유지. 다음 재시작 때 새 상수가 로드되나 사용처 없음
- ML 게이트 LIVE 전환 금지 규칙(CLAUDE.md) 영향 없음 — `ML_SHADOW_MODE=1` 유지

## 5. 검증 주체 (교차검증)

- [x] 옵션 C — `scripts/pre_deploy_check.py` (서버 실행) + 서버/로컬 전수 비교(구현과 독립된 읽기 전용 비교)

```
검증 주체: C
확인 항목: 4개 (md5 / 서버 게이트 / 무중단 / 트리 비교)
발견 이슈: 1개
  - 줄바꿈: 앞선 배포 5개가 CRLF 로 올라가 md5 비교가 오탐 → 서버에서 LF 로 정규화(내용 불변). 서버 파일 37개는 원래 CRLF(기존 deploy_to_aws 경로) — 그대로 둠
  - 내용 차이 1개 잔존: scripts/slippage_intraday.py (분석용, 라이브 무관) — 미배포
판정: PASS
```

## 6. 회고 (작업 종료 후 작성)

- **결과**: PASS — ML-count 오류 3건 해소, 서버 게이트 exit 0, 무중단
- **원인 귀속**: 실행 결함(커밋 b3b09f7 이후 서버 배포 누락 — lessons #31 의 반복)
- **한 줄 회고**: 'md5 다름 81개'는 줄바꿈 노이즈였고 실제 드리프트는 파일 1개였다 — 비교는 정규화 후에.
- **후속 조치**: scp 전송 전 CRLF→LF 정규화(또는 .gitattributes eol=lf) 검토 · hotfix_deploy.sh 는 로컬 .env 부재로 중단되므로 서버 게이트 방식 정식화 필요
