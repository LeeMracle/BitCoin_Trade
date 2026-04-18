# async def 내부에서 동기 ccxt 객체 호출 금지 (이벤트 루프 블로킹)

- **발생일**: 2026-04-17 (린트 R6 도입으로 발견)
- **심각도**: MEDIUM
- **카테고리**: 비동기/동시성

## 증상

`services/execution/realtime_monitor.py` 및 `services/execution/multi_trader.py`의 async 함수 내부에서 `ccxt.upbit(...)` (동기 객체) 의 `fetch_balance()` / `fetch_ticker()` / `fetch_tickers()` 를 `await` 없이 직접 호출.

```python
async def _hourly_sync(self):
    exchange = _create_exchange()       # ccxt.upbit (동기)
    raw_balance = exchange.fetch_balance()   # ← blocking I/O, 루프 멈춤
```

## 영향

- HTTP 타임아웃 또는 느린 응답 시 **전체 이벤트 루프(웹소켓 tick 처리 포함)가 블록**
- 최악의 경우 heartbeat touch도 지연되어 watchdog 오발동 가능
- 로그 수신 지연으로 추가 알림/차단 결정 지연

## 수정 방향

1. 단기(권장): `await asyncio.to_thread(exchange.fetch_balance)` 로 감싸서 기본 스레드풀에 넘김
2. 중기: `ccxt.async_support.upbit(...)` 로 전환 + 정식 `await ex.fetch_balance()` 사용
3. 장기: 빈도 높은 쿼리(잔고/시세)는 캐시 레이어(TTL) 추가

## 탐지된 위치 (2026-04-17 기준)

- services/execution/multi_trader.py:118 — `bal = ex.fetch_balance()`
- services/execution/realtime_monitor.py:180 — `raw_balance = exchange.fetch_balance()` (_hourly_sync)
- services/execution/realtime_monitor.py:201 — `tickers = exchange.fetch_tickers(...)` (_hourly_sync)
- services/execution/realtime_monitor.py:536 — `.fetch_ticker(...)` (레벨 재계산 경로)
- services/execution/realtime_monitor.py:1585 — `.fetch_balance(...)` (주기 보고)

## 검증 규칙

1. `scripts/lint_none_format.py` **R6** — async def 내부에서 ccxt 비동기 엔드포인트(fetch_*, create_order) 를 await 없이 호출하면 WARN
2. 위 5건은 코드 수정(PR) 후 WARN → 0건 목표. ERROR 승급은 수정 완료 후 후속 PR.

## 교훈

async def 안에서 동기 I/O 호출은 "조용한 블로킹"이다. await/to_thread로 명시적으로 비동기 경로로 옮겨야 이벤트 루프가 실제로 동시성을 얻는다.
