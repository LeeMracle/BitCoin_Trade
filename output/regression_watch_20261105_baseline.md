# 24h 회귀 감시 베이스라인 — 2026-11-05 13:17 KST

## 배포 정보

- 커밋: `2b6b3ff` (ADR 20261105-1 EMA-TREND 폐기 + 알림 throttle + ML shadow 위생) / `0dd86ef` (lessons #53)
- 서버 재시작: **2026-11-05 13:05:02 KST** (UTC 04:05:02) MainPID **521961**
- 감시 종료: **2026-11-06 13:05 KST**
- 감시자: bata-operator

## 베이스라인 스냅샷 (13:17 KST = UTC 04:17)

### 1. EMA-TREND 침묵 (최우선)

| 항목 | 베이스라인 | 임계 |
|---|---|---|
| journal `EMA-TREND` 발사 수 (재시작 이후) | **0건** | 1건 이상 → **즉시 engineer** |
| `workspace/ema_trend_state.json` history len | **198건** (고정) | 추가 시 즉시 alert |
| state 파일 mtime | `2026-11-05 03:47:43 UTC` (재시작 이전) | 재시작 후 mtime 갱신 시 alert |

> history 198건 전부 재시작 이전 기록(마지막 03:47 UTC 03:47 exit). 재시작(04:05) 이후 history 신규 추가 0건 확인.

### 2. 메인 전략 systemd

| 항목 | 값 | 판정 |
|---|---|---|
| ActiveState | `active (running)` | OK |
| MainPID | `521961` | 변경 시 재시작 사유 조사 |
| Memory | 357M (peak 391M) | < 400M 예산 내 OK (lessons #5) |
| 좀비 (self 제외) | 1 (realtime 본체만) | OK |
| 구독 종목 | 144개 | OK |

### 3. state ↔ balance

| 항목 | 값 | 비고 |
|---|---|---|
| cash_krw | `None` | **보고만** 등급 (위임 프롬프트 명시) |
| positions | 1종목 (`XAUT/KRW`) | 거래소 cross-check 필요 시 매시 healthcheck 참조 |
| consec_loss | `None` | 연패 플래그 없음 |
| cooldown_until | `None` | 쿨다운 없음 |
| closed_trades | 209건 (누적 승률 45%, 합계 -153.4%) | expert 영역 (operator 스코프 밖) |
| state mtime | `2026-11-05 04:04:58 UTC` | 재시작 직전 저장본, 정상 |

### 4. REGIME

> **⚠ 레짐 용어 2중화** (lessons #55) — 매매 경로와 관찰 경로는 **독립 시스템**이며 같은 BEAR/BULL 라벨을 공유한다. 매매 가능 여부는 **매매 레짐만** 참조.

- **매매 레짐 기준** (`scanner.fetch_btc_above_ema()`, BTC>EMA200 단일): 현재 **BULL** (True) — BEAR 전환 시(BTC<EMA200) 신규 매수 0건이 정상.
- **관찰 레짐** (`workspace/regime_state.json`, regime_switcher SMA50+EMA200+F&G): `current=BEAR` / `prev=BULL` / `enabled=false` — **관찰용(DRY-RUN)**이며 매매 코드에 연결되지 않음 (`REGIME_SWITCH_ENABLED=False`). regime_check.py 가 매일 00:30 UTC 재계산.
- `enabled=false` 값은 regime_switcher의 중간 플래그(스위칭 OFF)이며, 매매 레짐(`REGIME_FILTER_ENABLED=True`)과 **무관**.

### 5. 스케줄러 정합 (lessons #44)

| 항목 | 값 | 판정 |
|---|---|---|
| `crontab -l \| grep -c BitCoin_Trade` | **0** | OK (의도된 상태, timer 이전 완료) |
| `systemctl list-timers \| grep -c bata-` | **9** | OK (baseline 9) |
| 등록 timer | watchdog / critical-healthcheck / daily-report / ml-outcome / log-volume / vb-recheck / regime-check / daily-briefing / ml-weekly | OK |
| 다음 트리거 (bata-daily-briefing) | 2026-11-06 00:32 UTC (09:32 KST) | 정상 (24h 감시 중 발화 예정) |

### 6. ML shadow

- 오늘자 `workspace/ml_shadow/20261105.jsonl` 존재 (280B, 02:13 UTC = 11:13 KST 수정)
- `EMA_TREND` grep 결과: **0건** (오늘자 jsonl 내 유입 없음)
- 참고: 유입되더라도 `services/ml/shadow.py::EXCLUDED_SIGNAL_TYPES` 가 `EMA_TREND_breakout` 차단 처리 — 다음 `bata-ml-weekly` 실행 (일요일 04:00 UTC) 시 집계 결과로 검증

### 7. 텔레그램

- 09:32 KST 아침 브리핑: `[telegram] Markdown 400 → plain fallback 성공` 로그 확인 (lessons #39 수정 정상 동작)
- `send_message` plain fallback 재시도 메커니즘 OK

## 이상 항목 (재시작 후 04:05~04:18 UTC 13분 구간)

| 항목 | 상태 | 등급 |
|---|---|---|
| FLUID/KRW ATR 8.3~8.5% > 7% 차단 반복 | 정상 동작 (ATR 필터 작동) | noise (operator silence) |
| 매수/매도 신호 | 0건 | 매매 레짐 BULL이나 13분 창 샘플 특성상 미발화 (관찰 레짐 BEAR 무관, lessons #55) |
| 텔레그램 Markdown 400 | plain fallback 자동 복구 | OK (lessons #39) |

## 종합 판정

**베이스라인 모두 그린 — EMA-TREND 침묵 OK, systemd/timer/좀비/메모리 OK, 보고만 등급(`cash_krw=None`) 1건**

---

## 24h 자동 감시 설계안 (operator 권고)

### 배경 — 왜 완전 자동화는 어려운가

- operator 세션은 지속 불가 (9분 cycle live 감시 불가)
- 서버 측 cron/timer 신설은 **engineer 영역** (lessons #44 crontab/systemd 편집 위험, 다른 timer 영향 가능)
- 로컬 Windows Task Scheduler는 PEM 키 노출 보안·Python 환경 종속성 복잡

### 권고안: 체크포인트 수동 호출 + 1-line 요약

**체크포인트 일정 (4회)**

| # | 시각 (KST) | 경과 | 목적 |
|---|---|---|---|
| CP1 | 2026-11-05 19:05 | +6h | 6h 중간 — EMA-TREND 침묵 재확인 |
| CP2 | 2026-11-06 01:05 | +12h | 야간 구간 — 좀비/timer/09:32 브리핑 준비 |
| CP3 | 2026-11-06 07:05 | +18h | 아침 브리핑(09:32 KST) 발사 후 확인 |
| CP4 | 2026-11-06 13:05 | +24h | 최종 종합 — close 판정 |

**각 CP에서 PM이 operator 재호출** → operator가 아래 7개 항목 1회 실측 후 1-line 요약 리턴:

```
cycle CPn / EMA-TREND 침묵 OK (journal 0건 / history 198 fixed)
  / systemd OK (PID 521961 / mem XXX M) / 좀비 1 / timer 9 / cron 0
  / state OK (positions N / cash K) / 특이사항 X
```

**이상치만 상세 섹션으로 확장** — 에스컬레이션 기준(위임 프롬프트):
- **즉시 alert → engineer**: EMA-TREND journal 1건 이상 / history 199+ / systemd 비정상 / 좀비 ≥ 2 / cron 라인 수 ≠ 0 / timer ≠ 9
- **보고만**: cash_krw None 지속 / dust 알람 반복
- **expert 라우팅**: PnL 급락 / 승률 급락 (operator 스코프 밖)

### 보조: operator 수동 트리거 명령어 모음 (각 CP에서 복붙 1회 실행)

```bash
# A. EMA-TREND 침묵 (최우선) — 재시작 이후 journal + state history
ssh -i "$PEM" "$HOST" "sudo journalctl -u btc-trader --since '2026-11-05 04:05:00' --no-pager | grep -c 'EMA-TREND'"
ssh -i "$PEM" "$HOST" "cd /home/ubuntu/BitCoin_Trade && python3 -c \"import json; print(len(json.load(open('workspace/ema_trend_state.json'))['history']))\""

# B. systemd + 좀비
ssh -i "$PEM" "$HOST" "systemctl show btc-trader --property=MainPID,ActiveState,MemoryCurrent && pgrep -af 'daily_live.py' | grep -v 'bash -c' | grep -v grep | wc -l"

# C. 스케줄러 (lessons #44)
ssh -i "$PEM" "$HOST" "crontab -l | grep -c BitCoin_Trade && systemctl list-timers --all --no-legend | grep -c 'bata-'"

# D. state + closed_trades
ssh -i "$PEM" "$HOST" "cd /home/ubuntu/BitCoin_Trade && python3 -c \"import json; d=json.load(open('workspace/multi_trading_state.json')); print('cash=',d.get('cash_krw'),'pos=',len(d.get('positions',{})),'closed=',len(d.get('closed_trades',[])))\""

# E. ML shadow 오늘자
ssh -i "$PEM" "$HOST" "cd /home/ubuntu/BitCoin_Trade && TODAY=\$(date -u +%Y%m%d) && ls -la workspace/ml_shadow/\${TODAY}.jsonl && grep -c 'EMA_TREND' workspace/ml_shadow/\${TODAY}.jsonl"
```

### 완전 자동화 옵션 (선택)

**옵션 A: engineer에게 서버 측 "24h regression 감시 timer" 신설 위임**
- 신설: `bata-regression-watch.timer` (매 6h 실행, 2026-11-06 13:05 UTC 자동 비활성화)
- 1회성 timer 철거 로직까지 포함 — 복잡도 높음, 24h 짧은 window 고려 시 과설계 가능성
- **판정: 비권장** (ROI 낮음, 수동 체크포인트 4회로 충분)

**옵션 B: 로컬 PowerShell 스크립트 + Windows Task Scheduler**
- 4회 체크포인트 자동 ssh 실행 → stdout 로그 저장
- PEM 키·`$HOME` 환경 문제로 subagent 재진입 시 안정성 변수 (lessons #35)
- **판정: 보류** (CP4 (24h 최종) 때만 operator 수동 실행 권장)

### 최종 운영 방식 제안

- **4회 체크포인트 수동 호출 (CP1~CP4)**, 각 CP마다 operator가 7개 항목 1회 실측 후 1-line 요약
- 이상 감지 시 **CP 응답에 상세 섹션 포함 + engineer 즉시 알림**
- 24h 완료 (CP4) 시 operator 최종 보고 → PM이 task #6 close 판정

## 다음 액션

1. 본 베이스라인 보고 PM에게 회신 (현재 응답)
2. CP1 (6h 후, 2026-11-05 19:05 KST) PM 재호출 대기
3. CP2~CP4 동일 패턴 반복
4. 중간 이상 발견 시 즉시 engineer 알림 + PM 통보
