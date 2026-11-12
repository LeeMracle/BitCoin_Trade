# ROADMAP — 지금 무엇을 해야 하는가

> **갱신**: 2026-11-12 (모집단 축소 — U2-S1 BTC+ETH·DC12 OOS 통과, 시나리오 A) · **다음 갱신 트리거**: ADR·페이퍼 plan 승인 / 레짐 BULL 복귀 / 새 결정 / 세션 종료
>
> 🔴 **이 문서의 규칙 — 이력을 쌓지 않는다. 항상 덮어쓴다. 1페이지를 넘기지 않는다.**
> 이력이 필요하면 [decisions/](decisions/)(왜 정했나) · [lessons/](lessons/)(무엇이 고장났나) ·
> [reports/](../workspace/reports/)(세션 인수인계)에 이미 있다.
> 누적형으로 바뀌는 순간 이 문서는 [WBS.md](00.보고/WBS.md) 처럼 방치된다(2026-05-06 동결 전례).

---

## 1. 지금 어디에 있나

```
Phase 1 골격      ████████████ 완료
Phase 2 백테스트   ████████████ 완료   DC12+ATR3.0
Phase 3 실거래검증 ████████░░░░ n=52 · L1 조건부 PASS · L2 판독 대기(창 정의 결정 필요)
Phase 4 실전운영   ████████░░░░ 모듈·배포 완료, 검증 통과 대기
```

**한 줄**: 실거래 종목군(PIT)에서는 DC 돌파(v1)·대안 4축(v2) 모두 실패. **모집단을 BTC+ETH 로 줄이면 현행 DC12 가 OOS 7기준 전수 통과(11-12, Sharpe 1.31 · MDD -12.9% · n=62)** — 실패 원인은 전략이 아니라 모집단. cto 조건부 PASS(lookahead 수정 시 OOS Sharpe 1.22, IS 여유는 진입 슬리피지 0.2% 에서 소진). `BUY_DISABLED=True` 유지. **다음: ADR + 페이퍼 트레이딩 plan (Master 승인)**.

| | |
| --- | --- |
| 봇 | `btc-trader.service` active (10-29 00:16 UTC 재기동) · 텔레그램 브리핑 정상 · ML 게이트 **shadow** 유지 |
| 레짐 | **BEAR** — BTC 103.67M < EMA200 104.60M · **신규 매수 차단** (마지막 청산 10-29 PEPE) · F&G 26 |
| 포지션 | **0 / 15** — 10-05 보유분(BSV·QTUM·CKB·ONDO 등) 전부 청산됨 |
| 총자산 | **262,056원 (-12.6%, 기준 30만원)** — 서킷(-20%, 24만원)까지 약 22,000원 |
| 검증 기준선 | `strategy_start` **2026-10-05 01:48 UTC** · 이전 창 n=90 은 `closed_trades` 에 보존 |

---

## 2. 검증 창 결과 — 손절 -6% ([ADR 20260930-1](decisions/20260930_1_hard_stop_6pct.md))

| | 52건 전부 | 신규 진입분만(기존 보유 7건 제외) |
| --- | --- | --- |
| n · 승 | 52 · 16 | 45 · 16 |
| 승률 95%CI(Wilson) | **30.8% [19.9, 44.3]** | **35.6% [23.2, 50.2]** |
| 건당 평균 | -1.85% | -0.90% |
| n=50 규칙(CI 상한 < 43.6%) | 상한 44.3% — **0.7%p 차로 미확정** | n<50 — **판정 시점 전** |

- 기존 보유 7건은 -10% 캡 적용분이며 **0승 7패, 평균 -7.97%**. 포함 여부가 판정을 가른다 → **사용자 결정**
- L1 실행 검증([research/20261030_1](../workspace/research/20261030_1_l1_execution_audit.md)): 진입가·수익률 **52/52 체결과 일치** → 승률 31% 는 장부 오류가 아니다. 손절 3·TP 4건 FAIL 은 1분 내 급변 구간 슬리피지(코드 결함 0). 슬리피지 비용 건당 약 **-0.27%p** — L2 에서 백테스트와 비교 시 차감
- 직전 창(-10% 캡, 09-02 ~ 10-05): n=90, 승률 60.0%, 건당 +0.69% — 손절을 줄인 뒤 오히려 나빠졌다(단, 시장 구간이 다르다)
- **원인 분석**([research/20261103_1](../workspace/research/20261103_1_live_vs_backtest_universe.md)): 보유 중앙값 36분, -6% 손절 23건 중 13건이 진입 시간봉 안. 백테스트 진입 봉을 비관 처리해도 승률 52.4→51.7%(가설 기각). 대신 **신규 45건 중 41건이 백테스트 종목군(오래된 대형 알트 40종) 밖의 최근 상장 소형 코인** — 백테스트 기대치가 지금 봇에 적용되지 않는다

---

## 3. 열린 일 (우선순위 순)

| # | 무엇 | 비고 |
| --- | --- | --- |
| ① | ✅ **BUY_DISABLED kill switch 적용 (11-07)** — Master 결정 | config.py `BUY_DISABLED=True` + scanner/realtime_monitor 가드 + `check_buy_disabled_all_entry_paths()` AST 룰. 재활성은 ①-2 완료 + 신규 ADR + Master 재승인 전수 충족시만 · [ADR 20261107-1](decisions/20261107_1_buy_disabled_until_redesign.md) · [lessons #57](lessons/20261107_1_buy_disabled_kill_switch.md) |
| ①-2 | ✅ 재설계 v1(DC+필터 H1~H4, OOS 미달) · v2(대안 4축 A1~A4, IS 전부 탈락) **둘 다 시나리오 B 종결** | [research/20261110_1](../workspace/research/20261110_1_architecture_redesign_result.md) · cto 조건부 PASS. 새 plan 설계 입력: 가설별 구조적 손익분기 기준, Guards OFF 1차 측정 |
| ①-3 | ✅ **모집단 축소 — 시나리오 A (11-12)** | U2-S1 BTC+ETH·DC12·비중 0.475 · [research/20261112_1](../workspace/research/20261112_1_universe_reduction_result.md) · cto 조건부 PASS (M1 build_panel 레짐·ATR lookahead, M3 터치 진입 경로) |
| ①-4 | **ADR + 페이퍼 트레이딩 plan — Master 승인 필요** | 터치 진입 경로(현행 realtime_monitor 구조) · 실거래 EMA250·Wilder ATR · **진입 슬리피지 ≥0.2% 중단선** 사전 등록 · `build_panel` lookahead lessons 기록 |
| ② | ✅ **F1 레짐 fail-closed — 배포 완료(11-03 13:03 UTC)** | 조회 실패 시 3회 재시도 후 매수 차단, 봇 시작 시 기본값도 차단 · 서버 테스트 7/7 · pre_deploy_check 서버 실행 PASS · 백업 `~/deploy_backup/20261103_F1/` |
| ③ | ~~창 정의 결정~~ → 불필요 | 백테스트가 실거래를 재현해 "개선 필요"가 독립 근거로 확정. 판정 기준 §3-4 구조적 여유를 PIT 기준으로 재계산 |
| ④ | F2 주문 이력 7일 창 — 7일 넘게 보유 후 수동 매도 정산 시 TP 매도 누락 | `position_pnl.rebuild_realized_from_exchange` 등에 `since=entry` |
| ⑤ | 감사 스크립트에 "놓친 TP" 검사 추가 | C4 는 존재만 검사 — lessons #51 |
| ⑥ | 배포 경로 정식화 | `hotfix_deploy.sh` 로컬 `.env` 부재로 중단 · CRLF/LF 혼재 · pre-commit 훅 미활성(`core.hooksPath`) |
| ⑦ | 이벤트 필터 A(투자유의 종목) 방어 규칙 도입 여부 | [research/20261005_3](../workspace/research/20261005_3_event_filters_AB.md) · 사용자 결정 |

종료: 진입 후보 선택 규칙(0/4, [research/20261005_2](../workspace/research/20261005_2_candidate_selection_rule.md)) · 이벤트 필터 B. 기각: TP1 후 본전손절 · 일일 진입 상한. 보류: TP2 15%.

---

## 4. 사용자가 할 일

| # | 무엇을 | 언제 |
| --- | --- | --- |
| ① | ✅ **BUY_DISABLED kill switch 결정 완료 (11-07)** — 재설계 완료 전까지 매수 영구 차단 | 완료 |
| ② | 아침 텔레그램 브리핑 확인 | 매일 09:32 KST |
| ③ | 앱에서 직접 매도 시 장부 왜곡 — 정확히 남기려면 봇 정지 후 `manual_close_position.py` | 수동 매도 시 |

### 개입 신호

| 신호 | 뜻 |
| --- | --- |
| 브리핑이 **안 옴** | 봇/타이머 정지 — 즉시 확인 (09-26 ~ 10-05 9일 정지가 이 경로였다) |
| 레짐 **BULL 복귀** | `BUY_DISABLED=True` 가 매수 차단 유지 — kill switch 해제 전 매수 재개 없음 |
| BEAR **30일 이상** 지속(11-28 경) | 판정 기준 §3-4 ② 즉시 트리거 — 표본 정체, 개선 검토 |
| 총자산 **-17% 이하** | §3-4 ④ 서킷 근접 트리거 · 현재 -12.6% |
| "매수 중단" / "연패" | 안전장치 발동 (5연패 → 72h) |

---

## 5. 하면 안 되는 것

- ❌ 레짐 게이트 완화 — 2026-05-03 전례 승률 17.4% · 누적 **-111%**. BEAR 대기가 길어도 풀지 말 것
- ❌ 결과를 본 뒤 창 정의를 유리한 쪽으로 고르기 — 고른 이유를 결정 문서에 먼저 적을 것
- ❌ 실거래 표본으로 파라미터 선택 — 선택은 백테스트, 실거래는 방향 확인
- ❌ 브리핑의 누적 209건(승률 45%, 합계 -153%)으로 판정 — 여러 전략·파라미터가 섞인 값
- ❌ ML 게이트 LIVE 전환 — prior shift 로 99.3% 차단 ([CLAUDE.md](../CLAUDE.md))
- ❌ 캡·손절 파라미터를 config 만 바꿔 배포 — **보유 포지션 소급 여부를 코드 경로로 확인**할 것

---

## 6. 근거 문서

| 질문 | 문서 |
| --- | --- |
| 왜 이렇게 정했나 | [docs/decisions/](decisions/) |
| 무엇이 고장났었나 | [docs/lessons/INDEX.md](lessons/INDEX.md) |
| 판정 규칙 | [docs/strategy_improvement_criteria.md](strategy_improvement_criteria.md) |
| 이번 검증 | [plan 20261030_1](../workspace/plans/20261030_1_l1_execution_audit.md) · [research/20261030_1](../workspace/research/20261030_1_l1_execution_audit.md) |
| 기대치 상한 | [research/20260831_2](../workspace/research/20260831_2_why_no_profit_yet.md) |
| 프로젝트가 무엇인가 | [CLAUDE.md](../CLAUDE.md) |
