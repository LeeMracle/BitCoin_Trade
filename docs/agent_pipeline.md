# 에이전트 호출 파이프라인 (PM 주도)

> **원본**: 2026-09-07 이전까지 `CLAUDE.md` 본문에 있던 내용을 원문 그대로 이관했다.
> 팀 구성 요약은 [CLAUDE.md](../CLAUDE.md) "에이전트 팀", 상세 책임은 [agents/team.yaml](../agents/team.yaml).

---


PM은 사용자 요청을 받아 **싱글 / 병렬 / 순차** 3가지 패턴으로 다른 에이전트를 호출한다.
실제 호출은 `Agent` 툴에 `subagent_type` 지정 — 병렬 호출은 **단일 메시지에 multiple Agent 블록** (반드시 동시).

### 호출 패턴 3종

| 패턴 | 언제 | 호출 방식 |
| --- | --- | --- |
| **싱글** | 도메인 단일·책임자 명확 | `Agent(subagent_type="bata-engineer", ...)` 1회 |
| **병렬** | 독립 작업 동시 진행 (audit, RCA+영향평가) | 단일 메시지에 `Agent` 블록 N개 동시 호출 |
| **순차** | A 결과가 B 입력 (게이트, 승인 체인) | A 완료 → 결과 검토 → B 호출 |

### 표준 파이프라인 5종

#### 1. 일일 사이클 (daily)
```
09:05 PM → [싱글] bata-operator (start 브리핑)
            ↓ (이상 항목 있으면)
            PM이 분류해서 engineer 또는 expert에게 [싱글] 위임
09:10~23:00 operator 9분 cycle (자동)
23:00 PM → [싱글] bata-operator (end 브리핑)
            → PM이 당일 요약 (직접)
```

#### 2. P1 사이클 — 오류 반복 차단 (incident)
```
알람 발생
  ↓
PM → [싱글] bata-operator (트리아지)
  ↓ (분류 결과: 코드 오류)
PM → [싱글] bata-engineer (RCA → 수정 → 배포 → lessons → 룰)
  ↓
PM → [싱글] bata-operator (24h 회귀 감시 위임)
  ↓ (24h 후)
PM: incident 4단계 close 확인 → close 또는 보강 지시
```

#### 3. P2 사이클 — 주간 수익 개선 (월요일 09:30)
```
PM → [싱글] bata-investment-expert (주간 5Q 진단 + ADR 발의)
  ↓ (ADR 산출)
PM → [싱글] bata-engineer (게이트: 영향 grep + pre_deploy_check)
  ↓ (게이트 PASS)
PM: 승인 결정 (직접)
  ↓ (승인)
PM → [싱글] bata-engineer (배포 — deploy_to_aws.sh 또는 hotfix_deploy.sh)
  ↓
PM → [싱글] bata-operator (1주 drift 추적, 임계 초과 시 롤백 콜)
```

#### 4. 주간 audit (금요일) — **병렬 fan-out**
```
PM → [병렬] {
  bata-engineer:           "이번 주 incident 4단계 close 변환율 + 신규 lessons/룰 카운트"
  bata-investment-expert:  "이번 주 PnL/drift/필터 효과 요약"
  bata-operator:           "이번 주 false alarm rate / 오분류율 / 좀비 lag"
}
  ↓ (3개 결과 동시 회수)
PM: 통합 audit 보고서 작성 (직접) → 사용자 보고
```

#### 5. 신규 사고 패턴 (lessons에 없음) — **병렬**
```
PM → [병렬] {
  bata-engineer:           "RCA + 수정안 + lessons/룰 후보"
  bata-investment-expert:  "이 사고가 전략 수익에 미친 영향 + 도메인 권고"
}
  ↓ (RCA + 영향 평가 동시)
PM: 종합 → 우선순위 결정 → engineer 단독 또는 expert 합의로 다음 단계
```

### 호출 코드 예시

**싱글 호출 (engineer 위임)**
```
Agent(
  subagent_type="bata-engineer",
  description="realtime_monitor KeyError RCA",
  prompt="2026-05-24 22:15 KST 알람: realtime_monitor.py에서 KeyError 'pnl_realized'. RCA + 4단계 close 진행. 영향 범위 grep 결과 포함."
)
```

**병렬 호출 (주간 audit fan-out — 단일 메시지에 3개 Agent 블록)**
```
[같은 메시지에서 동시 호출]
Agent(subagent_type="bata-engineer", description="W## incident audit", prompt="...")
Agent(subagent_type="bata-investment-expert", description="W## PnL audit", prompt="...")
Agent(subagent_type="bata-operator", description="W## ops audit", prompt="...")
```

**순차 호출 (P2 사이클)**
```
1) Agent(subagent_type="bata-investment-expert", ...) → ADR 회수
2) PM 검토 후
3) Agent(subagent_type="bata-engineer", ..., prompt="ADR <링크> 게이트 검토") → 게이트 결과 회수
4) PM 승인
5) Agent(subagent_type="bata-engineer", ..., prompt="배포 실행") → 배포 결과 회수
6) Agent(subagent_type="bata-operator", ..., prompt="1주 drift 추적")
```

### PM 호출 규칙

- **싱글이 기본** — 단일 도메인 작업은 무조건 싱글 (병렬 남용 금지)
- **병렬은 audit·신규 사고만** — 독립 fan-out에만 사용. 의존 작업을 병렬로 돌리면 결과 불일치
- **순차에서 PM 직접 처리 단계 명시** — 게이트 결과 검토, 승인은 PM 본인 (위임 X)
- **하드룰 R4 (자기평가 금지)** — engineer가 만든 코드를 engineer가 PASS 판정하면 안 됨. 게이트는 별도 호출 또는 `cto` review
- **incident close는 PM이 직접 확인** — 4단계(lessons 작성, 룰 등록) 완료 검증 후 PM이 close 판정

