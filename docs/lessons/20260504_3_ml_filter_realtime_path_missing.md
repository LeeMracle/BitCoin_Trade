# lessons #26: ML 필터 hook을 multi_trader에만 추가하고 realtime_monitor 누락

- **발생일(KST)**: 2026-05-04
- **분류**: 안전장치 일관성 / 매수 경로 누락
- **선행 lessons**: #6 (전략 필터는 모든 매수 경로에 적용 필수)
- **관련 plan**: [20260504_3_ml_signal_filter](../../workspace/plans/20260504_3_ml_signal_filter.md)

## 사건 요약

ML 신호 필터(MLFilter, fail-open) S6 구현에서 `multi_trader.py` 매수 분기에만 ML gate hook을 삽입하고, **실시간 웹소켓 매수 경로(`realtime_monitor._execute_buy`)는 누락**했다. pdca-qa 교차검증에서 MAJOR 이슈로 발견.

운영 시 `ML_FILTER_ENABLED=1` 활성화하면:
- multi_trader 일일 배치 매수 → ML 필터 적용 ✓
- realtime_monitor 웹소켓 돌파 매수 → **ML 필터 우회 (lessons #6 직접 위배)**

## 원인

1. Plan §4 리스크 항목에 "scanner + realtime_monitor 두 경로 모두 hook 검토 (S6)"로 적시했음에도, S6 구현 단계에서 `multi_trader.py` 한 곳만 수정.
2. 실시간 매수 경로는 별도 함수(`_execute_buy`)로 분리되어 있으며, multi_trader와 호출 흐름이 달라 시각적으로 누락 식별 어려움.
3. pre_deploy_check에 ML hook 존재 여부 검증룰 부재 — 자동 감지 사각지대.

## 수정 (즉시)

1. `services/execution/realtime_monitor.py` 상단 import에 `_get_ml_filter`, `_ml_shadow` 추가
2. `_execute_buy()` 모든 사전 필터(서킷브레이커/F&G/EMA200/거래량/ATR) 통과 후 마지막 게이트로 ML 게이트 삽입
3. `record_block("ml_filter", symbol)` + `throttled_print` 60s 1회 (lessons #14 로그 throttle 준수)
4. `pre_deploy_check.py` `check_ml_filter_integrity()`에 검증룰 추가:
   - `services/execution/multi_trader.py`, `realtime_monitor.py` 두 파일 모두 `_get_ml_filter` 또는 `get_filter` 호출 존재해야 함

## 검증규칙 (자동화)

`scripts/pre_deploy_check.py` `check_ml_filter_integrity()`:

```python
for path_rel in ("services/execution/multi_trader.py", "services/execution/realtime_monitor.py"):
    src = (PROJECT_ROOT / path_rel).read_text(encoding="utf-8")
    if "_get_ml_filter" not in src and "get_filter" not in src:
        errors.append(f"[ML] {path_rel}에 ML 필터 hook 누락 — lessons #6 위배")
```

→ 향후 다른 매수 경로 추가 시 동일 패턴 강제.

## 교훈

1. **"모든 매수 경로"에 적용해야 하는 안전장치는, 추가 시점에 `grep -rn "buy_market_coin\|buy_market"` 등으로 모든 경로를 먼저 열거하고 체크리스트로 만들 것.**
2. **Plan에 "두 경로 모두" 명시했다고 끝이 아니라, 각 경로를 task list 별 항목으로 분리해야 누락 방지.**
3. **fail-open이라도 lessons #6는 위배 — fail-open은 "모델 부재 시 무력화"이지 "일부 경로 누락"의 면책이 아님.**

## 참조
- 위배된 lessons: [#6 모든 매수 경로 필터 적용](20260404_1_v2_filter_missing_path.md)
- 본 ML 시스템 ADR: [decisions/20260504_2_ml_signal_filter.md](../decisions/20260504_2_ml_signal_filter.md)
