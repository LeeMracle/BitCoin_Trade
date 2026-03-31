# CLAUDE.md 전략 파라미터와 실서버 불일치 (DC50 vs DC20)

- **발생일**: 2026-03-31
- **심각도**: HIGH
- **카테고리**: 운영

## 증상

CLAUDE.md에는 `DC(50)+ATR(14)x3.0`으로 기재되어 있으나,
AWS 서버 btc-trader.service 로그에는 `Donchian(20) + ATR(14)x3.0`으로 실행 중.

```
전략: composite Donchian(20) + ATR(14)x3.0
```

## 원인

Phase 2 백테스트에서 DC(50)이 OOS 최고 성과였으나,
Phase 4 실전 전환 시 DC(20)으로 변경 결정 (docs/decisions/20260329_daytrading_stop_composite_switch.md).
CLAUDE.md가 업데이트되지 않음.

## 수정

CLAUDE.md Phase 3/4 섹션의 전략 파라미터를 실서버와 일치시킬 것:
- `DC(50)+ATR(14)x3.0` → `DC(20)+ATR(14)x3.0`
- 또는 전략 변경 이력을 명시

## 검증규칙

배포 전 체크:
- `services/execution/config.py`의 DC_PERIOD 값
- `services/.env`의 전략 관련 설정
- CLAUDE.md의 전략 기재 내용
- 세 곳의 값이 모두 일치하는지 확인

## 교훈

문서(CLAUDE.md)와 실행 코드의 전략 파라미터는 반드시 동기화해야 한다.
전략 변경 시 (1) 코드 수정 → (2) CLAUDE.md 업데이트 → (3) 배포를 하나의 작업 단위로 처리할 것.
