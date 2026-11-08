# 세션 인수인계 — 2026-11-08 (2차, 23:00 KST)

**이번 세션 핵심**: 재설계 v1 백테스트 완료 **시나리오 B(미달)** + 아키텍처 교체 plan v2 작성 완료. 다음 세션은 **v2 백테스트 착수**.

---

## 1. 현재 상태 (2026-11-08 23:00 KST)

| 항목 | 값 |
|---|---|
| 봇 | `btc-trader.service` active (MainPID 569036, 2026-11-07 14:35 UTC 재시작) |
| 매매 상태 | **BUY_DISABLED=True** (ADR 20261107-1) — 자동 매수 영구 차단 유지 |
| 보유 | **1/15** — XAUT/KRW (11-05 진입, highest 5,593K +1.03%, trail 5,316K) |
| 신규 거래 | 0건 (kill switch 가드 발화 수백회/일, 정상) |
| h1 캐시 | **236개** (ouput/h1/, 재설계 v2 백테스트에 재사용) |
| cash_krw | null (미복구, 매수 차단 중이라 우선순위 낮음) |

---

## 2. 🔴 다음 세션 첫 작업 — 재설계 v2 백테스트 착수

### 준비 완료
- **plan 파일**: `workspace/plans/20261108_1_architecture_redesign.md`
- Master 승인 완료 (2026-11-08) — 사전 등록 원칙, 수정 없이 그대로 실행

### 가설 4개 (A1~A4) — 전부 DC 돌파 아닌 다른 축
| # | 아키텍처 | 핵심 |
|---|---|---|
| A1 | Mean Reversion | BB(20,2σ) 하단 + RSI(2)<10 → 다음 봉 시가 진입 |
| A2 | MTF Momentum | 일봉 EMA20>EMA50 + 시간봉 EMA20 pullback 재돌파 |
| A3 | NR7 Volatility | 7봉 최소 range → 다음 봉 돌파 |
| A4 | RS Rotation | 20봉 수익률 상위 10 + BTC 상회 |

### 호출 방법
Master가 "재설계 v2 백테스트 착수" (또는 유사) 지시 → **bata-engineer** 호출로 `scripts/backtest_architecture_redesign.py` 작성 → 실행 → **bata-investment-expert** 결과 해석

### 과적합 방지 강화 (plan v1 교훈 반영)
- 🆕 **IS 2분할 안정성** — IS-A(2024-10-29~2025-03-19) + IS-B(2025-03-20~2025-07-11) 둘 다 승률≥45% + 건당>0 + n≥25
- 🆕 **IS→OOS Sharpe 하락폭 < 50%** (H2처럼 2.38→0.28 폭락 방지)
- 기존: IS 재사용/가설 추가/창 재정의 금지

### 예상 리소스
- 백테스트 합계 3~4h + 코드 1~2h + 보고 1~2h = **6~10h** 다음 세션
- h1 캐시 재사용 → fetch 거의 불필요

### 결과 시나리오
- **A 전수 통과**: ADR 20261108-2 발의 → `BUY_DISABLED=False` + 새 전략 반영 → 재가동
- **B 전수 미달**: 현행 모집단에서 작동 가능한 축이 없음 — 본격 fundamentals 재검토 (거래소 교체? timeframe 변경? ML 랭커?)
- **C 경계**: Master 판단

---

## 3. 이번 세션 완료 작업 (2026-11-08)

### 커밋 1개 (push 완료)
| 커밋 | 내용 |
|---|---|
| `6b318aa` | plan 20261107_1 백테스트 결과 (시나리오 B 확정) + 아키텍처 교체 plan 20261108_1 사전 등록 |

### 재설계 v1 백테스트 결과
**PIT 기준선 재현** (음의 기대값):
- IS 승률 35.0% 건당 -0.86% / OOS 승률 37.5% 건당 -0.48%

**IS 가설별**:
- H1 결합(max_atr+confirm_close): 승률 50.6% 건당 +0.64% Sharpe 1.15 PASS
- H2 거래대금 상위30: 승률 50.4% 건당 **+1.21%** Sharpe **2.38** PASS ← IS 선택
- H3 상장365+상위50: 승률 46.5% FAIL(승률)
- H4 EMA20+EMA50: 승률 44.9% FAIL(승률)

**OOS (H2 1회)**:
- Sharpe 0.28 (기준 ≥0.8) **FAIL**
- 승률 CI 하한 39.8% (기준 >43.6%) **FAIL**
- MDD -3.6%, 건당 +0.20%, 거래 980 (3개 PASS)
- → 4기준 중 2 PASS 2 FAIL — **시나리오 B 확정**

**결론**: 현행 DC12+ATR3 breakout 아키텍처 모집단에서 작동 불가 확정. IS→OOS Sharpe 폭락(H2 2.38→0.28)이 과적합 신호.

---

## 4. 이번 세션 블로커 해결 이력 (교훈)

### DNS Timeout 이슈
- `aiodns.error.DNSError: Timeout while contacting DNS servers`
- 공유기 DNS(192.168.219.1) 응답 지연 → fetch_ohlcv의 aiohttp 경로에서 신규 소형 종목 fetch 실패
- **해결**: Wi-Fi DNS를 Cloudflare 1.1.1.1 교체 (Master가 관리자 PowerShell로 수동 실행)
- 영향: DNS 교체 후 148+8종목 fetch 정상화
- **lessons 등재 고려사항**: DNS 교체는 reversible이고 다른 PC에서도 재발 가능성 있음 → 환경 설정 체크리스트에 포함

### Monitor timeout 반복
- Monitor는 최대 3600000ms(1시간) → 긴 작업은 자연 timeout
- 재armed 패턴 확립 — 완료되지 않은 작업이면 TaskList 체크 후 재설정

---

## 5. 🟡 미해결 / 주의 사항

### (a) `cash_krw = null` 지속
- 11-05 재시작 이후 계속 null
- **매수 차단 중이라 영향 없음** — v2 백테스트 통과 후 BUY_DISABLED 해제 전에 복구 필요

### (b) 보유 포지션 XAUT 자연 종료 대기
- 현재 +1.03% (highest 5,593K), trail 5,316K 미도달
- 체결 시 **마지막 전략 사용 기록**
- Master 수동 매도 금지 (장부 왜곡)

### (c) output/h1/ 캐시 재사용
- 138MB, .gitignore 처리 (git 공유 안 됨)
- 다른 PC에서 이어 하려면 재fetch(약 2~3시간) or USB 복사 필요

---

## 6. 운영 플래그 현재 상태

| 플래그 | 값 | 비고 |
|---|---|---|
| **`BUY_DISABLED`** | **True** | ADR 20261107-1, v2 통과 전까지 유지 |
| `REGIME_FILTER_ENABLED` | True | ADR 20260516-2 |
| `REGIME_SWITCH_ENABLED` | False | 관찰 레짐 DRY-RUN |
| `EMA_TREND_ENABLED` | False | ADR 20261105-1 폐기 |
| 슬롯 / 비중 상한 | 15 / 0.10 | ADR 20260825-1 |

---

## 7. lessons 번호 상태 (최신)

- #53 — stale cron watchpoints false alarm
- #54 — EMA-TREND throttle (알림 폭주)
- #55 — 레짐 명명 혼동
- #56 — regression_watch 텔레그램 파싱 버그
- #57 — BUY_DISABLED kill switch

v2 백테스트 중 신규 lessons 생기면 **#58부터**.

---

## 8. 다른 PC에서 이어 진행 체크리스트

1. `git clone` 또는 `git pull origin master`
2. 외부 전달 필요: `services/.env` + SSH 키 (`upbit-trading-key-seoul.pem`)
3. Python 3.13 venv + `pip install -r requirements.txt`
4. DNS 체크 (Cloudflare 교체 필요 시)
5. h1 캐시: 재fetch(2~3h) 또는 USB 복사(138MB)
6. Claude에게 "재설계 v2 백테스트 착수" 지시 → 이 handoff + ROADMAP 읽고 자동 진행
