# ONG 단일 종목 -29.21% 대손실 — trail_stop 폭이 과도하게 넓음

- **발생일**: 2026-04-04 ~ 2026-04-06
- **발견일**: 2026-04-08
- **심각도**: HIGH
- **카테고리**: 전략 / 리스크 관리

## 증상

ONG/KRW 매매 로그:
- 진입: 2026-04-04 02:57, price 178
- 청산: 2026-04-06 23:08, price 126 (**-29.21%**)

서킷브레이커 발동(2026-04-05 21:01, 계좌 -29.17%)의 주요 기여 종목.

## 초기 가설 — "trail_stop이 작동 안 했다"

→ **틀림**.

## 실제 원인 — "trail_stop은 정상 작동, 단 스탑이 너무 낮았다"

서버 로그(`journalctl -u btc-trader.service`)에서 결정적 한 줄:

```
Apr 07 00:00:01  *** ONG/KRW 스탑 이탈! 가격: 126  스탑: 127 ***
```

trail_stop이 127에 설정되어 있었고, 가격이 127 아래로 내려가자 정확히 매도 발동. **로직은 완벽히 작동**.

진짜 문제는 **스탑이 127이었다는 것 자체**:
- 진입가 178, 스탑 127 → **스탑폭 51 KRW = -28.7%**
- 즉, 진입 직후 생성된 스탑이 이미 -28.7% 손실을 용인하는 위치

### 공식 추적

```python
trail_stop = price - level["atr"] * ATR_MULTIPLIER
           = 178 - ATR * 3
           = 127
```

→ ATR ≈ 17, **ATR/price ≈ 9.5%**. ATR_MULTIPLIER=3 이므로 스탑폭 = 28.5% of entry.

### 타 종목 비교 (동일 공식, 저변동 알트)

| 종목 | entry | trail_stop | 스탑폭 |
|---|---:|---:|---:|
| TRX | 484 | 451 | -6.8% |
| RVN | 8.67 | 7.51 | -13.4% |
| CHZ | 63.8 | 54.81 | -14.1% |
| **ONG** | **178** | **127** | **-28.7%** |

## 뿌리 원인

**Composite DC20+ATR*3 전략이 BTC 등 저변동 대형 코인 기준으로 튜닝되어, 고변동 소형 알트에 적용하면 스탑폭이 통제 불가능하게 벌어진다.**

ATR_MULTIPLIER=3은 변동성의 3배를 스탑으로 잡는 의미인데, 변동성이 가격의 9%면 스탑은 27%가 된다. 이는 "손실 제한 장치"라는 trail_stop의 본래 목적을 벗어난다.

## 수정

### 1. 하드 손절 캡 (`HARD_STOP_LOSS_PCT = 0.10`)

`services/execution/config.py`에 상수 추가:
```python
HARD_STOP_LOSS_PCT = 0.10  # 단일 포지션 최대 손실률 (10%)
```

`services/execution/realtime_monitor.py`의 3개 지점에 적용:

**(a) 진입 시 초기 trail_stop 계산** (`_execute_buy` 경로):
```python
atr_stop = price - level["atr"] * ATR_MULTIPLIER
hard_floor = price * (1 - HARD_STOP_LOSS_PCT)
trail_stop = max(atr_stop, hard_floor)   # 아래로 내려가는 것을 막음
```

**(b) 고점 갱신 시 trail_stop 상승 경로**:
```python
hard_floor = pos["entry_price"] * (1 - HARD_STOP_LOSS_PCT)
new_stop = price - atr_val * ATR_MULTIPLIER
pos["trail_stop"] = max(new_stop, hard_floor)
```

**(c) `_refresh_levels`의 정기 갱신 경로** — 동일 패턴.

### 2. 변동성 필터 (`MAX_ATR_PCT = 0.08`)

진입 전 ATR/price 비율 체크. 8% 초과 종목은 **진입 자체를 차단**:
```python
if level.get("atr"):
    atr_pct = level["atr"] / price
    if atr_pct > MAX_ATR_PCT:
        print(f"  [{symbol}] ATR 필터 — ATR/price={atr_pct:.1%} 차단")
        return
```

→ ONG 같은 극단적 변동성 알트를 입구에서 거름.

### 3. 결과

ONG 시나리오 재현 시:
- 변동성 필터(ATR/price 9.5% > 8%)에서 **진입 차단** (1차 방어)
- 가정법: 만약 진입했다 해도 trail_stop = max(127, 178 × 0.9) = **160** → 최대 -10% 손실로 제한 (2차 방어)

## 검증 규칙

1. `config.py`에 `HARD_STOP_LOSS_PCT`, `MAX_ATR_PCT` 상수 존재
2. `realtime_monitor.py`의 모든 trail_stop 계산 지점이 `max(..., hard_floor)` 경유
3. 진입 시 ATR 필터 통과 로그 출력 (`ATR 필터` 문자열 포함)
4. 단위 테스트 (후속): `tests/execution/test_hard_stop_cap.py` — ATR이 과도한 케이스와 정상 케이스 각각 검증

## 교훈

1. **"자동 손절이 작동했는가"와 "손절이 효과적이었는가"는 완전히 다른 질문**. 로직 디버깅 전에 로그 한 줄 먼저 확인할 것.
2. **ATR 기반 스탑은 변동성에 선형 비례** — 고변동 종목에서 제어 불능. 하드 캡이 반드시 필요.
3. **ATR*3 = 변동성의 3배 = 포지션의 25~30%** 가 될 수 있다는 사실을 설계 시점에 고려 못 함. 범용 전략은 저변동~고변동 전체 스펙트럼에서 안전해야 함.
4. **"BTC 튜닝을 알트에 재사용"은 명시적 검증 없이는 금지**. 현재 composite는 DC20+ATR*3이 BTC 최적화인데, 알트에도 같이 적용되고 있었음. 별도 파라미터 세트 또는 하드 캡이 필수.

## 관련 참조

- [lessons/20260408_3_cb_existing_positions_policy.md](20260408_3_cb_existing_positions_policy.md) — 서킷브레이커 정책
- [docs/lint_layer.md](../lint_layer.md) — 코드 품질 방어선
