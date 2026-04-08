# ADR — 서킷브레이커 발동 시 기존 포지션 처리 정책

- **번호**: ADR-20260408-1
- **작성일**: 2026-04-08
- **상태**: Accepted
- **관련 lessons**: [20260408_3_cb_existing_positions_policy](../lessons/20260408_3_cb_existing_positions_policy.md)

## 배경

2026-04-05 21:01 UTC, BATA 서킷브레이커(CB)가 발동했다. 계좌 총자산이 초기자본 300,000 KRW 대비 -29.17% (212,497 KRW)까지 하락하여 사전 정의된 임계값 -20%를 초과한 결과다.

**현재 CB 동작**(`services/execution/realtime_monitor.py`):
- 발동 후 **신규 매수는 전 종목 차단**
- **기존 보유 포지션(RVN/TRX/CHZ)은 그대로 유지**
- trail_stop 모니터링은 CB와 무관하게 계속 작동

## 쟁점

CB가 "신규만 차단"인 채로 기존 포지션이 계속 노출되는 것이 적절한가?

3가지 옵션:

### Option A — 현재 동작 유지 (신규만 차단)
- **장점**:
  - 이미 진입한 포지션은 리바운드 기회 보유 — 실제 4/5~4/8 사이 BTC 반등으로 총자산 285,510원까지 +34% 회복됨
  - 강제 청산은 일시적 과매도에서 손실 확정
  - 구현 단순 (현재 상태)
- **단점**:
  - "최악의 경우 -20%에서 멈춘다"는 CB 보장이 깨짐
  - 추가 손실이 이론상 무제한
  - 사용자에게 CB의 보호 효과가 모호함

### Option B — 즉시 전량 청산
- **장점**: 명확한 최대 손실 상한 보장
- **단점**:
  - 일시적 과매도에서 손절 확정
  - 이번 케이스(4/5 발동 → 4/8 회복)에서는 회복분을 날렸을 것
  - 급격한 시장가 매도로 슬리피지 확대

### Option C — 소프트 청산 (손실 중인 포지션만 정리)
- MFE(Maximum Favorable Excursion)가 특정 임계 아래인 포지션 우선 정리
- **장점**: 절충안, 회복 가능성이 있는 포지션은 유지
- **단점**: 구현 복잡도 ↑, 튜닝 대상 증가

## 결정

**Option A 유지 + 2차 안전장치 도입**.

### 근거

1. **실증 데이터**: 이번 사이클(4/5→4/8)에서 Option A는 +34% 회복을 허용. Option B였다면 212k로 확정 손실.
2. **하드 손절 캡 추가**: 오늘 lessons/20260408_5 에서 `HARD_STOP_LOSS_PCT=10%` 개별 포지션 캡을 신설. CB가 없더라도 **개별 포지션 손실이 -10%를 넘지 못한다**. 따라서 CB 발동 후 추가 하락도 포지션당 -10%로 자연스럽게 제한됨.
3. **2차 CB**: 개별 캡에도 불구하고 총자산이 추가 하락할 경우를 대비, **총자산 -25% 시 강제 전량 청산** 트리거를 2차 안전장치로 추가.

### 2차 안전장치 사양

```python
# services/execution/circuit_breaker.py (추가)
CB_L1_THRESHOLD = -0.20   # 1차: 신규 진입 차단
CB_L2_THRESHOLD = -0.25   # 2차: 기존 포지션 전량 청산

def check_and_trigger_l2(total_krw: float) -> bool:
    loss_pct = (total_krw - INITIAL_CAPITAL) / INITIAL_CAPITAL
    if loss_pct <= CB_L2_THRESHOLD and not _l2_triggered():
        _trigger_l2_liquidation()
        return True
    return False
```

트리거 시:
1. 모든 보유 포지션 시장가 전량 청산
2. 텔레그램 경보 + 이유 기록
3. `circuit_breaker_state.json`에 `l2_triggered_at` 기록
4. **수동 해제까지 신규 진입/보조 전략 전부 OFF**

### CB 해제 규칙 (현재 부재 → 이번 ADR에서 결정)

- **1차 해제**: 총자산이 초기자본의 **95% 이상** 회복 시 자동 해제 (현재 212,497 → 285,000 필요)
  - 또는 사용자가 `workspace/circuit_breaker_state.json`에서 `triggered: false` 로 수동 해제
- **2차 해제**: **수동 해제만 허용** (자동 해제 없음)
  - 이유: L2는 명시적 "catastrophic" 시그널, 재발 방지 위해 사용자가 직접 원인 확인 후 해제해야 함

## 구현 범위 (이번 ADR 승인으로 트리거)

- [ ] `services/execution/circuit_breaker.py` — L2 로직 추가
- [ ] `services/execution/realtime_monitor.py` — L2 체크 호출 지점 추가
- [ ] `workspace/circuit_breaker_state.json` 스키마 확장 (`l2_triggered_at`, `auto_resume_at`)
- [ ] L1 자동 해제 로직 (95% 회복)
- [ ] 단위 테스트 `tests/execution/test_circuit_breaker_l2.py`
- [ ] `pre_deploy_check.py` 에 L2 설정 검증 추가
- [ ] 운영 문서 `docs/runbook_circuit_breaker.md` 작성

## 비고

- 본 ADR 결정은 2026-04-08 11개 closed trades 데이터 기준. 추가 운영 데이터가 쌓이면 Option C로 재검토 가능.
- `HARD_STOP_LOSS_PCT` 와 `MAX_ATR_PCT` 는 별도 lesson(20260408_5)에서 이미 반영 완료, 본 ADR의 가정에 포함됨.
- WBS: P4-17 (본 ADR 승인) → P4-18 (구현) → P4-19 (배포 검증).
