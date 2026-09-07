# ROADMAP.md 신설 + CLAUDE.md 축약

- **작성일(KST)**: 2026-09-07 22:10
- **작성자/세션**: Claude Opus 5 세션 (사용자 지시)
- **예상 소요**: 60분
- **관련 이슈/결정문서**: 없음 (문서 체계 정비 — ADR 불요)

## 1. 목표

"지금 무엇을 해야 하는가"를 조망하는 문서가 부재하다. 그 역할을 하던
`docs/00.보고/WBS.md` 는 2026-05-06 이후 4개월 방치됐고, 정보가 CLAUDE.md 본문·
ADR 30건·research 문서에 흩어져 매 세션 재구성이 필요하다.

1. `docs/ROADMAP.md` 신설 — **고정 길이 현황판**(이력 누적 금지, 덮어쓰기만)
2. `CLAUDE.md` 축약 — 레퍼런스 역할만 남기고 서술형 경위는 원본 문서로 이관

## 2. 성공기준 (Acceptance Criteria)

- [x] `docs/ROADMAP.md` 생성 — 지금위치/다음행동/판정트리거/대기사유/분기 5블록
- [x] `docs/lessons/INDEX.md` 생성 — 교훈 상세 표를 **원문 손실 없이** 이관
- [x] `CLAUDE.md` 바이트 수 40% 이상 감소 — **71.6%** (55,493 → 15,763)
- [x] `scripts/pre_deploy_check.py` 실행 시 **신규 ERROR 0건** (기존 대비 회귀 없음)
- [x] CLAUDE.md 에 `DC(12)` 표기 존치 (check_strategy_consistency ERROR 방지)
- [x] `MIN_VOLUME_KRW` 언급 줄에 `5억` 표기 존치 (check_min_volume WARNING 방지)
- [x] 축약으로 **삭제된 정보가 다른 문서에 존재**함을 링크로 확인 (정보 손실 0)

## 3. 단계

1. 기준선 측정 — CLAUDE.md 바이트, pre_deploy_check 사전 실행(기존 ERROR/WARN 수)
2. `docs/ROADMAP.md` 작성
3. `docs/lessons/INDEX.md` 로 교훈 상세 표 이관
4. `CLAUDE.md` 재작성 (파라미터 단일 진실 표 + 링크 인덱스 구조)
5. `docs/00.보고/WBS.md` 상단에 아카이브 동결 표기
6. pre_deploy_check 실행 → 사전/사후 비교
7. 커밋

## 4. 리스크 & 사전 확인사항

- 🔴 **CLAUDE.md 는 문서가 아니라 파싱 대상이다.** `pre_deploy_check.py` 가 정규식으로 읽는다:
  - `check_strategy_consistency()`: `DC\((\d+)\)` 를 모두 수집, config `DONCHIAN_PERIOD=12`
    가 그 집합에 **없으면 ERROR**
  - `check_min_volume()`: `MIN_VOLUME_KRW` 포함 줄의 `(\d+)\s*억` 수집, `5` 없으면 WARNING
  → 축약 시 두 토큰을 반드시 남긴다 (lessons #4 계열)
- 🔴 **정보 손실 위험**: 교훈 요약표는 사고 재발 방지의 1차 방어선이다.
  삭제가 아니라 **이관**이어야 하며, CLAUDE.md 에는 압축 인덱스 + 링크를 남긴다
- ⚠ ROADMAP.md 가 이력 누적형이 되면 WBS.md 와 같은 이유로 다시 방치된다
  → 문서 자체에 "이력 금지, 덮어쓰기" 규칙을 명시
- 참조: lessons #4(CLAUDE.md↔config 동기화), #19(상수 자체정의 금지)

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 A — 별도 세션
- [ ] 옵션 B — 서브에이전트(`cto` review/gate)
- [x] 옵션 C — 자동 검증 스크립트: `scripts/pre_deploy_check.py` (사전/사후 비교)
- [ ] 옵션 D — 다른 모델

**검증 기록**
```
검증 주체: C (scripts/pre_deploy_check.py 의 CLAUDE.md 파싱 룰 2건 직접 호출)
        + 자체 링크/교훈번호 대사 스크립트

확인 항목: 7개 (§2 성공기준)
  1. check_strategy_consistency()   사전 E0/W0 → 사후 E0/W0
  2. check_min_volume_krw_range()   사전 E0/W0 → 사후 E0/W0
  3. DC(N) 매칭 = {DC(12)} — config DONCHIAN_PERIOD=12 와 일치
  4. MIN_VOLUME_KRW 줄 억 표기 = {5억} — config 500,000,000 과 일치
  5. 교훈 번호 대사: 원본 49 / 신규 CLAUDE.md 49 / INDEX.md 49 — 누락 0
  6. 상대링크 전수 검사: 6개 문서 99건 — 깨짐 0
  7. CLAUDE.md 55,493 → 15,763 bytes (-71.6%)

발견 이슈: 2개 (둘 다 수정 완료)
  - [M1] INDEX.md 이관 시 표의 링크가 루트 기준(`docs/lessons/...`)이라
         `docs/lessons/` 내부에서 전부 깨짐 → 50건 경로 재작성, 파일 존재 전수 확인
  - [M2] 신규 CLAUDE.md 한 줄 인덱스에서 **교훈 #7 누락** (49 → 48)
         → 대사 스크립트가 적발, 배포·인프라 그룹에 삽입 후 49/49 복구

판정: PASS (조건부 — 아래 한계 명시)
한계: pre_deploy_check 전체 실행은 Windows CP949 로 UnicodeDecodeError 크래시(본 변경과 무관,
      subprocess stdout 디코딩). 전체 게이트는 Linux 서버 또는 배포 시 재확인 필요.
```

> 코드 변경 없음(문서 전용)이므로 자동 검증 스크립트로 회귀 부재를 확인한다.

## 6. 회고 (작업 종료 후 작성)

- **결과**: PASS
- **원인 귀속**: 해당 없음
- **한 줄 회고**: 축약의 실제 리스크는 "정보를 지우는 것"이 아니라 **"옮기면서 조용히 흘리는 것"**
  이었다 — 교훈 #7 누락과 링크 50건 깨짐 모두 눈으로는 안 보였고 **대사 스크립트가 잡았다**.
  이관 작업은 반드시 원본 대비 기계적 대사를 붙여야 한다(교훈 #44 "룰은 실패를 잡는지로 검증" 계열).
- **후속 조치**:
  - ROADMAP.md 갱신 트리거(판정선 도달 / ADR 발의 / 세션 종료)를 지키는지 다음 세션에서 확인
  - pre_deploy_check 에 "ROADMAP.md 신선도"(N일 초과 미갱신 시 WARNING) 룰 검토 —
    WBS.md 가 4개월 방치된 재발을 막는 유일한 자동 장치. 단, 감시기를 감시 대상 밖에 둘 것(교훈 #44)
  - Windows 에서 pre_deploy_check 전체 실행 불가 건은 별건 이슈로 남김
