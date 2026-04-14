# L15: 시작 시 _refresh_levels() uncaught exception → 프로세스 크래시

## 날짜
2026-04-13

## 증상
- 업비트 정기 점검(04-13 02:00~08:00 KST) 중 btc-trader 서비스의 `start()` → `_refresh_levels()` → `get_krw_market_coins()` → `exchange.load_markets()` 호출에서 API 타임아웃 발생
- 이 예외가 처리되지 않아 프로세스가 크래시 (exit code 1)
- systemd `Restart=always`로 자동 재시작되었으나, 점검 중 **10회 반복 크래시**
- 위치: `realtime_monitor.py:142` → `scanner.py:84`

## 원인
- `start()` 메서드에서 `await self._refresh_levels()`를 try-except 없이 직접 호출
- `_run_websocket()`은 내부에 재연결 로직이 있었으나, 그 이전 단계인 초기 레벨 로딩에는 에러 처리가 없었음

## 수정
- `start()`에서 `_refresh_levels()` 호출을 재시도 루프로 감싸기 (최대 99회, 지수 백오프 5→60초)
- 첫 실패 시 텔레그램 알림 1회 발송
- 프로세스 크래시 대신 대기 후 자동 복구

## 검증규칙
- `start()` 내 `_refresh_levels()` 호출은 반드시 try-except로 감싸야 함
- API 장애 시 프로세스가 크래시하지 않고 재시도해야 함
- pre_deploy_check: `start()` 안에 `_refresh_levels` 호출 시 재시도 루프 또는 try-except 존재 확인

## 교훈
- 외부 API에 의존하는 초기화 로직은 반드시 **재시도 + 백오프 패턴** 적용
- systemd `Restart=always`는 최후 방어선이지, 정상적인 에러 처리를 대체할 수 없음 — 점검 중 크래시 루프는 로그 오염 및 자원 낭비를 초래
- 정기 점검 등 장시간 API 불가 상황에서는 내부 재시도가 크래시 루프보다 효율적
