# lessons #45 — 포지션 종료가 TP 루프 안에 있어 도달 불가능: 유령 포지션 + 승리 거래 표본 소실

- **일자**: 2026-08-24
- **분류**: P1 (실행 오류) — 슬롯 영구 점유 + 검증 표본 오염
- **관련**: lessons #28(잔고 0 자동정리), #6(모든 경로), #19(자체정의 금지), #43(체결가 확정),
  ADR 20260823-1(검증 기준선 재설정)

---

## 1. 증상

`SPK/KRW`가 2026-08-23 14:03 TP2에서 **전량 매도(+3,013원)** 되었는데도
`state["positions"]`에 하루 넘게 남아 있었다.

| | state | 거래소 실측 |
|---|---|---|
| SPK 보유수량 | 포지션 존재, `remaining_qty: 0.0`, `tp_sold_levels: [0,1]` | **0** |

결과 3가지:

1. 5슬롯 중 1칸을 영구 점유 → 신규 매수 차단 (표시상 5/5, 실제 4개)
2. `closed_trades`에 기록되지 않음 → **ADR 20260823-1의 검증 표본이 0에서 안 늘어남**
3. 일일보고가 5종목 보유 + SPK "+27.7%"(수량 0에 대한 평가손익)로 잘못 표시

---

## 2. 원인

`services/execution/realtime_monitor.py::_check_tp_levels`

```python
for idx, tp in enumerate(TP_LEVELS):
    if idx in sold_levels:      # [0,1] 완료 → 매 반복 continue
        continue
    if ret_pct < tp["trigger_pct"]:
        continue
    ...
        if cur_total <= 0:      # ← 잔고 0 자동정리 (lessons #28)
            # closed_trades 기록 + positions 제거   ← 여기까지 도달 못 함
```

**잔고 0 자동정리(lessons #28)가 TP 루프 안에 있었다.** 모든 TP 단계가 완료되면
루프가 전부 `continue`로 빠져나가므로 종료 코드가 **구조적으로 도달 불가능**해진다.

핵심은 관심사 혼재다 — `_check_tp_levels`는 "부분매도"를 하는 함수인데
"포지션 생애주기 종료"까지 그 안에 얹혀 있었다. 전자의 정상 흐름(전 단계 완료 =
더 팔 것 없음 = `continue`)이 후자를 정확히 무력화한다.

### 왜 늦게 발견됐나

이 버그는 **이익으로 청산된 거래에서만** 발생한다. 손절은 `_execute_sell`이
별도로 처리해 정상 제거된다. 즉 **잘 된 거래일수록 기록이 남지 않는다** —
성과 통계가 아래쪽으로 편향되고, 표본은 늘지 않는다.

---

## 3. 함께 드러난 결함 3건

### (a) `_execute_sell`의 조용한 삭제

```python
if total_amount <= 0:
    print(f"  {symbol} 잔고 없음 (total=0)")
    del positions[symbol]      # ← closed_trades 기록 없이 삭제
```

완료된 거래가 통계에서 사라지는 **두 번째 경로**였다.

### (b) `return_pct`가 마지막 체결가만 반영 — 부분 익절 이익 소실

`_execute_sell`은 `ret_pct = (exec_price / entry_price - 1) * 100`으로 기록한다.
TP1에서 +5%에 절반을 실현한 뒤 +1%에 트레일링스탑으로 이탈하면 **`+1%`로 기록**된다.
실제 성과는 `(0.5 × 5%) + (0.5 × 1%) = +3%`.

ADR 20260823-1의 판정 지표(승률 48% / 평균 +0.75%)가 이 값 위에 서 있으므로
**판정 자체가 부정확**해진다. `multi_trader.run_daily_cycle`에도 같은 산식이 있었다(교훈 #6).

### (c) TP 매도 직후 `remaining_qty`가 매도 전 값으로 기록

실측: JUP `remaining_qty` 174.99 (거래소 87.50), TAIKO 470.86 (거래소 233.25).
업비트 잔고 API가 체결 직후 **정산 전 값**을 돌려준 것. POL은 정상이라 간헐적이다.

거래 안전에는 영향 없다 — 실제 매도는 매번 라이브 `fetch_balance`의 `free`를 쓴다.
보고/정합 감시용 값만 오염된다(lessons #10 "state는 거래소 미러" 위배).

---

## 4. 수정

### 종료를 부분매도와 분리 — `services/execution/position_pnl.py` 신설

매도 경로가 셋(`_check_tp_levels`, `_execute_sell`, `multi_trader.run_daily_cycle`)이라
손익 산식을 중립 모듈에 두고 공유한다. `multi_trader`를 `realtime_monitor`가
import하므로 역방향 import가 불가능해서 별도 모듈이 필요했다.

- `record_realized(pos, exec_price, qty)` — 매도 1건 순손익(편도 수수료 2회)을 누적
- `position_return_pct(pos, fallback_price)` — 실현손익 합계 ÷ 진입금 (money-weighted)

### `realtime_monitor.py`

| 위치 | 변경 |
|---|---|
| `_close_position()` 신설 | closed_trades 기록 + positions 제거 + save_state **단일 경로** |
| `_close_if_liquidated()` 신설 | TP 전 단계 완료 포지션의 실잔고 확인 후 종료 (안전망) |
| `_check_tp_levels` 루프 **앞** | 전 단계 완료 시 `_close_if_liquidated` 호출 후 return |
| `_check_tp_levels` 루프 **안** | 매도 후 잔량 < `POSITION_DUST_KRW` 면 즉시 종료 (**근본 경로**) |
| `_execute_sell` 잔고 0 | `del` → `_close_position(reason="auto_cleanup_zero_balance")` |
| `_execute_sell` 정상 | 최종 매도분 `record_realized` 후 `_close_position`이 return_pct 산정 |
| TP 후 `remaining_qty` | 조회값이 매도분만큼 줄지 않았으면 `cur_total - sell_qty` 계산값 사용 |

`config.py`: `FEE_RATE = 0.0005`, `POSITION_DUST_KRW = 5_000` 추가
(교훈 #19 — 백테스트 스크립트들이 `FEE`를 각자 정의하고 있었다).

### 기존 포지션 보정 — `scripts/backfill_realized_pl.py`

`realized_pl_krw` 누적은 배포 시점부터 시작되므로, **이미 TP를 판** 4개 포지션은
보정하지 않으면 새 코드가 오히려 더 부정확해진다(TP 이익 누락 + 분모는 전액).

`journalctl -u btc-trader | grep '부분 익절 체결'`의 실측 gross 손익으로 backfill.
체결가는 `entry_price + gross_pl / sold_qty`로 역산 — SPK TP2로 검산:
`(27.1 - 24.2) × 1038.84811162 = 3012.66` = `daily_pl_state.json` 기록과 일치.

> 이 역산은 `sold_qty` 오차에 강건하다. `(exec - entry) × qty = gross`가 항상 성립하므로
> qty를 틀리게 잡아도 순손익은 수수료 항(≈0.1%)만 흔들린다.

---

## 5. 검증규칙 — `check_position_close_outside_tp_loop()`

**AST 기반**으로 작성했다. 정규식/문자열 포함 검사는 이 프로젝트에서 자기 주석에
3회 속았다(직전 세션 인수인계 §6). 주석과 docstring은 AST에 존재하지 않는다.

1. `_check_tp_levels` 본문에서 TP 루프(`For`)보다 **앞선 문장**에 종료 호출이 있는가
2. 루프 안에서 `sell_market_coin`보다 **뒤쪽 줄**에 종료 호출이 있는가 (lineno 비교)
3. `_execute_sell`에 `ast.Delete`(직접 `del`)가 남아 있지 않은가

### 역방향 테스트가 룰의 결함을 잡았다

초안 2번 규칙은 "루프 안에 종료 호출이 있는가"였는데, 루프 안에는 잔고 0 자동정리용
`_close_position`이 **따로** 있어서 매도 직후 종료가 통째로 사라져도 통과했다.
5개 변이 케이스 중 이 1건이 `FAIL`로 나와 드러났고, `sell_market_coin` 이후 줄 번호
비교로 바꿔 해결했다.

| 변이 | 기대 | 결과 |
|---|---|---|
| 현재 코드 (정상) | 0건 | PASS |
| 루프 앞 종료 가드 삭제 (원 버그) | ≥1건 | PASS |
| 루프 안 종료 확정 삭제 | ≥1건 | **초안 FAIL → 수정 후 PASS** |
| 종료 호출이 주석/문자열에만 존재 | ≥1건 | PASS |
| `_execute_sell` 조용한 삭제 부활 | ≥1건 | PASS |

---

## 6. 교훈

1. **생애주기 종료를 반복 처리 루프 안에 두지 말 것.** 루프의 정상 종료 조건
   (`전부 처리됨 → continue`)이 그 안의 다른 관심사를 정확히 무력화한다.
   `_check_tp_levels`는 "부분매도"만 해야 하고, "종료"는 별도 경로여야 한다.
2. **버그가 승자에게만 발생하면 통계는 조용히 편향된다.** "표본이 안 쌓인다"는
   레짐 탓으로만 보였지만, 실제로는 이긴 거래가 기록에서 빠지고 있었다.
3. **동일 계산이 3개 경로에 흩어져 있으면 반드시 갈라진다**(교훈 #19의 재확인).
   순환 import 때문에 공유가 불가능하면 그건 모듈을 하나 더 만들라는 신호다.
4. **검증 룰은 통과가 아니라 실패를 잡는지로 검증한다.** 이번에도 역방향 테스트에서만
   룰의 결함이 드러났다. 정적 검사는 AST로 — 문자열 검사는 자기 주석에 속는다.
