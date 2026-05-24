# lessons #28: state ↔ exchange 보정은 multi+vb 동시 처리 + 봇 자체 자동 정리 둘 다 필요

- **발생일(KST)**: 2026-05-11
- **분류**: 운영 회귀 / 데이터 정합 (lessons #10 #27 연관)
- **선행 lessons**: #10, #21, #27

## 사건 요약

2026-05-10 22:20 KST에 lessons #10 기반 `fix_state_balance_mismatch.py`로 차집합(`exchange_only={FLOCK}`, `state_only={1INCH}`)을 정리하고 봇 재시작 완료. 그러나 **다음 날 새벽 1:20 / 4:20 / 7:20 KST에 동일한 차집합 알림이 3회 연속 발사**됨 — `State에만 존재: 1INCH/KRW, BONK/KRW`.

진단 결과:

1. **`fix_state.py`가 `multi_trading_state.json`만 보정하고 `vb_state.json`은 미처리** — `realtime_monitor._hourly_sync` 라인 281은 `reloaded_state = composite | vb | ema_coins` 합집합으로 검사. 따라서 vb의 1INCH(entry 148, 5/9 진입)가 영구히 false alarm을 트리거.
2. **봇은 거래소 잔고 0을 명시적으로 인지하지만 자동 정리 코드 없음** — `_check_tp_levels`에서 `cur_total <= 0` 분기는 `print + return`만 수행. BONK는 23:01부터 `잔고 없음 (total=0) — TP 스킵, position 정리 필요` 로그 15회 이상 반복하며 영원히 고아.
3. **TAO도 같은 패턴으로 잠복 중**이었음 — 5/10 22:41 TP2 +679원 체결 후 거래소 잔량 0이 됐지만 state에 잔존, 다음 알람 사이클에서 폭주 예약.

## 원인

1. **fix_state 스크립트의 vb_state 누락**:
   - 코드 내부 코멘트(`DUST_KRW = 5_000  # ... lessons #28 예정`)로 이미 위험 인지되어 있었으나 미작성
   - 어제 정리는 multi의 FLOCK 추가만 효과. vb의 1INCH 제거는 무의미(이미 multi엔 없었음)

2. **TP 트리거 시 잔고 0 = 청산 완료 신호인데 봇이 무시**:
   - "수동 확인 필요" 알림으로 책임 이관 → 잠금 회피 행동 → false alarm 폭주 → 알람 피로 누적

3. **알람 디바운스(3회 누적)는 "동일 signature"가 키** → 종목이 여럿이면 signature가 종목 추가/제거 따라 변경되어 카운터 리셋 → 결국 우연히 같은 조합 3회 도달 시 알람 (이번 케이스)

## 수정 (2026-05-11)

1. **`scripts/fix_state_balance_mismatch.py` 강화**:
   - `VB_STATE_PATH` 추가, `multi_changed` 플래그 도입
   - main 흐름 마지막에 vb 보정 블록 추가 (제거만, add 미실시 — vb는 자체 진입 로직 사용 → 외부 add 시 충돌)
   - 백업도 vb_state.json 동시 생성 (변경 없으면 자동 unlink)
2. **`services/execution/realtime_monitor.py` 자동 정리 로직 추가**:
   - `__init__`에 `self._orphan_seen_count: dict[str, int] = {}` 추가
   - `_check_tp_levels`의 `cur_total <= 0` 분기를 3회 누적 시 자동 정리로 교체:
     - `closed_trades`에 `exit_reason="auto_cleanup_zero_balance"` 등록
     - `state.positions`에서 제거 + `save_state` 호출
     - 카운터 리셋
   - 보수적 3회 누적 — false positive(거래소 API 일시 0 응답) 회피
3. **AWS 배포 + fix_state 재실행**:
   - multi: BONK + TAO 제거 (11→9건)
   - vb: 1INCH 제거 (1→0건)
   - service stop → fix → start (메모리 덮어쓰기 race 방지)
4. **lessons #28 본 문서 작성**

## 검증규칙 (자동화)

`scripts/pre_deploy_check.py`에 추가 권장:

```python
def check_fix_state_covers_vb_state() -> None:
    """fix_state_balance_mismatch.py가 vb_state.json도 처리하는지 grep 검증.
    lessons #28 회귀(vb 누락) 방지.
    """
    p = Path("scripts/fix_state_balance_mismatch.py")
    src = p.read_text(encoding="utf-8")
    assert "VB_STATE_PATH" in src and "vb_state" in src.lower(), \
        "fix_state.py가 vb_state.json을 처리하지 않음 — lessons #28 회귀 위험"


def check_orphan_auto_cleanup_in_realtime_monitor() -> None:
    """realtime_monitor.py가 잔고 0 종목을 자동 정리하는지 검증.
    lessons #28 회귀(고아 state 영구 잔존) 방지.
    """
    p = Path("services/execution/realtime_monitor.py")
    src = p.read_text(encoding="utf-8")
    assert "_orphan_seen_count" in src and "auto_cleanup_zero_balance" in src, \
        "realtime_monitor에 자동 정리 로직 없음 — lessons #28 회귀 위험"
```

## 교훈

- **다중 state 파일 운영 시 보정 도구는 반드시 모든 state 커버 필수** — 누락된 state 파일은 영구 false alarm 발신원
- **봇이 명시적으로 "정리 필요"라고 인지한 상태**는 즉시 자동화 대상 — 사람의 수동 개입 의존은 알람 피로 누적 + 사고 재현
- **알람 디바운스(N회 누적)는 충분조건이 아님** — signature 변경으로 카운터 리셋되어도, 운영 사이클상 결국 같은 조합이 다시 도래 → 디바운스만으로는 false alarm 영구 차단 불가능. **근본 보정**(state 정합 + 자동 정리)이 필수

## 참조

- 코드: `scripts/fix_state_balance_mismatch.py`, `services/execution/realtime_monitor.py:1540`
- 백업: `workspace/multi_trading_state.json.backup_20260510_232531`, `workspace/vb_state.json.backup_20260510_232531`
- 관련 lessons: #10 (state ↔ balance 미러), #27 (좀비 옛코드 알림)
