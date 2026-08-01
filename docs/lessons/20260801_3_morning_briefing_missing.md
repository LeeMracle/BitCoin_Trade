# lessons #38 — 아침 브리핑 채널 부재 (silent morning gap)

- **발생일(KST)**: 2026-08-01 (사용자 지적으로 발견)
- **영향 기간**: 2026-06-02 이후 (lessons #33/#34 CRON_LIVE 제거 이후) — 약 2개월 무브리핑
- **심각도**: MEDIUM (사고는 아니지만 사용자 접점 부재로 이상 감지 지연 위험)

## 1. 원인

- 원래 아침 시각(09:05 KST) `CRON_LIVE`가 `daily_live.py` 실행 시 텔레그램 시작 알림을 발송했음
- lessons #33 (20260602_1 cron_zombie_relapse_no_realtime): systemd `btc-trader.service`가 `--realtime`을 상시 가동하므로 cron 별도 호출 시 매일 새 인스턴스가 생성돼 좀비 누적 → `CRON_LIVE` 완전 제거
- 하지만 **아침 브리핑 대체 채널을 마련하지 않음** → 사용자 아침 접점이 전무한 상태로 방치
- `CLAUDE.md`에는 "일일 체크: `python scripts/daily_check.py` (09:05 KST 실행 권장)"라고 명시돼 있으나 **crontab에 자동 등록되어 있지 않음** — 문서와 실제 운영 상태 괴리
- 오후 18:00 KST `daily_report.py`만 유일한 정기 텔레그램 채널이었으며, 아침 이상 상태 발생 시 최대 9시간 지연 감지

## 2. 수정 내역

### 2-1. `scripts/daily_check.py` 개편
- `--notify` 인자 추가 시 통합 아침 브리핑 발송
- 브리핑 5개 섹션:
  1. 🌤 레짐 상태 — `workspace/regime_state.json` (현재/이전/큐/마지막 판정)
  2. 🤖 봇 상태 — `systemctl is-active btc-trader`, MainPID, `pgrep -af daily_live.py` 좀비 카운트
  3. 💰 계좌 현황 — `upbit_client.get_balance()` + `multi_trading_state.json` 포지션·어제 마감·누적
  4. ⏰ cron 정합 — `crontab -l | grep BitCoin_Trade` 라인 카운트 (baseline 9)
  5. 🚨 이상 항목 — cooldown_until (5연패), 서킷브레이커 level 요약
- `--skip-console` 인자: cron 실행 시 콘솔 페이퍼 체크 SKIP하고 브리핑만 발송

### 2-2. `scripts/deploy_to_aws.sh` cron 등록
```
CRON_DAILY_BRIEFING="32 0 * * * ... scripts/daily_check.py --notify --skip-console >> /var/log/btc_report.log 2>&1"
```
- 09:32 KST 실행 = `regime_check.py`(09:30) 완료 2분 후 → `regime_state.json` 신선한 값 반영
- `daily_check.py` grep -v 제거 추가, `echo $CRON_DAILY_BRIEFING` 등록 라인 추가
- 사후 실측 게이트: baseline 8 → **9**로 상향

## 3. 검증 규칙 (pre_deploy_check.py)

`check_morning_briefing_registered()` 신설:
- `CRON_DAILY_BRIEFING` 변수 정의 존재
- 해당 변수에 `daily_check.py` + `--notify` 동시 포함
- 실행 시각 = `32 0 * * *` (09:32 KST) 확인
- `echo "$CRON_DAILY_BRIEFING"` 라인 존재
- `grep -v "daily_check.py"` 라인 존재 (기존 등록 정리)
- 사후 실측 게이트 baseline 값 ≥ 9

## 4. 교훈

1. **cron 라인 제거 시 대체 채널 명시 필수** — 채널이 하나 사라지면 그 채널의 사용자 접점 기능도 다른 곳으로 옮겨야 함. `CRON_LIVE` 제거 시 아침 브리핑 대체 미고려는 lessons #33 후속조치 누락
2. **문서와 실운영의 괴리 자동 감지 필요** — `CLAUDE.md`에 "09:05 KST 실행 권장"이 있는데 crontab 미등록 상태를 2개월 방치. 문서 언급 스크립트가 crontab에 있는지 정합 검사 검토 필요
3. **사용자 접점은 "침묵도 정보"가 되지 않도록 매일 최소 1건 발송** — BEAR 지속 상태에서 regime_check `--notify`는 전환 시에만 발송 = 무전환 시 침묵. 매일 정기 브리핑은 별도 채널로 보장해야 이상 감지 가능
4. **baseline 카운트는 인프라 변경 시 동시 갱신** — 신규 cron 추가 시 `deploy_to_aws.sh` 사후 게이트 baseline + `pre_deploy_check` baseline 동시 상향 필수

## 5. 관련 lessons

- lessons #33 (20260602_1): CRON_LIVE 제거 (원인 제공)
- lessons #34 (20260602_1 후속): non-realtime 좀비 감지
- lessons #36 (20260801_1): 사후 실측 게이트 baseline 개념 도입 (본 lessons에서 8 → 9 상향)
- lessons #37 (20260801_2): regime_check `--notify` 누락 (동일 패턴 — cron 인자 누락 silent fail)
