# cto health 스킬 — 다중 프로젝트 가시성 추가 (교훈 #17 대응)

- **작성일(KST)**: 2026-04-22 13:35
- **작성자/세션**: 자비스 (Claude Opus 4.7 [1M])
- **예상 소요**: 60분
- **관련 이슈/결정문서**: [docs/lessons/20260421_1_multi_project_process_misdiagnosis.md](../../docs/lessons/20260421_1_multi_project_process_misdiagnosis.md)

## 1. 목표

`cto health` 결과에서 **t3.micro에 공존하는 다른 프로젝트(Stock_Trade, Blog_Income)의 서비스 + RSS 상위 프로세스의 소유권**까지 자동 가시화한다. 교훈 #17의 재발(타 프로젝트 프로세스를 좀비로 오판)을 구조적으로 차단한다.

## 2. 성공기준 (Acceptance Criteria)

- [ ] `scripts/server_processes_audit.sh` 신규 — SSH로 서버 전체 running systemd unit + RSS top10 프로세스 + 각 PID의 `/proc/<pid>/cwd` 분류 출력
- [ ] `cto health` 결과에 "다중 프로젝트 동거 현황" 표가 포함되어 모든 활성 서비스와 그 소유 프로젝트(`/home/ubuntu/<repo>` 단위)가 1눈에 보인다
- [ ] RSS 상위 프로세스 중 `/proc/cwd`가 BitCoin_Trade가 아닌 것은 "타 프로젝트 소유"로 명시 분류 출력
- [ ] 04-22 직접 실행 결과로 PID 356167(stock-trader)이 "Stock_Trade 소유"로 표기되는 것을 실측 확인
- [ ] `pre_deploy_check.py`는 변경 없음 (검증 대상 외 — 휴먼 진단 보조)
- [ ] 변경 파일은 git 추적되는 영역(`scripts/`)에 둔다. `.claude/`는 gitignore이므로 SKILL.md 수정은 호출 위치 1줄만 추가

## 3. 단계

1. `scripts/server_processes_audit.sh` 작성 — bash + ssh
   - input: PEM 경로(env), HOST(env)
   - output:
     a) `systemctl list-units --type=service --state=running` 전체
     b) `ps -eo pid,user,rss,cmd --sort=-rss | head -10`
     c) 위 PID 각각에 `sudo readlink /proc/<pid>/cwd` 매핑
     d) 프로젝트별 그룹핑 (`/home/ubuntu/BitCoin_Trade` / `Stock_Trade` / `Blog_Income` / `기타`)
2. 로컬에서 실행 테스트 — 04-22 실측치로 PID 356167 분류 확인
3. `.claude/skills/cto/SKILL.md` health 절차에 "8. 다중 프로젝트 가시성" 항목 추가 (스크립트 호출 1줄)
4. 출력 표 템플릿에 "다중 프로젝트 동거 현황" 표 추가
5. cto health 직접 호출 → 새 표가 결과에 포함되는지 확인
6. 커밋: `scripts/server_processes_audit.sh` + `workspace/plans/20260422_*` (SKILL.md는 gitignore이므로 제외)

## 4. 리스크 & 사전 확인사항

- **리스크 1**: `.claude/`가 gitignore이므로 SKILL.md 수정은 다른 환경에 전파되지 않음 → 스크립트 자체를 git 추적하여 호출 가능 상태로 보존. SKILL.md 변경은 본 환경 한정 편의.
- **리스크 2**: `sudo readlink /proc/<pid>/cwd`는 sudo 권한 필요. ubuntu 계정은 sudoers에 있으므로 OK. 다만 NOPASSWD 여부 확인.
- **리스크 3**: ps RSS 정렬은 한 시점 스냅샷 — 짧은 부하로 순위 흔들림 가능. `head -10`이면 충분 (오판 방지가 목적).
- 참조 lessons: #5(메모리), #17(다중 프로젝트), #9(자동화 cron 등록 의무) — #9는 적용 대상 아님(스킬 보조 스크립트)

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [x] 옵션 C — 자동 검증 스크립트: 신규 `scripts/server_processes_audit.sh` 자체를 직접 실행하여 PID 356167이 Stock_Trade 소유로 분류되는지 실측

**검증 기록 형식 (필수)**
```
검증 주체: C (직접 실행 실측)
확인 항목: 6개 (§2 성공기준)
발견 이슈: (실행 후 기재)
판정: (실행 후 기재)
```

> 본 작업은 진단 보조 스크립트이므로 자기검증으로 충분. 운영 영향 없음.

## 6. 회고 (작업 종료 후 작성)

- **결과**: PASS
- **원인 귀속**: 해당 없음
- **한 줄 회고**: 핵심 진단 로직을 git 추적되는 `scripts/`에 두고 SKILL.md는 호출 위치만 가리키게 분리한 게 적절. `.claude/` gitignore 제약을 우회하면서 다른 환경 전파성도 확보.
- **후속 조치**: 다중 환경 전파 필요 시 `.claude/skills/`만 gitignore 예외 처리 별도 검토(현재는 본 환경 한정)
- **검증 기록**:
```
검증 주체: C (직접 실행 실측)
확인 항목: 6개 (§2 성공기준)
발견 이슈: 0개
  - PID 356167은 Stock_Trade로 정확히 분류됨
  - PID 398867은 BitCoin_Trade로 분류됨
  - PID 373769는 Blog_Income으로 분류됨
판정: PASS
```
