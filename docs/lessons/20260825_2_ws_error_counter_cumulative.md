# lessons #47 — "연속 오류"가 실은 누적 오류: 정상 웹소켓 끊김이 봇 자동중지로 번진다

- **일자**: 2026-08-25
- **분류**: P1 (안전장치 오발동) — 실제 고장이 없는데 매매 중단
- **관련**: lessons #42(보유종목 구독 탈락), #30(안전장치 알람 루프), #21(fail-closed 원칙),
  #14(이벤트 루프 로그 throttle)

---

## 1. 증상

텔레그램 알림:

```
⚠ 오류 발생
웹소켓 오류: Cannot write to closing transport
wss://api.upbit.com/websocket/v1
(연속 오류: 1/5)
```

서버 로그 실측 (06:17:34 UTC):

```
06:17:34  웹소켓 오류: Cannot write to closing transport
06:17:34  5초 후 재연결...
06:17:34  구독 완료: 141개 종목      ← 같은 초에 완전 복구
```

**매매 영향은 없었다.** 구독 종목 수도 그대로여서 lessons #42 재발도 아니다.
문제는 이 정상 사건이 **봇 자동중지 카운터를 올린다**는 것이다.

---

## 2. 원인 (두 개가 겹친다)

### (a) 정상 종료를 오류로 분류

업비트는 웹소켓 연결을 주기적으로 정리한다. 그때 aiohttp가 `heartbeat=30` 핑을
**이미 닫히는 중인** 소켓에 쓰면 `ClientConnectionResetError: Cannot write to closing
transport`가 난다. 재연결하면 끝나는 정상 경로다.

같은 사건이 타이밍에 따라 두 얼굴로 나타난다:

| 서버가 먼저 닫음 | 핑이 겹침 |
|---|---|
| `msg.type == CLOSED` → `break` (오류 아님) | 예외 → `_handle_error` (오류 집계) |

### (b) reset이 체결 경로에만 있다 — "연속"이 아니라 "누적"

```
services/execution/realtime_monitor.py:2405  self._reset_errors()   ← _execute_buy 성공
services/execution/realtime_monitor.py:2538  self._reset_errors()   ← _execute_sell 성공
```

이 봇은 레짐 게이트 때문에 **거래가 드물다**. 매매 없는 날에는 카운터가 내려갈 방법이
없으므로, 웹소켓 끊김이 며칠에 걸쳐 5회 쌓이면 `MAX_CONSECUTIVE_ERRORS` 도달 →
`self.running = False` → **고장이 없는데 봇이 멈춘다.**

> 변수명이 `consecutive_errors`라서 "연속으로 5번 실패해야 멈춘다"고 읽히지만,
> 리셋 조건이 희소하면 사실상 **수명 누적 카운터**다. 이름이 설계를 감췄다.

---

## 3. 수정

```python
def is_benign_ws_error(exc) -> bool:
    return isinstance(exc, (
        aiohttp.ClientConnectionError,   # ClientConnectionResetError / ServerDisconnected 포함
        ConnectionResetError,
        asyncio.TimeoutError,
    )) or "closing transport" in str(exc)
```

- **정상 끊김**: 별도 카운터 `_ws_disconnects`(1시간 슬라이딩)로 집계, 로그만.
  **시간당 10회 이상**이면 그때 `ws_flapping`으로 알림 (진짜 이상 신호).
- **예상 밖 예외**(파싱 오류·코드 버그): 종전대로 `consecutive_errors` 집계 → 중지 대상 유지.
- **틱 수신 시 `_reset_errors()`**: 실제 데이터가 들어오면 연결이 살아있다는 뜻이므로
  거래가 없어도 카운터가 해소된다.

분류 동작 검증 6/6 — `closing transport` / `ServerDisconnectedError` / TCP reset /
`asyncio.TimeoutError` → benign, `ValueError`(파싱) / `KeyError`(코드 버그) → 고장.

---

## 4. 검증규칙 (pre_deploy_check)

`check_ws_error_not_cumulative()`:

1. `is_benign_ws_error` **정의** 존재
2. 웹소켓 예외 분기에서 **실제 호출**(`if is_benign_ws_error(`) — 정의만 하고 안 쓰면 사문화(lessons #44 계열)
3. `_run_websocket` 본문에 `self._reset_errors()` 존재 — 체결 없이도 카운터가 해소되는 유일한 경로

역방향 테스트 3/3 적발(정의 제거 / 호출 제거 / reset 제거).

---

## 5. 교훈

1. **안전장치 카운터는 "리셋 조건이 얼마나 자주 성립하는가"까지가 설계다.**
   증가 조건만 보고 임계값을 정하면, 리셋이 희소한 시스템에서 임계값은 결국 도달한다.
2. **정상 운영에서 발생하는 사건을 오류로 세지 않는다.** 세려면 빈도로 센다
   (절대 횟수가 아니라 "시간당 N회").
3. **같은 사건이 타이밍에 따라 다른 형태로 나타나는지 확인한다.**
   여기서는 서버 종료가 `CLOSED` 메시지 또는 예외로 갈렸고, 한쪽만 오류로 집계됐다.
4. lessons #30과 같은 계열 — **안전장치 자체가 오발동원**이 되는 패턴.
