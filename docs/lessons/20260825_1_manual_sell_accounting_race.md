# lessons #46 — 수동 매도 회계: 틱 경로가 1시간 교차검증을 앞질러 손익을 왜곡

- **일자**: 2026-08-25
- **분류**: P1 (실행/회계 오류) — 검증 표본 왜곡 + 안전장치 오발동
- **관련**: lessons #45(포지션 종료 단일 경로), #28(잔고 0 자동정리), #25(불변 입력/가변 추적),
  #38(경로 A/B 불일치), #12(NoneType 포매팅), ADR 20260823-1(검증 기준선), ADR 20260825-1(슬롯 확대)

---

## 1. 증상

사용자 지시로 JUP 잔량을 **+7.4%에 수동 매도**했다. 그대로 두면 `closed_trades`에는
**+2.5%**가 기록될 상황이었다 (실제 money-weighted는 **+6.44%**).

또한 하루 뒤 관점에서 두 가지가 더 어긋난다:

| 대상 | 잘못된 값 | 실제 |
|---|---|---|
| `closed_trades.return_pct` | TP1 실현분만 반영 | 수동 매도 대금까지 합산해야 함 |
| `daily_pl_state.json` (3% 한도) | 수동 2건 **미기록** | +1,750 / -5,671 반영 필요 |
| 검증 표본 (30건) | 수동 매도가 전략 성적에 합산 | 전략 청산만 세야 함 |
| 5연패 브레이커 | 수동 손실이 연패로 집계 | 전략 손실만 세야 함 |

---

## 2. 원인 — 더 빠른 경로가 먼저 도착한다

봇에는 수동 매도를 인식하는 정상 경로가 있다: `_close_manually_sold()`가
**1시간 주기** 교차검증에서 `only_state`를 3회 연속 확인하면 거래소 체결 이력으로
실현손익을 재구성한다(lessons #45에서 추가).

문제는 **틱 주기 경로가 훨씬 빠르다**는 것이다.

```python
# _check_tp_levels — 틱마다 실행
if cur_total <= 0:
    cnt = self._orphan_seen_count.get(symbol, 0) + 1
    if cnt < 3: return
    self._close_position(symbol, pos, price, "auto_cleanup_zero_balance")  # ← 몇 분 안에 도달
```

`_close_position`은 `realized_pl_krw` 기준으로 수익률을 계산하는데, **수동 매도 대금은
거기 없다**. 결과적으로 TP 실현분만 반영된 값이 확정된다.

즉 "잔고가 사라진 이유"를 두 경로가 다르게 해석하는데, **틀린 해석 쪽이 항상 먼저 도착한다.**

## 3. 파생 문제 — 개입이 전략 지표에 섞인다

`closed_trades`는 (a) 검증 표본 30건, (b) 5연패 브레이커, (c) 일일보고 승률의
**공통 입력**이다. 여기에 사용자 개입이 섞이면:

- 표본: 유리한 타이밍의 수동 익절이 승률을 부풀린다 (실측: 전략 1건인데 "2건 승률 100%")
- 브레이커: 비중 축소(`weight_trim`) 손실 3건 + 전략 손실 2건 = **연패 5 → 72h 매수 중단**
  (전략은 멀쩡한데 멈춘다)

---

## 4. 수정

1. **`scripts/manual_close_position.py`** — 매도와 회계를 한 트랜잭션으로 묶는다.
   봇 정지 확인(가동 중이면 거부, state 덮어쓰기 방지) → `sell_market_coin`(확정 체결가,
   lessons #43) → `record_realized` 누적 → money-weighted 청산 → `daily_pl.record_sell`.
   `--qty`로 부분 정리 지원, `entry_qty`/`entry_amount_krw`는 불변(lessons #25).
2. **`services/execution/trade_class.py`** — 청산 사유를 strategy/manual/repair/unknown으로
   분류하는 단일 출처. **allowlist**(전략 사유 열거)라서 새 사유는 unknown으로 드러난다.
3. 통계 경로 2곳(`daily_report`, `build_strategy_summary`)과 연패 산정 2곳
   (`check_consec_loss`, `_get_consec_loss`)에 동일 적용.

### unknown 처리를 일부러 다르게 한다

| | 통계(검증 표본) | 연패 브레이커 |
|---|---|---|
| strategy | 포함 | 포함 |
| manual / repair | 제외 | 제외 |
| **unknown** | **제외** | **포함** |

각 경로가 **안전한 쪽으로 틀리도록** 맞춘 것이다 — 통계는 부풀지 않게, 안전장치는
안 걸리지 않게. 새 매도 경로 등록을 잊어도 "성적은 좋아 보이는데 브레이커는 안 걸린다"는
최악 조합이 나오지 않는다.

---

## 5. 검증규칙 (pre_deploy_check)

| 룰 | 검사 |
|---|---|
| `check_exit_reasons_classified()` | 코드의 `_close_position(...)` 마지막 문자열 인자 + `exit_reason` 리터럴이 전부 trade_class에 등록됐는가 / 집계 경로 2곳이 분류기를 **import** 하는가 |
| `check_consec_loss_floor_consistency()` (확장) | 연패 산정 2곳이 `counts_for_consec_loss(` **호출** + `get("consec_loss_floor_date")` **호출** 형태를 갖는가 |

### 역방향 테스트에서 초안이 두 번 깨졌다

1. 단순 정규식이 `rebuilt["last_exec_price"]`의 **딕셔너리 키**를 청산 사유로 오인
   → 괄호 균형을 세어 호출 범위를 확정한 뒤 마지막 리터럴만 취하도록 수정
2. `periodic_analysis.py`의 **주석에 적힌 "trade_class"** 때문에 통과
   → 실제 `from ... import` 문을 요구하도록 수정

기존 lessons #38 룰도 같은 약점이 있었다 — `floor = self.state.get("consec_loss_floor_date")`를
지워도 **바로 위 주석의 이름** 때문에 통과했다. 호출 형태 요구로 함께 강화.
**"주석에 속는 룰"은 이 프로젝트에서 5회째다.**

---

## 6. 교훈

1. **잔고가 사라진 이유를 여러 경로가 해석한다면, 가장 빠른 경로가 진실을 정한다.**
   느리지만 정확한 경로를 추가하는 것으로는 부족하다 — 빠른 경로가 틀린 값을 쓰지 않게 해야 한다.
2. **사람의 개입은 전략 지표에서 분리한다.** 같은 저장소를 쓰되 라벨로 가른다.
   분리하지 않으면 승률도, 안전장치도 둘 다 오염된다.
3. **분류기의 기본값은 "드러나는 쪽"으로.** allowlist는 등록을 잊으면 눈에 띄고,
   blocklist는 등록을 잊으면 조용히 섞인다.
4. **검증 룰은 문자열 포함이 아니라 호출 형태로 검사한다.** 자기 주석에 속는 사례가 반복된다.
