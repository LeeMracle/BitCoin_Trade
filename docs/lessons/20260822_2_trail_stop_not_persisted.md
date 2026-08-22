# 트레일링스탑 고점 갱신이 메모리에만 반영 — 재시작 시 이익 보호 소멸

- **발생일**: 2026-08-22 (감지)
- **심각도**: HIGH (재시작 시 확보 이익 보호 상실, 손실 확대 가능)
- **카테고리**: 상태 영속화 / 안전장치

## 증상

2026-08-22 04:42 UTC 기준 보유 3종목이 모두 +5~7% 상승했음에도
`workspace/multi_trading_state.json`의 트레일스탑이 진입가 -10%에 그대로 멈춰 있었다.

| 종목 | 진입가 | 현재가 | 상태파일 `highest` | 상태파일 `trail_stop` |
|---|---:|---:|---:|---:|
| JUP | 285 | 304 | **285** | 256.5 |
| SPK | 24.1 | 25.7 | **24.2** | 21.69 |
| TAIKO | 108 | 114 | **108** | 97.2 |

상태 파일 최종 수정 시각: `00:04:56` — 진입 직후에서 **4시간 38분간 멈춤**.

이 상태에서 봇이 재시작되면 3종목 모두 트레일스탑이 진입가 -10%로 되돌아가고,
그동안 확보한 +6% 이익 보호가 전부 소멸한다.

## 원인

`services/execution/realtime_monitor.py::_on_ticker()` 고점 갱신 블록:

```python
if price > pos["highest"]:
    pos["highest"] = price
    ...
    pos["trail_stop"] = max(new_stop, hard_floor)
    # ← save_state() 호출 없음
```

`pos`는 `self.state["positions"]`를 참조하므로 **메모리에서는 정상 갱신**되어
실시간 손절 판정(`if price < pos["trail_stop"]`)은 올바르게 동작한다.
그러나 디스크에 내려쓰지 않아 프로세스 재시작 시 `load_state()`가
진입 시점 값을 읽어온다.

같은 파일의 다른 경로(매수/매도/청산 등)에는 `save_state()`가 13곳 있으나,
**고점 갱신 경로에만 누락**되어 있었다. 실시간 틱마다 호출되는 경로라
I/O 부담을 우려해 의도적으로 뺀 것으로 보이나, 대안(throttle)이 없어
영속화 자체가 사라진 상태였다.

`btc-trader.service`는 `Restart=always` + `WatchdogSec=300`이므로
**재시작은 예외 상황이 아니라 상시 가능한 일**이다.

## 수정

```python
    pos["trail_stop"] = max(new_stop, hard_floor)

    # 상승분 영속화 — 틱마다 쓰지 않도록 throttle
    _now_mono = _time.monotonic()
    if _now_mono - self._trail_persist_ts >= TRAIL_PERSIST_INTERVAL_SEC:
        self._trail_persist_ts = _now_mono
        save_state(self.state)
```

- `TRAIL_PERSIST_INTERVAL_SEC = 30`을 **`config.py`에 정의하고 import**
  (lessons #19 — 모듈 자체 상수 정의 시 단일 진실 원천 붕괴).
- 최악의 경우 30초 분량의 ratchet만 유실. 하루치 전체를 잃던 것 대비 무해한 수준.
- `save_state()`는 이미 atomic write(tmp → `os.replace`)이므로 부분 쓰기 노출 없음.

## 검증규칙 (pre_deploy_check.py)

`check_trail_stop_persisted()` 신설:

1. 고점 갱신 블록(`if price > pos["highest"]:`) **본문 안에** `save_state` 존재
2. `TRAIL_PERSIST_INTERVAL_SEC`가 `realtime_monitor.py`에 자체 정의되지 않고
   `config.py`에 존재 (lessons #19 회귀 차단)
3. 블록 자체를 못 찾으면 **ERROR** (사문화 룰 방지)

버그 재현본에서 1번이 발화하는 것을 확인함 (역방향 검증 통과).

## 관련 발견 (별건, 미수정)

상태 파일이 **체결가가 아닌 신호가**를 기록하고 있다.

| 종목 | 상태파일 `entry_price` / `entry_qty` | 거래소 실보유 | 실제 평균 체결가 |
|---|---|---:|---:|
| TAIKO | 108 / 475.2153 | 470.8555 | 109.0 |
| SPK | 24.1 / 2095.0104 | 2086.3534 | 24.2 |

슬리피지가 반영되지 않아 손익 계산과 손절선 기준이 실제보다 유리하게 잡힌다.
교훈 #10(상태 파일은 거래소 미러) 계열 — 별도 건으로 추적 필요.

## 교훈

1. **상태를 바꾸는 모든 경로에 영속화가 붙어야 한다.** 같은 파일에 `save_state()`가
   13곳 있는데 1곳만 빠지면, 코드 리뷰에서 "저장하고 있다"는 인상만 남고
   누락은 눈에 띄지 않는다. 경로별 누락은 grep으로 세어야 보인다 (교훈 #6 계열).
2. **I/O 부담을 이유로 영속화를 빼지 말고 throttle하라.** 빈도가 문제면 간격을 두면 된다.
   "안 쓰기"는 해결이 아니라 데이터 유실이다.
3. **`Restart=always`인 서비스에서 "재시작 시"는 예외 경로가 아니다.** 워치독 타임아웃,
   OOM, 배포 — 재시작은 상시 발생한다. 재시작 후 상태가 어떻게 복원되는지가
   정상 동작의 일부다.
