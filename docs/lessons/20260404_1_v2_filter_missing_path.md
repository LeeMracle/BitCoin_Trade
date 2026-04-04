# v2 필터가 실제 매수 경로에 미적용 (진입 경로 불일치)

- **발생일**: 2026-04-04
- **심각도**: HIGH
- **카테고리**: 코드 / QA 누락

## 증상

composite v2 필터(F&G 게이트, BTC SMA 필터)를 구현하여 배포했으나, 서버 로그에 필터 관련 출력이 없었음. 확인 결과 **필터가 실제 매수 판단에 적용되지 않고 있었음**.

## 원인

이 프로젝트에는 **매수 신호 판단 경로가 2개** 존재:

1. `services/execution/scanner.py` → `scan_entry_signals()` — 일배치/스캔 모드
2. `services/execution/realtime_monitor.py` → `_execute_buy()` ← **웹소켓 실시간 모드 (실전 사용 중)**

v2 필터(F&G, BTC SMA)는 경로 1(scanner.py)에만 구현되었고, 실제 서버가 사용하는 경로 2(realtime_monitor.py)에는 적용되지 않았음.

```
scanner.py (F&G+BTC 필터 있음)  ← 실전에서 사용 안 함
realtime_monitor.py (필터 없음)  ← 실전에서 사용 중 ★
```

## QA가 못 잡은 이유

1. QA 검증이 "import 성공 + 기본값 호환성 + 단위 테스트"에 한정
2. **통합 테스트(실제 매수 경로 추적)를 하지 않음**
3. scanner.py에 필터가 있으니 "적용됨"으로 착각
4. realtime_monitor.py가 scanner.py를 호출하지 않고 독자적으로 매수 판단한다는 사실을 검증하지 않음

## 수정

`realtime_monitor.py`에 다음 추가:

1. `__init__`에 `self._fg_value`, `self._btc_above_sma` 캐시 변수
2. `_refresh_levels()`에서 F&G/BTC SMA 1회 조회 + 로깅 (`[v2]` 태그)
3. `_execute_buy()`에서 F&G < 20 차단 + BTC < SMA20 알트 차단

## 검증규칙

### 자동 검증 (pre_deploy_check.py)

1. `realtime_monitor.py`의 `_execute_buy` 함수 내에 `fg_value` 또는 `_fg_value` 문자열이 존재하는지 확인
2. `realtime_monitor.py`의 `_execute_buy` 함수 내에 `btc_above_sma` 또는 `_btc_above_sma` 문자열이 존재하는지 확인
3. 전략 필터를 추가할 때 **모든 매수 경로**에 적용했는지 체크리스트 필수

### 수동 검증 (배포 후)

- 서버 로그에 `[v2]` 태그가 찍히는지 확인
- `journalctl -u btc-trader | grep '\[v2\]'`

## 교훈

**전략 필터는 "전략 코드"가 아니라 "매수 실행 직전"에 적용해야 한다.** 이 프로젝트처럼 진입 경로가 여러 개(scanner, realtime_monitor)인 경우, 한 곳에만 넣으면 다른 경로가 뚫린다. 필터는 가장 마지막 관문(`_execute_buy`)에 넣는 것이 안전하다.
