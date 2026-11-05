# lessons #46 — EMA-TREND DRY-RUN chop 텔레그램 알림 폭주 (23건/6분)

- 날짜: 2026-11-05 KST
- 분류: 알림 throttle / 전략 알람 (DRY-RUN)
- 관련 lessons: #1 (봉 마감 전략을 실시간 틱으로 실행 금지 — 근본 원인), #14 (이벤트 루프 로그 throttle 필수), #30 (안전장치 알람도 발사 후 디바운스), #3 (안전/매수 경로 즉시성 — LIVE는 압축 금지 호환)
- 범위: `services/execution/realtime_monitor.py::_check_ema_trend`, DRY-RUN 모드만

## 현상

2026-11-05 KST 09:03~09:09, EMA-TREND DRY-RUN 전략이 BTC/KRW 단일 심볼에
**6분간 약 23쌍(진입+청산)** 의 텔레그램 알림을 발사. 평균 수익률 -0.07%
(DRY 라 금전 피해 0, 알림만 폭주). 09:57 KST 1쌍 재발.

서버 `workspace/ema_trend_state.json` 의 `history` 배열 166건 전수는
전부 BTC/KRW 심볼, 전부 "EMA50 하향이탈" 사유였음.

사용자 체감: 텔레그램 알림이 수초 간격으로 쏟아져 다른 중요 알림(scanner 매수,
critical healthcheck 등)이 시야에서 밀려남.

## 근본 원인

**(이번 범위 아님)** EMA-TREND 는 "실시간 틱 가격 vs 일봉 EMA" 를 비교한다
— lessons #1 의 전형. 실시간가가 `EMA50 = 108,492,115` 에 딱 걸치자 수초 단위로
위-아래를 오가며 진입/청산 신호가 번갈아 발화. `_check_ema_trend` 가 틱마다
호출되므로 chop 창 안에 쌍이 23회 생성됨.

**이번 범위 (알림 폭주 억제)**:
`_check_ema_trend` 안의 알림 호출이 **날것 `await send(...)` 2곳** 이었다
(진입 1 + 청산 1). 어떤 방어도 없이 틱 이벤트 수만큼 텔레그램 발사됨.
lessons #14 (CB 로그 throttle), #30 (ws-stale 디바운스) 선례는 이미 있었으나
EMA-TREND 는 별도 추가 전략이라 수평 전개가 안 됐음 — lessons #6 (모든 경로
커버) 의 전략 알람 버전.

## 핵심 교훈

1. **"실시간 틱으로 봉 기반 시그널" 전략은 chop 폭주 전제** — 알림 설계 시
   기본으로 throttle 포함해야 한다 (후 추가 아님).
2. **LIVE 체결 알림은 압축 금지** — 실매매 보고는 lessons #3 (즉시성) 범위.
   `EMA_TREND_DRY_RUN` 플래그로 분기 필수.
3. **압축 사실을 사용자에게 알려야 재발 감지가 쉽다** — 쿨다운 창 종료 후
   첫 알림에 "최근 Ns 내 압축 N건" 접미사를 붙이면 chop 자체를 사후 식별 가능.
4. **AST 검증이 유일한 방어선** — 문자열 포함 검사는 주석·docstring 에
   속는다(이 프로젝트 3회 피해, 메타교훈 #45/#49). `send(...)` 호출 금지는
   AST Call 노드 식별자로 걸러야 한다.

## 수정

### 코드

**1) 모듈 상수 + 인스턴스 상태 (`services/execution/realtime_monitor.py`)**

```python
EMA_TREND_ALERT_COOLDOWN_SEC = 300  # 5분 (상단, line 153)
# __init__ 안 (line 175~179)
self._ema_trend_alert_last_ts: dict[str, float] = {}
self._ema_trend_alert_suppressed: dict[str, int] = {}
```

**2) wrapper 메서드 (`_save_ema_trend_state` 바로 뒤, line 1404~1442)**

```python
async def _ema_trend_send_throttled(self, symbol: str, msg: str) -> None:
    # LIVE 는 압축 미적용 (lessons #3 호환)
    if not EMA_TREND_DRY_RUN:
        await send(msg)
        return
    now = _time.time()
    last = self._ema_trend_alert_last_ts.get(symbol, 0.0)
    suppressed = self._ema_trend_alert_suppressed.get(symbol, 0)
    if (now - last) < EMA_TREND_ALERT_COOLDOWN_SEC:
        self._ema_trend_alert_suppressed[symbol] = suppressed + 1
        return
    if suppressed > 0:
        msg = f"{msg}\n_(최근 {EMA_TREND_ALERT_COOLDOWN_SEC}s 내 압축 {suppressed}건 — chop 가능성)_"
    await send(msg)
    self._ema_trend_alert_last_ts[symbol] = now
    self._ema_trend_alert_suppressed[symbol] = 0
```

**3) 호출부 교체 (`_check_ema_trend` 내 2곳)**
- line 1572 (청산 알림): `await send(...)` → `await self._ema_trend_send_throttled("BTC/KRW", ...)`
- line 1602 (진입 알림): 동일

console `print(..., flush=True)` 는 유지(디스크 로그는 정보 손실 금지 — lessons #14).

### 운영 조치

- `hotfix_deploy.sh` 로 배포 (lessons #33 raw scp 금지). 인프라(cron/systemd)
  변경 없음 — scp + btc-trader restart 로 충분.
- 배포 후 24h 회귀 감시(operator) — EMA-TREND 알림이 쿨다운 창마다 최대 1건,
  압축 접미사가 정상 표기되는지 확인.

## 검증규칙 (pre_deploy_check 추가)

함수: `check_ema_trend_alert_throttle()`

AST 기반 (문자열 포함 검사 금지 — 메타교훈 #45/#49):

1. `_ema_trend_send_throttled` 메서드 정의 존재 (없으면 ERROR — "미구현").
2. `_check_ema_trend` 메서드 본문에 `send(...)` identifier Call 노드 **금지**
   (존재 시 ERROR — line 번호 포함).
3. `_check_ema_trend` 본문에 `self._ema_trend_send_throttled(...)` 호출 **≥ 2 개**
   (진입 1 + 청산 1 쌍). 미달 시 ERROR — lessons #6 (모든 경로 커버) 위배.

역방향 테스트 (본 lessons 작성 시 수행 — 3시나리오 ALL PASS):
- 정방향: 현재 코드 ERROR=0
- 변이 B: wrapper 호출을 날것 send 로 되돌리면 ERROR 2건 (날것 호출 + 호출 수 0)
- 변이 C: wrapper 함수 자체 삭제하면 ERROR 1건 ("미구현")

단위 테스트 (압축 로직 자체):
- DRY 모드 10연속 발사 → send 1회 (압축 9) — PASS
- 쿨다운 후 재발사 → "압축 9건" 접미사 포함 2회째 — PASS
- LIVE 모드 5연속 → send 5회 (압축 미적용) — PASS

## 관련 lessons

- #1 — 봉 마감 전략을 실시간 틱으로 실행하면 chop 발생(근본 원인, 이번 작업 범위 밖)
- #3 — 매수/매도 즉시성 경로는 retry/throttle 금지 → LIVE 체결 알림 압축 제외의 근거
- #6 — 모든 매수 경로 커버 필수 → 전략 알림 버전으로 이번 수평 전개
- #14 — 이벤트 루프 로그 throttle (CB 발동 중) 선례
- #30 — 안전장치 알람도 디바운스 (5연패 알림 4회 반복) 선례
- #45/#49 — AST 검증 필수 (문자열 포함 검사 3회 피해)
