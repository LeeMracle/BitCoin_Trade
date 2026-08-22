# 주문 체결가 대신 신호가가 기록 — 손익·손절선·성과통계 전부 오염

- **발생일**: 2026-08-22 (감지)
- **심각도**: HIGH (진입가/청산가/실현손익/승률 통계 전반 부정확)
- **카테고리**: 거래소 API 계약 / 데이터 정합성

## 증상

상태 파일의 진입가·수량이 거래소 실체와 다르다.

| 종목 | 상태파일 `entry_price` / `entry_qty` | 거래소 실제 | 오차 |
|---|---|---|---|
| OP | 155.0 / 815.7157 | **157.0 / 805.3244** | -1.3% |
| TAIKO | 108.0 / 475.2153 | 109.0 / 470.8555 | -0.9% |
| SPK | 24.1 / 2095.0104 | 24.2 / 2086.3534 | -0.4% |

기록된 값은 전부 **돌파 감지 시점의 신호가**였다.

## 원인

`multi_trader.py::buy_market_coin()` / `sell_market_coin()`:

```python
order = exchange.create_market_buy_order(symbol, None, params={"cost": amount_krw})
return {"id": order.get("id"), "price": order.get("average") or order.get("price"), ...}
```

`average or price`로 이미 방어한 것처럼 보이지만, **업비트 `create_market_*_order`
응답에는 체결 정보가 아예 없다.**

```
create 응답: average=None, price=None, filled=None, cost=None, status='wait'
```

따라서 `price`가 `None`이 되고, 호출부의

```python
exec_price = order.get("price") or price   # ← price = 신호가
```

폴백이 항상 발동해 **신호가가 체결가로 기록**됐다.

### 왜 발견이 늦었나 — 업비트의 두 가지 함정

1. **시장가 매수는 `canceled` 상태로 끝난다.** 주문한 KRW 중 최소 주문 단위 미만의
   잔액이 환불되면서 주문이 `cancel` 처리된다. 체결 실패가 아니다.
   → `status`로 성공을 판정하면 정상 체결을 실패로 오판한다.
2. **그래서 `fetch_closed_orders`에 매수 주문이 잡히지 않는다.** 조사 중
   "TAIKO 매수 주문 0건"이 나와 한참 헤맸다. `fetch_canceled_orders`로 봐야 나온다.

또한 ccxt의 `fetchMyTrades`는 업비트 미지원(`NotSupported`)이라
체결 내역을 직접 가져올 수도 없다. **`fetch_order(uuid)` 재조회가 유일한 경로**다.

### 실측 확인

```
create 응답 기준 체결가 : None          ← 그래서 신호가 155로 폴백
settle_order 후 체결가  : 157.0
  filled = 805.3243815 | cost = 126435.93 | status = canceled
```

## 영향 범위

| 경로 | 오염 대상 |
|---|---|
| 매수 | `entry_price`, `entry_qty`, `entry_amount_krw`, **하드 손절선** |
| 매도 | `exit_price`, `return_pct`, 실현손익(`daily_pl`), `closed_trades` |

`closed_trades`는 승률·평균수익률·연패 판정의 입력이다.
즉 **"승률 19% / 평균 -6.3%"라는 성과 통계 자체가 부정확한 값 위에서 계산돼 왔다.**

손절선도 어긋난다. OP 기준 하드 캡(-10%)은 `157 × 0.9 = 141.3`이어야 하는데
신호가 기준으로 `155 × 0.9 = 139.5`가 잡혀 **정책보다 1.3% 깊은 손절선**이 된다.

## 수정

`upbit_client.py`에 공용 헬퍼 신설 (매수·매도 양쪽에서 사용 — 교훈 #6):

```python
def settle_order(exchange, order, symbol):
    """fetch_order 재조회로 확정 체결가/수량을 얻는다."""
    for attempt in range(ORDER_SETTLE_RETRIES):
        d = _retry_on_429(exchange.fetch_order, oid, symbol)
        if float(d.get("filled") or 0) > 0:   # ← status 아닌 filled로 판정
            return d
        time.sleep(ORDER_SETTLE_DELAY_SEC)
    return order or {}                         # fail-open: 매매 자체는 막지 않음


def order_exec_price(settled):
    """신뢰도 순: average > cost/filled > price. 없으면 None."""
```

- 성공 판정은 **`filled > 0`** (업비트 매수의 `canceled` 종료 특성 때문).
- 재조회 실패 시 원본 반환 → 호출자의 기존 폴백이 동작(fail-open).
  단 **폴백 시 경고 로그를 남겨** 조용히 신호가가 기록되지 않게 한다.
- 상수는 `config.py`에 정의 후 import (교훈 #19).

부수 보정:
- `entry_amount_krw`를 요청액이 아닌 **실제 체결 대금(`cost`)** 으로 저장
  (TP 잔량 회계 기준이므로 부분 매도 수량이 어긋나지 않게).
- `trail_stop`을 **확정 체결가 기준으로 재계산** (하드 손절 캡 정책 일치).

## 검증규칙 (pre_deploy_check.py)

`check_exec_price_settled()` 신설:

1. `buy_market_coin`/`sell_market_coin`이 체결 확정 경로를 거치는지
2. `settle_order`가 `status`가 아닌 `filled > 0`으로 판정하는지
3. 재시도 상수가 `config.py`에 정의됐는지 (교훈 #19 회귀 차단)

버그 재현본에서 3건 발화 확인. 실거래 주문으로 기능 검증 완료
(create 응답 `None` → `settle_order` 후 `157.0`).

## 교훈

1. **"방어 코드가 있다"와 "방어가 동작한다"는 다르다.** `average or price`는
   방어처럼 보였지만 두 필드 모두 `None`인 상황을 상정하지 않았다.
   폴백 체인은 **모든 항목이 비는 경우**를 반드시 포함해야 한다.
2. **조용한 폴백은 버그를 영구화한다.** 신호가로 폴백하면서 로그 한 줄 남기지
   않았기 때문에 4개월간 아무도 몰랐다. 폴백은 **정상 경로가 아니라 예외**이므로
   반드시 흔적을 남겨야 한다 (교훈 #31·#39 계열).
3. **거래소 API의 "완료" 정의를 문서가 아니라 실측으로 확인하라.** 업비트 시장가
   매수가 `canceled`로 끝난다는 것, `fetch_closed_orders`에 안 잡힌다는 것,
   `fetchMyTrades`가 미지원이라는 것 — 셋 다 코드를 짜기 전에 알기 어렵고,
   실제 주문 객체를 덤프해봐야 드러난다.
4. **주문 생성 응답 ≠ 체결 결과.** 비동기 체결 거래소에서는 생성 응답에
   체결 정보가 없는 것이 오히려 일반적이다. 체결가가 필요하면 **재조회가 원칙**이다.
