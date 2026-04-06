# 최종 config 권장안 — 200EMA 필터 적용

- 작성일: 2026-04-05
- 근거: v1.5 Sprint B P5-16 레짐 필터 비교 결과 (Filter B 확정)
- 대상 파일: `services/execution/config.py`

> 이 문서는 코드 변경안을 제시하며, 실제 코드 수정은 별도 작업으로 진행한다.

---

## 1. 현재 config.py 관련 설정 현황

```python
# 현재 전략 설정 (변경 불필요)
STRATEGY = "composite"
STRATEGY_KWARGS = {"dc_period": 20}

# 현재 레짐 필터 설정: 없음
# (서킷브레이커만 존재)
CIRCUIT_BREAKER_ENABLED = True
CIRCUIT_BREAKER_THRESHOLD = -0.20
```

---

## 2. 권장 추가 설정

`config.py`의 전략 설정 섹션에 아래 항목을 추가한다.

```python
# ═══════════════════════════════════════════════════════
# 레짐 필터 (200EMA 기반)
# ═══════════════════════════════════════════════════════
# v1.5 Sprint B 검증 결과: Filter B (200EMA) 최선
# - OOS Sharpe: 1.115 → 1.274 (+14.3% 개선)
# - 차단 조건: BTC/KRW 일봉 종가 < 200일 지수이동평균
# - 효과: OOS 구간에서 패배 거래 2건(승률 0%, 평균 -3.87%) 차단
# - 근거: output/regime_filter_comparison.md

REGIME_FILTER_ENABLED = True          # True: 200EMA 필터 활성화
REGIME_FILTER_EMA_PERIOD = 200        # EMA 기간 (일)
REGIME_FILTER_SYMBOL = "BTC/KRW"     # 필터 기준 종목 (BTC만 적용)
# 필터 적용 방식:
#   - BTC/KRW 종가 >= 200EMA → 신규 매수 허용
#   - BTC/KRW 종가 < 200EMA → 신규 매수 차단 (기존 포지션 트레일링스탑 유지)
```

---

## 3. 코드 변경이 필요한 파일

### 3-1. `services/execution/scanner.py`

매수 후보 종목 스캔 단계에서 200EMA 조건을 추가한다.

**변경 위치**: 진입 신호 생성 직전 (DC 돌파 조건 확인 후)

**추가 로직 (의사코드)**:
```python
if config.REGIME_FILTER_ENABLED:
    btc_close = get_latest_close("BTC/KRW")   # 최신 BTC 일봉 종가
    btc_ema200 = compute_ema(btc_close_series, period=200)  # 200EMA 계산
    if btc_close < btc_ema200:
        logger.info("레짐 필터 발동: BTC < 200EMA, 신규 매수 차단")
        return []   # 전 종목 신규 진입 건너뜀
```

**주의사항**:
- 200EMA 계산을 위해 최소 200일 이상의 BTC 일봉 데이터가 필요하다.
- DuckDB 캐시(`data/cache.duckdb`)에서 BTC/KRW 일봉 조회 후 pandas EMA 계산.
- 계산 결과를 캐싱하여 스캔 루프 내 중복 호출을 방지한다.

### 3-2. `services/execution/realtime_monitor.py`

웹소켓 실시간 감시에서도 동일한 200EMA 조건을 적용한다.

**변경 위치**: 돌파 신호 감지 후 주문 발행 직전

**추가 로직 (의사코드)**:
```python
if config.REGIME_FILTER_ENABLED:
    if not is_btc_above_ema200():   # scanner와 동일한 함수 공유
        logger.info("레짐 필터: BTC < 200EMA, 돌파 신호 무시")
        return  # 주문 발행 없이 종료
```

**교훈 참조**: lessons/20260404_1 — 필터는 모든 매수 경로(scanner + realtime_monitor)에 동시 적용 필수. 한 곳만 적용 시 필터 우회 발생.

### 3-3. 공통 헬퍼 함수 (신규 작성 권장)

두 파일에서 재사용할 수 있도록 공통 유틸 함수로 분리한다.

**권장 위치**: `services/execution/regime_filter.py` (신규)

```python
# services/execution/regime_filter.py 권장 내용 (의사코드)

def is_btc_above_ema200(db_path: str, ema_period: int = 200) -> bool:
    """BTC/KRW 종가가 200EMA 위에 있는지 확인.
    
    Returns:
        True  → 필터 통과 (매수 허용)
        False → 필터 차단 (매수 금지)
    """
    # 1. DuckDB에서 BTC/KRW 일봉 최근 (ema_period + 50)개 조회
    # 2. pandas ewm(span=ema_period).mean() 으로 EMA 계산
    # 3. 최신 종가 vs 최신 EMA 비교
    # 4. close >= ema200 → True, close < ema200 → False
```

---

## 4. 테스트 체크리스트

코드 적용 후 아래 항목을 순서대로 확인한다.

- [ ] BTC 종가와 200EMA 값이 정상적으로 출력되는가 (로그 확인)
- [ ] BTC < 200EMA 상황에서 scanner가 빈 리스트를 반환하는가
- [ ] BTC < 200EMA 상황에서 realtime_monitor가 돌파 알림을 발행하지 않는가
- [ ] BTC >= 200EMA 상황에서 기존 신호 생성이 정상 동작하는가
- [ ] 기존 포지션의 트레일링스탑이 필터와 무관하게 계속 작동하는가
- [ ] REGIME_FILTER_ENABLED = False 설정 시 기존 동작과 동일한가

---

## 5. 기대 효과

| 항목 | 필터 전 | 필터 후 |
|------|---------|---------|
| OOS Sharpe (2024~2026) | 1.115 | 1.274 |
| OOS 거래수 | 11건 | 9건 |
| OOS 승률 | 45.5% | 55.6% |
| OOS 평균수익 | +8.44% | +11.17% |
| 차단 거래 | — | 2건 (승률 0%, 평균 -3.87%) |

---

## 6. 주의사항

1. **BTC 200EMA는 일봉 기준**이다. 4시간봉이나 1시간봉 EMA와 혼동하지 않는다.
2. **기존 포지션은 영향 없다.** 필터는 신규 진입만 차단하며, 보유 중인 포지션의 트레일링스탑은 계속 작동한다.
3. **200EMA 데이터 워밍업 필요**: 처음 실행 시 최소 200일 이상의 BTC 일봉이 DuckDB에 있어야 EMA가 정확하게 계산된다. 현재 캐시에는 3,110일봉이 있으므로 문제없다.
4. **CLAUDE.md 동기화 필요**: 코드 적용 후 CLAUDE.md의 Phase 3 설명에 "200EMA 필터 적용" 내용을 추가한다. (lessons/20260331_1 교훈 — config ↔ 서버 파라미터 동기화 필수)

---

*참조: output/regime_filter_comparison.md, docs/lessons/20260404_1_v2_filter_missing_path.md*
