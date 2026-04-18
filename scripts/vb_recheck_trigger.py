#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VB 재검증 자동 트리거.

조건:
  - BTC 일봉 close > EMA200 이 최근 7일 연속 충족
  - 마지막 재집계 이후 최소 7일 경과 (workspace/vb_recheck_last.json 확인)

동작:
  - 조건 충족 시 workspace/reports/YYYYMMDD_vb_drymake_auto_recheck.md 생성
  - 서버에서 vb_state.json + journalctl 집계 스크립트 템플릿 포함
  - 텔레그램으로 "VB 재검증 트리거 발동" 알림 전송
  - workspace/vb_recheck_last.json 갱신

사용:
  python scripts/vb_recheck_trigger.py            # 체크만
  python scripts/vb_recheck_trigger.py --notify   # 체크 + 텔레그램 알림 (조건 충족 시만)
  python scripts/vb_recheck_trigger.py --force    # 조건 무시하고 강제 생성
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# ── 경로 상수 ──────────────────────────────────────────────────────────
_STATE_FILE = _PROJECT_ROOT / "workspace" / "vb_recheck_last.json"
_REPORTS_DIR = _PROJECT_ROOT / "workspace" / "reports"

# ── 파라미터 ──────────────────────────────────────────────────────────
_EMA_PERIOD = 200        # BTC EMA 기간 (일)
_CONSEC_DAYS = 7         # 연속 상향 조건 (일)
_COOLDOWN_DAYS = 7       # 마지막 트리거로부터 최소 경과 일수
_FETCH_LIMIT = _EMA_PERIOD + 50  # EMA 안정화용 여유분


# ════════════════════════════════════════════════════════════════════
# BTC EMA200 연속 충족 확인
# ════════════════════════════════════════════════════════════════════

def fetch_btc_daily_closes(limit: int = _FETCH_LIMIT) -> list[float]:
    """BTC/KRW 일봉 종가 조회. 실패 시 빈 리스트 반환."""
    try:
        exchange = ccxt.upbit({"enableRateLimit": True})
        candles = exchange.fetch_ohlcv("BTC/KRW", "1d", limit=limit)
        return [float(c[4]) for c in candles]
    except Exception as e:
        print(f"  [경고] BTC 일봉 조회 실패: {e}", flush=True)
        return []


def check_consecutive_above_ema(closes: list[float], consec: int = _CONSEC_DAYS,
                                 ema_period: int = _EMA_PERIOD) -> bool:
    """최근 consec 일이 모두 EMA(ema_period) 위에 있으면 True.

    closes: 오래된 순 → 최신 순 정렬된 일봉 종가 리스트
    """
    if len(closes) < ema_period:
        print(f"  [경고] 데이터 부족: {len(closes)}개 < {ema_period}개", flush=True)
        return False

    series = pd.Series(closes)
    ema = series.ewm(span=ema_period, adjust=False).mean()

    # 최근 consec 개 봉 전부 close > EMA 이어야 함
    recent_closes = series.iloc[-consec:]
    recent_ema = ema.iloc[-consec:]

    result = all(c > e for c, e in zip(recent_closes, recent_ema))
    return result


# ════════════════════════════════════════════════════════════════════
# 상태 파일 I/O
# ════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """workspace/vb_recheck_last.json 로드. 없으면 초기값 반환."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_trigger_ts": 0, "last_7day_above": False}


def save_state(state: dict) -> None:
    """workspace/vb_recheck_last.json 갱신."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_cooldown_passed(last_trigger_ts: int, cooldown_days: int = _COOLDOWN_DAYS) -> bool:
    """마지막 트리거로부터 cooldown_days 이상 경과했으면 True."""
    if last_trigger_ts == 0:
        return True  # 최초 실행 → 쿨다운 없음
    elapsed = time.time() - last_trigger_ts
    return elapsed >= cooldown_days * 86400


# ════════════════════════════════════════════════════════════════════
# 보고서 생성
# ════════════════════════════════════════════════════════════════════

def generate_report(date_str: str) -> Path:
    """workspace/reports/YYYYMMDD_vb_drymake_auto_recheck.md 생성 후 경로 반환."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORTS_DIR / f"{date_str}_vb_drymake_auto_recheck.md"

    content = f"""# VB 재검증 자동 트리거 보고서

생성일시: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC (KST +9)
트리거 조건: BTC 일봉 close > EMA200 7일 연속 충족

---

## 재검증 절차

### 1. 서버에서 vb_state.json 집계

```bash
# AWS 서버 접속 후 실행
cd /home/ubuntu/BitCoin_Trade

# VB 상태 현황 집계
python3 -c "
import json
from pathlib import Path
state = json.loads(Path('workspace/vb_state.json').read_text())
positions = state.get('positions', {{}})
history = state.get('history', [])
wins = [h for h in history if h.get('return_pct', 0) > 0]
print(f'보유: {{len(positions)}}건')
print(f'거래: {{len(history)}}건 / 승률: {{len(wins)}}/{{len(history)}}')
if history:
    avg = sum(h.get('return_pct',0) for h in history) / len(history)
    print(f'평균수익: {{avg:+.2f}}%')
    last5 = history[-5:]
    for h in last5:
        print(f'  {{h[\"symbol\"]}} {{h.get(\"return_pct\",0):+.1f}}% ({{h.get(\"reason\",\"\")}})')
"
```

### 2. journalctl VB 로그 집계

```bash
# 최근 7일 VB 관련 로그 추출
sudo journalctl -u btc-trader --since "7 days ago" \\
    | grep -E "\\[VB\\]" \\
    | tail -100
```

### 3. DRY-RUN 집계 스크립트

```bash
# VB DRY-RUN 7일 재집계
PYTHONUTF8=1 python3 scripts/daily_live.py --vb-recheck 2>&1 | tee /tmp/vb_recheck_{date_str}.log
```

### 4. 결과 기록

재검증 완료 후 `workspace/reports/{date_str}_vb_drymake_recheck_result.md` 에 결과를 기록합니다.

---

## 판단 기준

| 지표 | 기준값 | 판단 |
|------|--------|------|
| 승률 | > 50% | PASS |
| 평균수익 | > 0% | PASS |
| 최대연속손실 | < 3회 | PASS |

---

*자동 생성 — vb_recheck_trigger.py*
"""
    report_path.write_text(content, encoding="utf-8")
    return report_path


# ════════════════════════════════════════════════════════════════════
# 텔레그램 알림
# ════════════════════════════════════════════════════════════════════

async def send_telegram_notify(report_path: Path) -> None:
    """텔레그램으로 VB 재검증 트리거 발동 알림 전송."""
    try:
        from services.execution.telegram_bot import send_message
        msg = (
            "VB 재검증 트리거 발동\n\n"
            "BTC EMA200 7일 연속 상향 조건 충족\n"
            f"보고서: {report_path.name}\n\n"
            "서버에서 vb_state.json + journalctl 집계 후\n"
            "재검증 결과를 workspace/reports/ 에 기록해주세요."
        )
        await send_message(msg)
        print("  [알림] 텔레그램 전송 완료", flush=True)
    except Exception as e:
        print(f"  [경고] 텔레그램 전송 실패: {e}", flush=True)


# ════════════════════════════════════════════════════════════════════
# 메인 로직
# ════════════════════════════════════════════════════════════════════

def run(notify: bool = False, force: bool = False) -> bool:
    """트리거 체크 및 처리. 트리거 발동 시 True 반환.

    Args:
        notify: True이면 텔레그램 알림 전송 (조건 충족 시)
        force:  True이면 조건 무시하고 강제 발동
    """
    now_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    print(f"[VB 재검증 트리거] {now_str} 체크 시작", flush=True)

    state = load_state()

    if force:
        print("  [--force] 조건 무시하고 강제 발동", flush=True)
        triggered = True
    else:
        # 조건 1: BTC 7일 연속 EMA200 상향
        closes = fetch_btc_daily_closes()
        above_7day = check_consecutive_above_ema(closes)
        state["last_7day_above"] = above_7day
        print(f"  BTC 7일 연속 EMA200 상향: {above_7day}", flush=True)

        if not above_7day:
            print("  → 조건 미충족. 트리거 발동 안 함.", flush=True)
            save_state(state)
            return False

        # 조건 2: 쿨다운 경과
        cooldown_ok = is_cooldown_passed(state.get("last_trigger_ts", 0))
        print(f"  쿨다운({_COOLDOWN_DAYS}일) 경과: {cooldown_ok}", flush=True)

        if not cooldown_ok:
            last_ts = state.get("last_trigger_ts", 0)
            last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"  → 마지막 트리거 {last_dt} — 쿨다운 미경과. 발동 안 함.", flush=True)
            save_state(state)
            return False

        triggered = True

    # 트리거 발동 처리
    print("  → 트리거 발동!", flush=True)
    report_path = generate_report(now_str)
    print(f"  보고서 생성: {report_path}", flush=True)

    state["last_trigger_ts"] = int(time.time())
    save_state(state)
    print(f"  상태 파일 갱신: {_STATE_FILE}", flush=True)

    if notify:
        asyncio.run(send_telegram_notify(report_path))

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VB 재검증 자동 트리거 — BTC EMA200 7일 연속 상향 시 재집계 보고서 생성"
    )
    parser.add_argument(
        "--notify", action="store_true",
        help="조건 충족 시 텔레그램 알림 전송"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="조건 무시하고 강제 발동"
    )
    args = parser.parse_args()

    triggered = run(notify=args.notify, force=args.force)
    sys.exit(0 if triggered else 0)  # 항상 0 (cron 재시도 방지)


if __name__ == "__main__":
    main()
