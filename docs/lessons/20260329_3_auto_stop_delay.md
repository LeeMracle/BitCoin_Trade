# 5연패 자동 중단 미작동 (체크 주기 문제)

- **발생일**: 2026-03-29
- **심각도**: MEDIUM
- **카테고리**: 실행

## 증상

검증 플랜에 "5건 연속 손실 시 즉시 중단" 기준을 설정했으나,
4시간 보고 주기에만 연패를 체크 → 4시간 사이에 6연패 발생 후에야 중단.

## 원인

- `realtime_monitor.py`의 연패 체크가 보고 주기(4시간)에만 실행
- 거래 체결 즉시 연패 카운트를 확인하지 않음

## 수정

`services/execution/trader.py` — 매 거래 체결 시 연패 카운트 확인 로직 추가:
```python
# 체결 후 즉시 연패 체크
if self.consecutive_losses >= self.max_consecutive_losses:
    self.emergency_stop("연패 한도 초과")
```

## 검증규칙

- `trader.py`에서 체결 콜백 내 연패 체크 로직 존재 확인
- `max_consecutive_losses` 설정값이 config에 존재하는지 확인

## 교훈

안전장치(자동 중단)는 "체크 시점"이 핵심이다.
주기적 체크가 아닌 이벤트 기반(체결 즉시) 체크여야 실질적 보호가 된다.
