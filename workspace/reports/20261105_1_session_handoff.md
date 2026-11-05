# 세션 인수인계 — 2026-11-05

**EMA-TREND 전략 폐기 + 레짐 명명 혼동 교정이 핵심.** 다음 세션은 §2(CP4 완료 후 액션)부터 읽을 것.

---

## 1. 현재 상태 (2026-11-05 22:00 KST 실측)

| 항목 | 값 |
|---|---|
| 봇 | `btc-trader.service` **active** (MainPID 521961, 재시작 13:05 KST) |
| 보유 | **2/15** — XAUT/KRW (02:13 UTC 진입), POL/KRW (08:30 UTC 진입) |
| 매매 레짐 (BTC>EMA200) | **BULL** (True) — 신규 진입 허용 |
| 관찰 레짐 (regime_switcher) | **BEAR** (since 11-01 00:30 UTC, DRY-RUN, 매매 미연결) |
| 슬롯 13개 비어있는 이유 | BEAR 아님, DC12 돌파 신호가 드물고 종목 필터(ATR 7%↑, 거래량) 차단이 대부분 |
| 회귀 감시 | **진행 중** — CP2(01:05) / CP3(07:05) / CP4(13:05, 최종) 자동 발사 예약 |
| cash_krw | **null** (state 필드 미채워짐, 다음 매수 신호 전 engineer 복구 필요) |

---

## 2. 🔴 다음 세션이 먼저 해야 할 것 — CP4 완료 후 액션 (2026-11-06 13:05 KST~)

### (a) 회귀 감시 cleanup
```bash
ssh -i "$HOME/Downloads/upbit-trading-key-seoul.pem" ubuntu@13.124.82.122 \
  "bash /home/ubuntu/BitCoin_Trade/scripts/regression_watch.sh --cleanup"
```
→ transient timer 3개 정리 (bata-regression-cp2/cp3/cp4)

### (b) E 항목 서버 반영 (lessons #55 알람 라벨 분리)
`hotfix_deploy.sh` 공식 경로로 재시작 포함 배포. 대상 4파일:
- `scripts/regime_check.py` (`[관찰레짐]` prefix)
- `scripts/daily_check.py` (헤더 변경)
- `services/execution/regime_switcher.py` (`format_notification` prefix)
- `scripts/pre_deploy_check.py` (`check_regime_filter_all_entry_paths()` — 서버 실행에 영향 없으나 함께)

**engineer 위임** → "lessons #55 E항목 서버 반영" 지시만 하면 됨.

### (c) Task #6 close
CP4 텔레그램 수신 + 24h 요약 확인 → PM이 operator에게 최종 close 지시.

---

## 3. 오늘(2026-11-05) 완료한 것 — 커밋 3개

| 커밋 | 내용 |
|---|---|
| `2b6b3ff` | **EMA-TREND 전략 영구 폐기** (ADR 20261105-1) — 175건 실측 승률 0.57% / 누적 -22.64% / trail 작동 0건 / lessons #1 정면 재현 사례. `EMA_TREND_ENABLED=False`, 알람 throttle wrapper, ML shadow `EXCLUDED_SIGNAL_TYPES` 필터, pre_deploy_check 룰 3종 신설 |
| `0dd86ef` | lessons #53 (crontab 오경보 — timer 이전 후 사문화 지점) + 과거 산출물 보존 (ADR 20260817 swing MTF 백테스트 등) |
| `192404a` | lessons #55 (**레짐 명명 혼동 교정**) — 매매 레짐(`scanner.fetch_btc_above_ema`) vs 관찰 레짐(`regime_switcher`) 2시스템 공존을 같은 "BEAR/BULL" 용어로 혼동. CLAUDE.md 각주 + baseline 교정 + 알람 라벨 `[관찰레짐]` prefix + `check_regime_filter_all_entry_paths()` AST 룰 |

---

## 4. 🟡 미해결 / 주의 사항

### (a) `cash_krw = None` 지속
- 봇 재시작(13:05 KST) 후 `fetch_balance` 미반영
- positions=2 상태라 다음 매수 신호 없으면 치명도 낮음
- **다음 매수 신호 전까지 engineer 복구 권장** (수동 `fetch_balance` 1회 호출 또는 realtime_monitor 부트 로직 보강)

### (b) ML shadow 과거 174건 학습 데이터 오염
- `EMA_TREND_breakout` signal_type 174 음수 라벨 유입 상태
- 신규 집계는 필터로 제외되지만, **과거 로그는 보존** (분석 가치)
- 다음 ML 재학습 시 raw N → 필터 M diff 로깅 확인 필요

### (c) 레짐 알람 라벨 분리는 서버 미반영
- 로컬 커밋만 되어 있고 서버 코드는 기존 라벨 사용 중
- CP4 완료 후 (§2-b) 반영 — 그 전까지 받는 `[레짐]` 알람은 "관찰 레짐"임을 Master가 수동 해석

---

## 5. 미사용 lessons 번호 주의

- `lessons/20261105_1_stale_cron_watchpoints_false_alarm.md` = #53
- `lessons/20261105_2_ema_trend_dry_alert_chop.md` = #54
- `lessons/20261105_3_regime_naming_collision.md` = **#55** (파일명 `_3_`과 번호 다름 — INDEX 선점으로 조정)

신규 lessons 작성 시 INDEX.md 실측 후 번호 부여할 것.

---

## 6. 운영 플래그 현재 상태 (`services/execution/config.py`)

| 플래그 | 값 | 비고 |
|---|---|---|
| `REGIME_FILTER_ENABLED` | **True** | 매매 레짐(BTC>EMA200) — ADR 20260516-2 |
| `REGIME_SWITCH_ENABLED` | False | 관찰 레짐 자동 전환 DRY-RUN |
| `EMA_TREND_ENABLED` | **False** | ADR 20261105-1 폐기 |
| `ML_FILTER_ENABLED` / `ML_SHADOW_MODE` | 1 / 1 | shadow 수집 중, 차단 안 함 |
| 슬롯 / 비중 상한 | 15 / 0.10 | ADR 20260825-1 |
| 하드 손절 / 트레일 | -6% / ATR(14)×3.0 | lessons #13 · ADR 20260930-1 |
