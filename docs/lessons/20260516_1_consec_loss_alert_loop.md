# lessons #30 — 5연패 자동 중단 알람 반복 발사

- **날짜**: 2026-05-16 (KST)
- **참조**: lessons #14 (이벤트 루프 throttle), #28 (state 자동 정리), ADR 20260516-2

## 현상

`_send_periodic_report` 매 cycle (약 9분 주기) 호출 시 `check_consec_loss` 결과 5연패 → 동일 알람 텔레그램 반복 발사. 5/16 21:08, 21:17, 21:26, 21:35... 사용자에게 동일 메시지 4건 도착.

```
🛑 5연패 자동 중단
연속 5건 손실 — 검증 플랜 조기 탈출
승률: 20% (3/15)
원인 분석 후 전략 수정 필요
재시작: sudo systemctl restart btc-trader
```

## 근본 원인

`services/execution/realtime_monitor.py:_send_periodic_report()`의 5연패 분기:
- `self.running = False` 설정 후 알람 발사 + return
- 그러나 `self.running`이 외부에서 또 True로 복원되는 패턴 (또는 _send_periodic_report가 별 task로 self.running 무관 호출)
- 매 cycle마다 `check_consec_loss` → 5연패 → 알람 → return 반복

**핵심**: 알람 발사 자체에 "이미 발사됨" 플래그가 없음. cooldown_until은 매수 차단에만 사용, 알람 디바운스 미연결.

## 핵심 교훈

- **안전장치 알람도 발사 후 디바운스 필수** — lessons #14("이벤트 루프 throttle") 정신을 알람에도 적용
- `cooldown_until` 같은 안전장치 플래그와 알람 발사 플래그는 **분리** 필요 — cooldown은 매수 차단, alerted_until은 알람 silence
- self.running=False가 cycle을 멈춘다고 가정 금지 — 다른 코루틴이 계속 cycle 돌릴 수 있음

## 수정

### 코드 변경
- `services/execution/realtime_monitor.py:_send_periodic_report()`:
  - 5연패 검출 후 `state["consec_loss_alerted_until"]` 또는 `cooldown_until` 미래값 살아있으면 **알람 발사 skip + 콘솔만**
  - 신규 발사 시 `consec_loss_alerted_until = now + 72h` 설정 (cooldown과 동일 기간)

### 즉시 응급
- AWS state에 `consec_loss_alerted_until` 수동 설정 (now + 72h) → 코드 패치 적용 전부터 즉시 silence

## 검증규칙 (pre_deploy_check 추가 후보)

- `realtime_monitor.py:_send_periodic_report`에 `consec_loss_alerted_until` 또는 동등 디바운스 로직 존재
- 5연패 케이스 시뮬: 동일 state 두 번 호출 시 첫 번째만 알람, 두 번째는 콘솔만

## 관련 lessons

- #3 안전장치는 체결 즉시 체크 (주기 체크 X)
- #14 이벤트 루프 로그 throttle 필수
- #22 알림 등급(level) 도입은 default 호환 유지
- #28 state ↔ balance 일치 + 디바운스
