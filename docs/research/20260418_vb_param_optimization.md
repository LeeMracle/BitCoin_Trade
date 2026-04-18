# P5-02 VB 파라미터 최적화 — 실행 대기 설계서

- **작성일(KST)**: 2026-04-18
- **작성자**: 자비스 (PDCA Builder)
- **상태**: 실행 대기 — 착수 트리거 미충족 (하락장 지속)
- **관련 WBS**: P5-02
- **관련 보고서**: [workspace/reports/20260417_vb_drymake_7day_recheck.md](../../workspace/reports/20260417_vb_drymake_7day_recheck.md)

---

## 1. 배경

2026-04-18 기준, P5-28 개선(하락장 필터 A~E) 적용 후 7일 재검증(2026-04-10~16)이 거래 건수 0건으로 **CONDITIONAL(샘플 부족)** 판정으로 종료되었다.

- **원인**: BTC가 EMA200 아래에서 7일 연속 유지 → 개선 A(하락장 필터)가 의도대로 진입을 전면 차단.
- **결론**: VB 모듈은 고장이 아닌 대기 상태. 파라미터 최적화는 **상승장 복귀 후 거래 데이터가 재축적된 뒤** 착수.
- **이 문서의 목적**: 상승장 복귀 시 즉시 착수할 수 있도록 탐색 그리드, 백테스트 설계, 성공 기준을 미리 정의한다.

---

## 2. 현재 파라미터

`services/execution/config.py` 기준 (2026-04-18):

| 파라미터 | 변수명 | 현재값 | 설명 |
|----------|--------|--------|------|
| K (상승장) | `VB_K_BULL` | 0.4 | 변동성 돌파 계수 — 상승 레짐 |
| K (중립) | `VB_K_NEUTRAL` | 0.5 | 변동성 돌파 계수 — 중립 레짐 |
| K (하락장) | `VB_K_BEAR` | 0.7 | 변동성 돌파 계수 — 하락 레짐 |
| K (극공포) | `VB_K_CRISIS` | 0.85 | 변동성 돌파 계수 — F&G 20~30 구간 |
| 손절 비율 | `VB_SL_PCT` | 0.020 | 진입가 대비 -2.0% 손절 |
| 레짐 판별 SMA | `VB_SMA_PERIOD` | 50 | 레짐 판단용 SMA 기간 |
| 최대 슬롯 | `VB_MAX_POSITIONS` | 3 | 동시 보유 최대 종목 수 |
| 포지션 비율 | `VB_POSITION_RATIO` | 0.30 | 가용 현금 중 VB 할당 비율 |
| 최소 거래량 | `MIN_VOLUME_KRW` (공유) | 500,000,000 KRW | 24h 최소 거래대금 |

> **주의**: `MIN_VOLUME_KRW` 는 composite 전략과 공유된 파라미터이므로 VB 단독 최적화 시 별도 변수로 분리가 필요하다.

---

## 3. 탐색 그리드

### 3.1 핵심 파라미터 그리드

| 파라미터 | 탐색 값 | 현재값 | 비고 |
|----------|---------|--------|------|
| VB_K (상승장 기준) | 0.5, 0.6, **0.7**, 0.85 (현재), 1.0 | 0.4 | 보수화 방향 탐색 |
| VB_SL_PCT | 1.5%, **2.0%** (현재), 2.5%, 3.0% | 2.0% | 손절폭 확대/축소 효과 |
| volume_min (배수) | 기본(1x), 2x, 3x | 1x | 저유동성 종목 필터 강화 |

> K 값 5개 × SL 4개 × volume_min 3개 = **60 조합**
> 실용적 범위로 축소 시: K {0.5, 0.7, 0.85} × SL {1.5%, 2.0%, 2.5%} × vol {1x, 2x} = **18 조합**

### 3.2 보조 파라미터 (2차 탐색)

| 파라미터 | 탐색 값 | 비고 |
|----------|---------|------|
| VB_MAX_WEEKLY_PER_SYMBOL | 2, 3 (현재), 5 | 과매매 방지 vs 기회 포착 |
| VB_LOSS_COOLDOWN_N | 2, 3 (현재) | 연패 쿨다운 민감도 |
| VB_SMA_PERIOD | 20, 50 (현재) | 레짐 판단 속도 |

---

## 4. 백테스트 설계

### 4.1 대상 기간

| 구간 | 기간 | 목적 |
|------|------|------|
| 전체 (in-sample) | 2025-10-01 ~ 2026-03-31 (6개월) | 최적화 기간 |
| 상승장 검증 | 2025-10-01 ~ 2025-12-31 | 상승 레짐 성과 확인 |
| 하락장 검증 | 2026-01-01 ~ 2026-03-31 | 하락 레짐 과최적화 방지 |
| out-of-sample | 2026-04-01 ~ 착수 시점 | 실주행 병행 기간 |

> [lessons/20260329_2](../lessons/20260329_2_backtest_period_bias.md): 상승장 비중 높은 기간만으로 최적화 시 하락장 과대평가 위험 — 상승장/하락장 구간 별도 검증 필수.

### 4.2 in-sample / out-of-sample 분할

- in-sample: 전체 기간의 앞 70% (4.2개월) — 파라미터 탐색
- out-of-sample: 나머지 30% (1.8개월) — 최적 파라미터 검증
- 과최적화 방지: OOS 샤프가 IS 샤프의 70% 이상이어야 채택

### 4.3 평가 지표

| 지표 | 채택 최소 기준 | 참고 |
|------|--------------|------|
| 승률 | ≥ 40% | P4-06 기존 기준 |
| 평균 수익률 | ≥ +1.0% | 수수료(0.05%×2) 후 |
| MDD | ≥ -10% | (하락 손실 상한) |
| 샤프 (OOS) | ≥ 0.5 | 리스크 조정 수익 |

---

## 5. 실행 절차

### 5.1 사전 확인

1. `backtest/engine.py` 에 VB 전략 어댑터 존재 여부 확인
   - 있으면: 기존 어댑터의 K/SL 파라미터를 그리드 탐색에 노출
   - 없으면: `strategies/advanced.py` 의 VB 로직(`_calc_vb_signal` 등)을 백테스트 어댑터에 주입
2. 1분봉 데이터 수집 불필요 — VB는 일봉 기준 (전일 고가 + K × ATR/Range)
3. 수수료 가정: 0.05% taker × 2 = 0.10% 왕복

### 5.2 탐색 실행 (의사코드)

```python
from itertools import product
import backtest.engine as bt  # 또는 어댑터 임포트

GRID = {
    "k_bull":   [0.5, 0.7, 0.85, 1.0],
    "sl_pct":   [0.015, 0.020, 0.025, 0.030],
    "vol_mult": [1, 2, 3],
}

results = []
for k, sl, vol in product(GRID["k_bull"], GRID["sl_pct"], GRID["vol_mult"]):
    res = bt.run_vb_backtest(
        start="2025-10-01", end="2026-03-31",
        k_bull=k, sl_pct=sl, vol_min_mult=vol,
    )
    results.append({"k": k, "sl": sl, "vol": vol, **res})

# IS 기준 정렬, OOS 검증 후 최종 선택
df = pd.DataFrame(results).sort_values("oos_sharpe", ascending=False)
```

### 5.3 결과 저장 위치

- 탐색 결과 전체: `workspace/runs/vb_param_grid_YYYYMMDD.csv`
- 최적 파라미터 선정 보고서: `workspace/reports/vb_param_result_YYYYMMDD.md`
- config.py 반영: `services/execution/config.py` — VB_K_BULL 등 수정

---

## 6. 성공 / 실패 기준

### 6.1 성공 기준 (config.py 반영 가능)

아래 조건을 **모두** 충족하는 파라미터 조합이 OOS에서 존재:

1. OOS 승률 ≥ 40%
2. OOS 평균 수익률 ≥ +1.0%
3. OOS MDD ≥ -10%
4. OOS 샤프 ≥ 0.5
5. OOS 샤프 ≥ IS 샤프 × 0.70 (과최적화 방지)

### 6.2 조건부 채택 (2주 추가 DRY-RUN)

- OOS 승률 36~39% 또는 OOS MDD -10 ~ -12%

### 6.3 실패 기준 (파라미터 변경 불채택)

- 어떤 조합도 OOS 승률 < 36% AND 평균 수익률 < 0 → 현행 파라미터 유지 또는 VB 전략 재설계

---

## 7. 착수 트리거

아래 **두 조건을 모두** 충족할 때 착수:

1. **BTC 상승장 복귀**: BTC/KRW 종가 > EMA(200) 상태가 3일 연속 유지
2. **VB 거래 누적**: `vb_state.json` 의 `history` 배열에 상승장 기간 신규 거래 **15건 이상** 축적

**자동 감지 방법**:

```python
# scripts/check_vb_ready.py (착수 트리거 확인 용도)
import json, pathlib
from datetime import datetime

state = json.loads(pathlib.Path("workspace/vb_state.json").read_text("utf-8"))
trades_after = [
    t for t in state.get("history", [])
    if t.get("entry_ts", "") >= "2026-04-18"   # 상승장 복귀 이후
]
print(f"상승장 복귀 이후 VB 거래 수: {len(trades_after)} / 15")
print("착수 가능" if len(trades_after) >= 15 else "대기 중")
```

---

## 8. 주의사항

- **교차검증 필수**: 최적화 결과는 별도 세션 또는 QA 에이전트로 교차검증. 동일 세션 자기 PASS 금지 ([cross_review_policy.md](../cross_review_policy.md)).
- **config.py 반영 시 동기화**: CLAUDE.md ↔ config.py ↔ 서버 파라미터 3방향 동기화 ([lessons/20260331_1](../lessons/20260331_1_dc_strategy_mismatch.md)).
- **하드 스탑 캡 유지**: `HARD_STOP_LOSS_PCT=0.10` 은 VB 최적화와 무관하게 유지 ([lessons/20260408_5](../lessons/20260408_5_ong_wide_stop.md)).
- **실전 반영 전 승인**: Execution Risk Guard 승인 + PM Orchestrator 최종 확인 필수.
