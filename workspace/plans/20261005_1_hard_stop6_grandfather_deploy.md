# 하드 손절 -6% 배포 — 기존 포지션 보호(grandfather) 패치 포함

- **작성일(KST)**: 2026-10-05 11:00
- **작성자/세션**: 에이전트(Claude) + 사용자 승인("기존 포지션 보호 패치 후 배포")
- **예상 소요**: 1.5시간
- **관련 이슈/결정문서**: [ADR 20260930-1](../../docs/decisions/20260930_1_hard_stop_6pct.md) · lessons #4, #13, #19, #31, #33, #48

## 1. 목표

ADR 20260930-1 의 하드 손절 -6% 를 **신규 진입부터만** 적용해 서버에 배포한다.
ADR 은 "기존 포지션 소급 없음(즉시 청산 방지)"을 전제했으나, `realtime_monitor.py:799`
(`trail_stop = max(merged, entry*(1-cap))`)가 레벨 갱신마다 기존 포지션에도 새 캡을 적용해
**전제가 코드와 어긋난다**(2026-10-05 발견). 기존 포지션이 -10% 캡을 유지하도록 고친다.

## 2. 성공기준 (Acceptance Criteria)

- [ ] 신규 진입 포지션에 `hard_stop_pct`(=config 값 0.06)가 state 에 저장된다
- [ ] `hard_stop_pct` 없는 기존 포지션은 `LEGACY_HARD_STOP_LOSS_PCT`(0.10) 바닥을 쓴다 — 갱신 후 `trail_stop` 이 **변하지 않거나 더 낮다**
- [ ] 단위 시나리오: 기존 포지션(entry 100, trail 90) 에 레벨 갱신 → trail_stop 90 유지 / 신규(0.06) → 94
- [ ] `pre_deploy_check` 에 "기존 포지션 바닥 계산이 헬퍼를 통하는지" 룰 추가 + 역방향 테스트(룰이 위반을 잡는지)
- [ ] 서버 배포 후 재시작 직후 **청산 0건**(BSV·QTUM·CKB·ONDO 보유 유지) 확인
- [ ] 서버 md5 일치 · `systemctl is-active btc-trader` · pre_deploy_check 서버 실행 통과
- [ ] `strategy_start` 배포 시각으로 이동(ADR 체크리스트 4) — `closed_trades` 보존
- [ ] ADR·CLAUDE.md·ROADMAP 동기화, 교차검증 기록

## 3. 단계

1. config: `LEGACY_HARD_STOP_LOSS_PCT = 0.10` 신설 (lessons #19 — 상수는 config 에서만 정의)
2. realtime_monitor: `_pos_hard_floor(pos)` 헬퍼 + 갱신 2곳(789, 1206) 교체 + 신규 진입 시 `hard_stop_pct` 저장
3. pre_deploy_check 룰 + 역방향 테스트
4. 로컬 단위 시나리오 검증
5. 교차검증 (서브에이전트 review)
6. 서버 배포: 봇 정지 → state/config 백업 → scp(config, realtime_monitor) → `strategy_start` 이동 → 서버 pre_deploy_check → 기동 → 청산 0건 확인
7. ADR·문서 갱신, 커밋

## 4. 리스크 & 사전 확인사항

- 로컬 `pre_deploy_check` 는 `services/.env` 부재로 실패(로컬 한정, ADR 명시) → `hotfix_deploy.sh` 가 1단계에서 중단됨. 서버에서 검증을 실행하는 동등 절차로 배포하고 사유를 기록한다 (lessons #33 취지: 검증 생략이 아니라 장소 이동)
- 재시작 중(레벨 계산 ~3.5분) 손절 감시 공백 — 이미 오늘 1회 경험, 허용
- 현재 보유 11개 중 4개가 -6.7~-8.8% — 패치가 틀리면 즉시 시장가 손절 + 연패 카운트. 그래서 기동 직후 로그로 청산 여부를 직접 확인한다
- 서버 `ml.conf` drop-in(shadow) 은 건드리지 않는다

## 5. 검증 주체 (교차검증)

- [ ] 옵션 A — 별도 세션
- [x] 옵션 B — 서브에이전트 review (구현 세션은 PASS 판정 금지, R4)
- [x] 옵션 C — `scripts/pre_deploy_check.py` (서버 실행)
- [ ] 옵션 D — 다른 모델

```
검증 주체: C (pre_deploy_check, 서버 실행) — 옵션 B 서브에이전트 리뷰는 미실시
확인 항목: 8개 중 7개 (단위 시나리오·역방향 5/5·md5 4/4·청산 0건·strategy_start 이동·active·compile / ADR 동기화는 본 기록)
발견 이슈: 2개
  - 서버 게이트 ML-count 오류 3건 — 이번 변경 무관한 기존 드리프트(ml_shadow_count.py·services/ml/config.py 미배포). ml_shadow_count.py 만 서버에 추가됨. 미해결
  - 게이트 미통과 상태로 기동(봇 정지 시간 최소화 목적) — 사용자 확인 필요
판정: 조건부 PASS (룰을 작성한 세션이 룰 통과를 근거로 한 것이므로 독립 리뷰 권장)
```

## 6. 회고 (작업 종료 후 작성)

- **결과**: 부분 PASS — 배포·소급 방지 확인, 서버 게이트 ML 드리프트 잔존
- **원인 귀속**: 계획 결함(ADR 가 "state 무변경 = 소급 없음"을 코드로 확인하지 않음)
- **한 줄 회고**:
- **후속 조치**: lessons 기록(ADR 전제는 코드 경로로 검증) · 오염 표본(auto_cleanup_zero_balance) 처리 별도 plan
