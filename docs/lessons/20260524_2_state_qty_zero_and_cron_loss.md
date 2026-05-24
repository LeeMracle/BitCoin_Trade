# lessons #32 — entry_qty=0 회귀 + AWS BitCoin cron 대거 누락

- 날짜: 2026-05-24 (KST 21:30)
- 분류: state 잔량 회계 / cron 인프라 회귀
- 관련 lessons: #10 (state↔거래소 미러), #24 (다중 프로젝트 crontab), #25 (불변 입력/가변 추적 분리), #28 (fix_state 자동 정리), #31 (cron silent fail)
- 발견 경위: PM 21:08 KST 종합 보고에서 결함 3건 제기 → engineer RCA 진행 중 진짜 원인 식별

## 현상

### A. state↔balance 잔량 불일치 (lessons #10/#25 회귀)
`workspace/multi_trading_state.json` vs 업비트 실잔량:

| 종목 | state.entry_qty | state.remaining_qty | 거래소 실잔량 |
|---|---|---|---|
| SUN/KRW | 943.98 | (없음) | 660.79 |
| MTL/KRW | **0** | 93.68 | 46.84 |

MTL은 5/5 매수 시점에 `entry_qty=0` 으로 저장됨. 그 결과 부분 TP1 발동 시 `sold_qty = entry_qty × 0.4 = 0`이 되었어야 하지만, line 1629의 `entry_qty = pos.get("entry_qty") or cur_total` fallback이 작동해 실제 매도는 cur_total 기준으로 성공. 그러나 state 잔량 회계는 영구히 어긋남 → realtime_monitor의 교차검증 로그 "일치 OK — 2종목"은 종목 존재만 확인하므로 잔량 drift는 감지 못 함.

### B. AWS BitCoin cron 대거 누락 (lessons #24/#31 회귀)
AWS `crontab -l` 결과: BitCoin cron이 단 2개만 남음.

남은 cron:
- `0 18 * * *` ml_outcome_match.py
- `0 19 * * 0` ml_weekly_review.py

누락된 cron (deploy_to_aws.sh가 등록해야 할 7개):
- `5 0 * * *` daily_live.py (실 거래 봇)
- `0 9 * * *` daily_report.py (18:00 KST 종합 보고)
- `* * * * *` watchdog_check.sh (heartbeat 감시)
- `10 0 * * *` log_volume_check.sh
- `15 0 * * *` vb_recheck_trigger.py
- `30 0 * * *` regime_check.py
- `5 * * * *` critical_healthcheck.py (인증 8h 무감지 방지 — lessons #20 안전망)

영향: realtime_monitor가 죽어도 자동 복구 안 됨. 인증 실패해도 매시 감지 못 함. 일일 18:00 종합 보고 누락 (실제로 5/22 이후 btc_report.log 0바이트).

### C. (PM 보고 오인) hourly_digest heartbeat 미생성
`/tmp/bata_hourly_digest_heartbeat` 5/4 mtime 정지. 그러나 deploy_to_aws.sh line 134 확인 결과 **2026-05-04 사용자 요청으로 의도적 비활성화** (`# CRON_DIGEST=...` 주석). 진짜 운영 heartbeat는 `/tmp/bata_heartbeat`로 1분 전 정상 갱신중. PM의 lessons #23 회귀 의심은 사실관계 미일치 (PM 보고 자체가 잘못된 경로 점검).

### D. (PM 보고 오인) ml_outcome / ml_weekly_review 로그 0바이트
cron 등록 OK + 매일 실행 OK. 0바이트 사유는 단지 `n_decisions=0` 출력이 짧고 로그 redirect가 매 호출마다 append라 외관상 변화 없음. ml_shadow/*.jsonl이 5/17 이후 누락된 이유는 봇이 신규 매수를 하지 않은 결과(SUN/MTL 2 포지션 보유 중). lessons #31 회귀 아님.

## 근본 원인

### A. entry_qty=0 저장 버그
`services/execution/realtime_monitor.py:1969` 구 코드:
```python
entry_qty = float((order or {}).get("amount") if not DRY_RUN else 0) or (order_amount / exec_price if exec_price > 0 else 0)
```
- 업비트 시장가 매수 응답 `order["amount"]`는 None인 경우가 많다 (cost로 주문, amount는 체결 후 결정).
- `float(None)` → `TypeError` → except → `entry_qty = 0`.
- `or` 단락평가: 첫 항이 0이면 두 번째로 가야 하지만, **첫 항이 예외를 던지면** try 블록 전체가 종료되어 두 번째 fallback에 도달 못 함.
- `fix_state_balance_mismatch.py`는 종목 add/remove만 처리, 잔량(qty) 미러링 누락 → 사후 보정 불가.

### B. AWS BitCoin cron 누락
정확한 시점은 불명이나 정황상:
- 다른 프로젝트(Stock_Trade) deploy 또는 직접 `crontab -e` 시 BitCoin 라인 미보존 (lessons #24와 동형).
- 그 후 BitCoin 측은 hotfix_deploy.sh 또는 scp+restart 우회로만 코드 갱신 → cron 미갱신 (lessons #31).
- 5/14 hotfix로 ml_outcome/ml_weekly_review 2개만 echo 추가 → 나머지 7개는 누락 상태로 영구 silent fail.

## 핵심 교훈

1. **entry_qty 결정은 다중 fallback 사슬 필수**. 업비트 시장가 매수에서 `order["amount"]`는 None일 수 있으므로 `filled` → `amount` → `fetch_balance` → 산식 4단 사슬이 안전.
2. **invariant 가드 명시**. `entry_qty <= 0` 검사 후 경고 로그 + placeholder 저장으로 회귀 즉시 감지.
3. **fix_state는 종목 + 잔량 둘 다 미러링**. lessons #10 "state는 거래소 미러" 원칙 = 종목+수량 양면.
4. **AWS crontab은 baseline 검증 필수**. pre_deploy_check에서 deploy_to_aws.sh의 활성 CRON_xxx 변수 카운트 ≥ baseline을 검증.
5. **다중 프로젝트 환경에서 hotfix scp는 cron 갱신 안 됨**. cron 누락 의심 시 `crontab -l` 전체 확인이 첫 단계.
6. **PM 보고의 사실관계도 engineer가 grep으로 재검증**. "5/14 heartbeat 패치 회귀" 같은 도메인 추측은 코드/주석 확인 후 결론.

## 수정 (코드 + 운영 조치)

### 코드
- `services/execution/realtime_monitor.py:1966~1995` — entry_qty 결정 로직 재작성 (4단 fallback + invariant 가드).
- `scripts/fix_state_balance_mismatch.py:88~125` — 잔량 미러링 로직(`qty_fixes`) 추가. entry_qty=0 또는 remaining_qty drift > 1% 이면 거래소 실잔량으로 보정.
- `scripts/pre_deploy_check.py` — 신규 검증 함수 3개 등록.

### 운영
- AWS에서 `scripts/fix_state_balance_mismatch.py` 실행 → MTL/SUN 잔량 미러링.
- `scripts/deploy_to_aws.sh` 재실행 → 누락된 BitCoin cron 7개 재등록.
- 배포 후 `crontab -l | grep -c BitCoin_Trade` ≥ 9 확인.

## 검증규칙 (pre_deploy_check 추가)

신규 함수 3개를 `scripts/pre_deploy_check.py`에 추가:

1. **`check_entry_qty_invariant()`** — realtime_monitor의 entry_qty 결정 블록에 `filled`, `fetch_balance` fallback과 `entry_qty <= 0` invariant 가드가 모두 존재하는지 검증. 옛 한 줄 표현식(`float((order or {}).get("amount"))`) 잔존 시 즉시 ERROR.
2. **`check_state_qty_mirror_in_fix()`** — `scripts/fix_state_balance_mismatch.py`에 `qty_fixes` 또는 "잔량 미러" 키워드가 존재하는지 검증. 잔량 미러링 로직 없으면 ERROR.
3. **`check_btc_cron_count_baseline()`** — `scripts/deploy_to_aws.sh`의 활성(주석 아닌) `CRON_xxx` 변수 카운트가 ≥ 8 인지 검증. baseline 미만이면 ERROR.

## 관련 lessons
- #10 — state는 거래소 미러 (잔량까지 포함)
- #24 — 다중 프로젝트 crontab 통째 갱신 위험
- #25 — 불변 입력(entry_qty) vs 가변 추적(tp_sold_levels) 분리
- #28 — fix_state 자동 정리 (vb 포함)
- #31 — deploy 우회 cron silent fail

## 후속 권고

- 다음 P2 사이클(투자 expert)에서 부분 TP 회계의 ADR 20260516-1 구현 점검 — entry_amount_krw 기준 회계가 entry_qty 회계와 일관되는지 재검토.
- AWS crontab 풀 검증을 일일 작업(daily-work 스킬)의 헬스체크에 추가 — `expected_btc_cron_count` 환경변수 9 비교.

## 후속 보강 (2026-05-24 22:10 KST, P2-1)

### 발견
P1 close 직후 PM이 P2-1로 위임 — engineer가 권고했던 후속 항목.
- `services/execution/multi_trader.py:245` scanner 매수 경로에 `entry_qty` / `entry_amount_krw` / `tp_sold_levels` 모두 미저장 확인.
- realtime_monitor 단일 경로만 가드 → lessons #6 "모든 매수 경로 일관성" 정신 위배.
- 동형 회귀 위험 잠재 (현재 운영상 scanner 경로는 daily_live.py cron으로 매일 1회 실행, P3 메인 경로는 realtime_monitor이지만 scanner도 진입 가능).

### 수정
- `services/execution/multi_trader.py:235~262` — scanner 매수 경로에도 realtime_monitor.py:1966~2010과 동형 4단 fallback + invariant 가드 + `entry_amount_krw`/`entry_qty`/`tp_sold_levels` 저장 적용.
- `scripts/pre_deploy_check.py` — `check_entry_qty_invariant()`를 단일 파일 검증 → **모든 매수 경로 순회 검증**으로 확장:
  - targets 리스트: `realtime_monitor.py` + `multi_trader.py`.
  - 정규식을 `positions[symbol] = {...}` 본문까지 캡처하도록 확장.
  - `entry_amount_krw` 누락 ERROR / `tp_sold_levels` 누락 WARNING 추가.

### 검증
- pre_deploy_check.py 단독 실행 PASS (warning 1건은 로컬 rsync 부재로 무관).
- 강도 시험: multi_trader에서 `entry_amount_krw` 라인 제거 시 ERROR 발생 → 복구 시 PASS. 회귀 가드 작동 확인.

### 영향 범위 grep 결과 (`positions[symbol]\s*=\s*\{`)
- `services/execution/realtime_monitor.py:2001` — P1 수정 완료
- `services/execution/multi_trader.py:265` — P2-1 수정 완료
- 그 외 매수 후 dict 신규 생성 경로 없음 (`del positions[symbol]` / 부분 매도 후 갱신은 별도 케이스)

### 배포
- multi_trader.py는 daily_live.py가 `python -m services.execution.multi_trader`로 호출 — cron `5 0 * * *` (KST 09:05).
- 다음 daily_live.py 실행 전 hotfix 또는 deploy_to_aws.sh로 반영 권장. 단, 24h 회귀 감시 중이므로 PM 승인 후 배포.
