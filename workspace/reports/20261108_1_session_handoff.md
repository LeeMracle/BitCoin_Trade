# 세션 인수인계 — 2026-11-08

**이번 세션 핵심**: BUY_DISABLED kill switch 적용 + 전략 재설계 사전 등록 plan 작성. 다음 세션은 **expert 재호출 → 백테스트 착수**가 바로 가능.

---

## 1. 현재 상태 (2026-11-08 기준)

| 항목 | 값 |
|---|---|
| 봇 | `btc-trader.service` active (MainPID 569036, 2026-11-07 14:35 UTC 재시작) |
| 매매 상태 | **BUY_DISABLED=True** (ADR 20261107-1) — 자동 매수 영구 차단 중 |
| 보유 | **1/15** — XAUT/KRW (11-05 02:13 UTC 진입, highest +1.03%, trail 미도달) |
| 매매 레짐 | BULL (BTC > EMA200), 관찰 레짐은 BEAR |
| cash_krw | **null** (여전히 미복구 — 다음 매수 신호 없으므로 우선순위 낮음) |

**실매매 변화**: 11-06 ~ 11-07 신규 거래 **0건** (BEAR + BUY_DISABLED 중복 차단).

---

## 2. 🔴 다음 세션 첫 작업 — 전략 재설계 백테스트 착수

### 준비 완료 상태
- **사전 등록 plan**: `workspace/plans/20261107_1_strategy_redesign.md` (expert 작성, 2026-11-07)
- **plan 승인 불필요** — Master가 "질문 왜 하냐"고 지적, 사전 등록 원칙에 따라 작성된 그대로 백테스트 착수
- 가설 4개 (H1 결합 / H2 거래대금 상한 / H3 상장일+순위 / H4 추세 게이트), IS/OOS 창 고정, 성공 기준 사전 등록

### 호출 방법
Master가 "재설계 백테스트 착수" 또는 "expert 백테스트" 지시하면 **bata-investment-expert** 재호출:
- plan 그대로 실행 (가설·창·성공 기준 변경 금지 — 과적합 유혹)
- IS 선택 → OOS 1회 검증 → 통과 시 ADR 20261108-1 (가칭) 발의 → engineer에 재활성 지시

### 백테스트 결과 시나리오
- **전수 통과**: 신규 ADR 발의 → engineer가 `BUY_DISABLED=False` 복귀 + 새 전략 반영 → 재가동
- **전수 미달**: "현행 DC12+ATR3 아키텍처는 모집단에서 작동 불가 확정" → 아키텍처 교체 수준 재설계 필요 (추가 plan)

---

## 3. 이번 세션 완료 작업 (2026-11-07 ~ 2026-11-08)

### 커밋 2개 (push 완료)
| 커밋 | 내용 |
|---|---|
| `9072460` | **lessons #55 E항목 서버 반영** (관찰 레짐 prefix `[관찰레짐]`, MainPID 521961→552524) + **regression_watch.sh 텔레그램 파싱 버그** (lessons #56, curl exit 23 + \|\| echo 폴백이 "200000" 조합). 24h 회귀 감시 CP4 완료 → Task #6 close |
| `6bf5c9d` | **ADR 20261107-1 BUY_DISABLED kill switch** 적용. config/scanner/realtime_monitor 매수 경로 전수 가드 + `check_buy_disabled_all_entry_paths()` AST 룰. lessons #57 등재. ROADMAP §3 ①번 "✅ 결정 완료" 갱신. MainPID 552524→569036 |

### 로드맵 반영 (`docs/ROADMAP.md`)
- §3 ①번: "Master 결정 필요" → "✅ BUY_DISABLED kill switch 적용 (11-07)"
- §4 ①번: "결정" → "결정 완료"
- 개입 신호: BULL 복귀 설명 교체 ("매수 재개" → "`BUY_DISABLED=True`가 매수 차단 유지")

---

## 4. 🟡 미해결 / 주의 사항

### (a) `cash_krw = null` 여전히 미복구
- 11-05 재시작 이후 계속 null
- **매수 차단 중이라 우선순위 매우 낮음** — 재활성 승인 직전에만 engineer 복구 위임
- 수동 `fetch_balance` 1회 호출로 해결 가능

### (b) 보유 포지션 XAUT/KRW 자연 종료 대기
- 트레일 5,316,198 (현재가 대비 여유), 하드캡 -6% (5,203,840)
- 체결 시 **마지막 전략 사용 기록**이 됨 (재설계 전까지 추가 진입 없음)
- Master 수동 매도 금지 (장부 왜곡) — 자연 종료 대기

### (c) 보류 작업 (ROADMAP §3 ④~⑦)
- ④ F2 주문 이력 7일 창
- ⑤ 감사 스크립트 "놓친 TP" 검사 (lessons #51)
- ⑥ 배포 경로 정식화 (`.env`, CRLF, pre-commit)
- ⑦ 이벤트 필터 A 방어 규칙
- → 재설계 작업 중엔 보류 유지, 백테스트 완료 후 재평가

---

## 5. 운영 플래그 현재 상태 (`services/execution/config.py`)

| 플래그 | 값 | 비고 |
|---|---|---|
| **`BUY_DISABLED`** | **True** | ADR 20261107-1 — 재활성 전수 조건 충족까지 유지 |
| `REGIME_FILTER_ENABLED` | True | ADR 20260516-2 — 매매 레짐(BTC>EMA200) |
| `REGIME_SWITCH_ENABLED` | False | 관찰 레짐 DRY-RUN |
| `EMA_TREND_ENABLED` | False | ADR 20261105-1 폐기 |
| `ML_FILTER_ENABLED` / `ML_SHADOW_MODE` | 1 / 1 | shadow 수집 중 |
| 슬롯 / 비중 상한 | 15 / 0.10 | ADR 20260825-1 |
| 하드 손절 / 트레일 | -6% / ATR(14)×3.0 | lessons #13 · ADR 20260930-1 |

---

## 6. lessons 번호 상태

이번 세션 추가:
- `#56` — regression_watch.sh 텔레그램 파싱 버그
- `#57` — BUY_DISABLED kill switch

INDEX.md에 전부 등재됨. 신규 lessons 작성 시 반드시 INDEX 실측 후 번호 부여.
