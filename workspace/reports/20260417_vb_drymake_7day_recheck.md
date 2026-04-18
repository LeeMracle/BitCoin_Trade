# VB 개선 DRY-RUN 7일치 재검증 (P5-28b)

- **작성일(KST)**: 2026-04-17 10:00
- **작성자/세션**: pdca-pm (자비스) — 자동 집계
- **검증 기간**: 2026-04-10 ~ 2026-04-16 (7일)
- **관련 결정**: [20260403_vb_dryrun_review.md](../../docs/decisions/20260403_vb_dryrun_review.md), [20260408_1_cb_existing_positions_policy.md](../../docs/decisions/20260408_1_cb_existing_positions_policy.md), P5-28 개선 배포(04-09 16:50 KST)

## 1. 재검증 목적

2026-04-03 검증(3일, 6건, 승률 33%, 누적 +6.23%)이 **샘플 부족으로 판단 보류**되었고, 04-09 **VB 개선 A~E**가 배포되었다(하락장필터/데드블랙/주3회/연패쿨다운/임계완화). P4-06 시점의 NO-GO 판단을 **개선 후 7일 데이터**로 재평가한다.

개선 내용 요약(P5-28):
- A. 하락장 필터(BTC EMA200 아래에서 K bearish 상향, DEAD 종목 블랙리스트)
- B. DEAD 심볼 자동 배제 (vb_filters.compute_dead_symbols)
- C. 주간 최대 3회 진입 제한 (심볼별)
- D. 연패 쿨다운 (N회 연패 시 지정 시간 동안 진입 차단)
- E. 임계완화 — SL(%) 완화, K 슬라이딩 상향

## 2. 데이터 소스와 접근 제약

재집계에 필요한 핵심 데이터:
1. `workspace/vb_state.json` (서버 상주) — 누적 거래 기록
2. `journalctl -u btc-trader --since 2026-04-10 --until 2026-04-17` (서버 상주) — `[VB]` 태그 로그
3. `workspace/reports/vb_rotation_*.md` 또는 `workspace/vb_journal.json` (있을 경우)

**제약**: 이번 세션은 로컬(Windows) 환경이고 SSH pem 키로 서버 실시간 접근이 제한된다. 실제 AWS 서버(13.124.82.122)의 vb_state.json / journalctl 로그에 접근해야 정확한 재집계 가능.

**현재 세션이 수행 가능한 범위**:
- 개선 배포 여부 확인 (04-09 배포 완료, WBS 기록)
- 기존 정량 기준(승률/평균수익/MDD) 재평가 기준 정리
- 재집계 실행 스크립트 템플릿 제공 (서버에서 실행하도록)
- 한계 명시 + 후속 조치 권고

## 3. 04-09 배포 기준 데이터(참조) — 개선 전 24건

WBS 기록(`docs/00.보고/WBS.md:42`):
- **04-09 시점 VB DRY-RUN 7일 누적(03-31 ~ 04-08)**: 24건, 승률 36.8%, 누적 +13.66%, 4 PASS / 2 FAIL
- **판정: NO-GO (개선 후 재검증)** — P5-28 개선 A~E 적용 후 04-15 재집계 대기

## 4. 재집계 실행 스크립트 (서버에서 실행)

아래 스크립트를 서버에서 실행하면 개선 후 데이터로 재집계 가능하다.

```bash
# AWS 서버에서 실행
cd /home/ubuntu/BitCoin_Trade
PYTHONUTF8=1 .venv/bin/python - <<'PY'
import json, pathlib, statistics, datetime as dt
state = json.loads(pathlib.Path("workspace/vb_state.json").read_text(encoding="utf-8"))
trades = state.get("trades", [])  # [{symbol, entry_ts, exit_ts, return_pct, ...}]
start = dt.datetime(2026,4,10)
end   = dt.datetime(2026,4,17)
filt = [t for t in trades if start <= dt.datetime.fromisoformat(t["exit_ts"][:19]) < end]
rets = [t["return_pct"] for t in filt if "return_pct" in t]
wins = [r for r in rets if r > 0]
print(f"기간: 2026-04-10 ~ 2026-04-16")
print(f"거래 수: {len(rets)}")
if rets:
    print(f"승률: {len(wins)/len(rets)*100:.1f}%")
    print(f"평균 수익: {statistics.mean(rets)*100:+.2f}%")
    print(f"누적 수익: {(1 + sum(rets)) - 1 if False else sum(rets)*100:+.2f}%")
    # MDD 계산
    eq = 1.0; peak = 1.0; mdd = 0.0
    for r in rets:
        eq *= (1 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq/peak - 1)
    print(f"MDD: {mdd*100:+.2f}%")
PY
```

**대안 (journalctl 기반)**: vb_state.json에 거래 기록이 누적되지 않는 경우 journalctl에서 `[VB]` 태그 추출:

```bash
journalctl -u btc-trader --since "2026-04-10 00:00" --until "2026-04-17 00:00" --no-pager \
  | grep -E "\[VB\]" > /tmp/vb_7day.log
grep -c "진입:" /tmp/vb_7day.log   # 진입 건수
grep -c "청산:" /tmp/vb_7day.log   # 청산 건수
grep "수익률" /tmp/vb_7day.log | head -30
```

## 5. 재평가 기준 (개선 효과 판정)

| 지표 | 기존(04-09 NO-GO) | 개선 후 목표 | 비고 |
|------|-------------------|--------------|------|
| 승률 | 36.8% | **≥ 40%** | 실전 전환 최소 기준 |
| 평균 수익률 | - | **≥ +1.0%** | OR 조건 |
| MDD | - | **≥ -10%** 이내 | 안전 상한 |
| 거래 수 | 24건(7일) | 15건+ | 통계 유의성 하한 |

**결정 규칙**:
- **GO**: 승률 ≥ 40% 또는 (평균 수익률 ≥ +1.0% AND MDD ≥ -10%)
- **CONDITIONAL**: 승률 36~39% 또는 MDD -10~-12% → 2주 추가 검증
- **NO-GO**: 승률 < 36% AND 평균 수익률 < 0 → 개선안 재설계 또는 VB 폐기 검토

## 6. 이번 세션 판정

**판정: 데이터 미확보 — 서버 직접 재집계 필요 (임시 HOLD)**

근거:
- 로컬에 vb_state.json 없음, journalctl 접근 불가.
- 개선 A~E 배포는 04-09 완료 확인. 7일 경과(04-16) 시점에서 재집계 타이밍은 도래.
- 다음 작업일(04-18)에 서버 접속하여 위 §4 스크립트 실행 후 본 리포트 §7~§9 채워 넣기.

## 7. 재집계 결과 (2026-04-18 23:30 KST 서버 실행)

- **데이터 소스**: `workspace/vb_state.json` (history 24건, 04-10 이후 신규 0건) + `journalctl -u btc-trader --since "2026-04-10" --until "2026-04-18"` ([VB] 태그 21줄)
- **집계 기간**: 2026-04-10 00:00 ~ 2026-04-17 23:59 (7일)

| 지표 | 값 |
|------|-----|
| 거래 수 | **0건** |
| 승률 | N/A (거래 없음) |
| 평균 수익률 | N/A |
| 누적 수익률 | 0.00% |
| MDD | 0.00% |
| 최대 이익 | - |
| 최대 손실 | - |

**진입 시도 로그 (journalctl [VB] 태그, 21줄 요약)**:
- 7일간 매일 00:03 일일 스캔 실행 → **7일 연속 "A: 하락장(BTC<EMA200)"로 신규 진입 차단** (04-10, 04-11, 04-12, 04-13, 04-14, 04-15, 04-16, 04-17)
- "오늘 이미 회전 완료" 메시지 14회 — 기존 회전 로직 정상 호출(진입 자체는 위 A 필터로 차단됨)
- 실제 진입/청산 이벤트 **0건**

## 8. 거래별 표

거래 이벤트 없음. 대신 차단 사유별 집계:

| 차단 사유 | 발동 횟수 | 비고 |
|-----------|-----------|------|
| A: 하락장 필터 (BTC<EMA200) | 7 (매일 1회) | 의도된 차단 — 04-09 P5-28 개선 A의 목적대로 동작 |
| B: DEAD 블랙리스트 | (활성 심볼 1건 유지) | `dead_symbols_count=1`, 차단 이벤트 로그 없음(사전 배제) |
| C: 주 3회 제한 | 0 | `weekly_count={}` — 진입이 없어 미발동 |
| D: 연패 쿨다운 | 0 | 미발동 |

## 9. 최종 권고 — **CONDITIONAL (샘플 부족, 추가 관찰)**

§5 결정 규칙 적용:
- 거래 수 0건 < 통계 유의성 하한 15건 → **판정 보류**
- 승률/평균/MDD 모두 계산 불가 (거래 없음)
- 원인: BTC가 7일 연속 EMA200 이하를 유지하여 **개선 A(하락장 필터)가 100% 의도대로 작동 중**. VB 모듈이 "고장"이 아니라 "대기 상태(방어 성공)".

**결정**:
- ✅ VB 모듈 유지 (비활성화하지 않음)
- ✅ 하락장 필터는 **정상 작동** 중 — 2026-04-09 P5-28 개선의 설계 목표에 부합
- ⏳ 상승장 복귀 (BTC > EMA200) 시 샘플 수집 재개 → 04-25 또는 BTC가 EMA200 상향 돌파 후 7일 시점에서 재집계

## 10. 후속 조치

- [x] 04-18 서버 집계 실행 → §7~§9 완료
- [x] 결정: CONDITIONAL — 04-25 또는 상승장 복귀 후 재집계 (별도 decisions 문서는 판정 보류이므로 미생성, 본 리포트로 갈음)
- [x] VB 모듈 비활성화 불필요 (`VB_ENABLED=True` 유지)
- [ ] 상승장 복귀 감지 시 자동 알람 (P5-04 레짐 자동 전환 시스템과 연계 — 별도 티켓)
- [ ] 재집계 트리거: BTC > EMA200 조건 7일 충족 시 즉시 본 리포트 후속판 작성
