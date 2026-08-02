"""일일 페이퍼 트레이딩 체크 스크립트 + 아침 브리핑 (텔레그램).

매일 09:32 KST (regime_check 완료 2분 후) 실행 권장 — cron: 32 0 * * *

lessons #38 (20260801_2, 아침 브리핑 채널 부재):
    2026-08-01 이전 crontab에는 아침 09:xx KST 텔레그램 브리핑이 없었고
    (원래 있던 CRON_LIVE는 lessons #33/#34로 제거), 사용자 접점은 18:00 daily_report 단독.
    아침 봇 상태·계좌·레짐·cron 정합 무브리핑 상태였음.
    본 스크립트가 --notify 시 통합 아침 브리핑을 발송.

실행 방법:
  # 콘솔만 (기존 동작 유지)
  python scripts/daily_check.py

  # 텔레그램 아침 브리핑 발송 (cron 용)
  python scripts/daily_check.py --notify

Cron (AWS, UTC):
  32 0 * * * cd /home/ubuntu/BitCoin_Trade && PYTHONUTF8=1 PYTHONPATH=/home/ubuntu/BitCoin_Trade \\
             .venv/bin/python scripts/daily_check.py --notify >> /var/log/btc_report.log 2>&1

브리핑 구성 (--notify 시):
  1. 레짐 상태 (regime_state.json 기반)
  2. 봇 상태 (systemd active, PID, uptime, 좀비 카운트)
  3. 계좌 현황 (총평가, KRW 현금, 포지션 수, 오늘까지 실현 PnL 누적)
  4. cron 정합 (BitCoin_Trade crontab 라인 카운트 ≥ 8, lessons #36)
  5. 이상 항목 (있으면 나열)

전략 변경:
  services/execution/config.py 의 STRATEGY 값을 수정
  가능한 값: dc_atr, rsi_ema, ensemble, regime, mtf, volume, composite
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.paper_trading.runner import run_daily, fetch_recent_ohlcv  # noqa: E402
from services.execution.config import STRATEGY  # noqa: E402
from services.paper_trading import strategy_rsi_ema  # noqa: E402

KST = timezone(timedelta(hours=9))
MULTI_STATE = ROOT / "workspace" / "multi_trading_state.json"
REGIME_STATE = ROOT / "workspace" / "regime_state.json"


# ═══════════════════════════════════════════════════════════════════
# 콘솔 페이퍼 트레이딩 체크 (기존 동작)
# ═══════════════════════════════════════════════════════════════════
async def _run_console_check() -> None:
    print("=" * 60)
    print("일일 페이퍼 트레이딩 체크")
    print("=" * 60)

    # 메인 전략: config.py 의 STRATEGY 설정 사용
    print(f"\n[메인 전략] {STRATEGY}  (변경: services/execution/config.py)")
    print("-" * 40)
    await run_daily()

    # 보조 전략: RSI(10) + EMA(150) — 관찰용
    print("\n[보조 전략] RSI(10)>50/<45 + EMA(150) — 관찰용")
    print("-" * 40)
    try:
        df = await fetch_recent_ohlcv(days=210)
        indicators = strategy_rsi_ema.get_indicators(df)
        entry_signal = strategy_rsi_ema.check_entry(df)
        exit_signal = strategy_rsi_ema.check_exit(df)

        print(f"  RSI(10): {indicators['rsi']}")
        print(f"  EMA(150): {indicators['ema150']:,.0f}")
        print(f"  종가 > EMA: {'예' if indicators['above_ema'] else '아니오'}")
        print(f"  매수 신호: {'*** 발생! ***' if entry_signal else '없음'}")
        print(f"  매도 신호: {'*** 발생! ***' if exit_signal else '없음'}")
    except Exception as e:
        print(f"  보조 전략 오류: {e}")

    print("\n" + "=" * 60)


# ═══════════════════════════════════════════════════════════════════
# 아침 브리핑 섹션 (텔레그램 --notify 전용)
# ═══════════════════════════════════════════════════════════════════

def _fmt_num(v, spec: str = ",.0f") -> str:
    """lessons #12 — .get() 결과가 None이면 f-string 포매팅 크래시.

    None/비수치 → '-' 반환. 정상 값은 지정된 spec으로 포매팅.
    """
    if v is None:
        return "-"
    try:
        return format(float(v), spec)
    except (TypeError, ValueError):
        return "-"


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _section_regime() -> list[str]:
    """레짐 상태 섹션 — regime_state.json 기반."""
    out: list[str] = ["🌤 레짐 상태"]
    state = _load_json(REGIME_STATE)
    if not state:
        out.append("  - regime_state.json 없음 (regime_check.py 미실행?)")
        return out
    current = state.get("current", "UNKNOWN")
    prev = state.get("prev", "UNKNOWN")
    signals = state.get("recent_signals", [])
    enabled = state.get("enabled", False)
    last_ts = state.get("last_decided_ts", 0)
    last_str = "N/A"
    if last_ts:
        try:
            last_str = datetime.fromtimestamp(last_ts, tz=KST).strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_str = f"ts={last_ts}"
    out.append(f"  - 현재: {current} (이전 {prev}) — 스위칭 {'ON' if enabled else 'OFF'}")
    out.append(f"  - 최근 시그널 큐: {signals}")
    out.append(f"  - 마지막 판정: {last_str} KST")
    return out


def _section_bot() -> list[str]:
    """봇 상태 섹션 — systemd + 좀비 카운트 (로컬 시 SKIP)."""
    out: list[str] = ["🤖 봇 상태"]
    # 서버에서만 systemctl 조회 (로컬 콘솔 실행 시 SKIP)
    is_server = os.path.exists("/home/ubuntu/BitCoin_Trade")
    if not is_server:
        out.append("  - (로컬 실행 — systemctl SKIP)")
        return out
    # active 여부 + main PID
    try:
        active = subprocess.check_output(
            ["systemctl", "is-active", "btc-trader"], timeout=5
        ).decode().strip()
        out.append(f"  - btc-trader.service: {active}")
    except Exception as e:
        out.append(f"  - btc-trader.service 조회 실패: {type(e).__name__}")
    # MainPID
    try:
        pid_out = subprocess.check_output(
            ["systemctl", "show", "btc-trader", "-p", "MainPID", "-p", "ActiveEnterTimestamp"],
            timeout=5,
        ).decode().strip()
        for line in pid_out.splitlines():
            out.append(f"  - {line}")
    except Exception:
        pass
    # 좀비 카운트 — daily_live.py 프로세스 (systemd 아닌 것)
    try:
        pgrep_out = subprocess.check_output(
            ["pgrep", "-af", "daily_live.py"], timeout=5
        ).decode().strip()
        n_total = sum(1 for ln in pgrep_out.splitlines() if ln.strip())
        n_realtime = sum(1 for ln in pgrep_out.splitlines() if "--realtime" in ln)
        n_non_realtime = n_total - n_realtime
        out.append(f"  - daily_live.py 프로세스: 총 {n_total} (realtime {n_realtime}, non-realtime {n_non_realtime})")
        if n_non_realtime >= 1:
            out.append(f"  - ⚠ non-realtime 좀비 감지 (lessons #34 회귀) — 즉시 kill 필요")
    except subprocess.CalledProcessError:
        out.append("  - daily_live.py 프로세스: 0개 (⚠ systemd 미가동 의심)")
    except Exception as e:
        out.append(f"  - pgrep 조회 실패: {type(e).__name__}")
    return out


def _section_account() -> list[str]:
    """계좌 현황 섹션 — upbit_client + multi_trading_state."""
    out: list[str] = ["💰 계좌 현황"]
    # 잔고
    try:
        from services.execution.upbit_client import get_balance  # noqa: WPS433
        bal = get_balance()
        out.append(f"  - 총평가: {_fmt_num(bal.get('total_krw'))} KRW")
        out.append(f"  - 현금 KRW: {_fmt_num(bal.get('krw'))}")
        out.append(f"  - BTC 평가: {_fmt_num(bal.get('btc_krw_value'))}")
        out.append(f"  - 알트 평가: {_fmt_num(bal.get('alts_krw_value'))}")
    except Exception as e:
        out.append(f"  - 잔고 조회 실패: {type(e).__name__} ({e})")
    # 포지션 수 + 오늘 실현 PnL
    state = _load_json(MULTI_STATE)
    positions = state.get("positions", {})
    closed = state.get("closed_trades", [])
    out.append(f"  - 보유 포지션: {len(positions)}종목")
    # 어제(KST) 마감된 거래 손익
    today = datetime.now(tz=KST).date()
    yesterday = today - timedelta(days=1)
    y_closed = []
    for t in closed:
        exit_ts = t.get("exit_ts") or t.get("closed_at") or t.get("exit_time")
        if not exit_ts:
            continue
        try:
            if isinstance(exit_ts, (int, float)):
                dt = datetime.fromtimestamp(exit_ts, tz=KST).date()
            else:
                dt = datetime.fromisoformat(str(exit_ts).replace("Z", "+00:00")).astimezone(KST).date()
        except Exception:
            continue
        if dt == yesterday:
            y_closed.append(t)
    # lessons #12 회귀 방지: return_pct가 명시적 None이면 sum/비교 크래시 → `or 0` 방어
    if y_closed:
        y_sum = sum((t.get("return_pct") or 0) for t in y_closed)
        y_wins = sum(1 for t in y_closed if (t.get("return_pct") or 0) > 0)
        out.append(f"  - 어제({yesterday}) 마감: {len(y_closed)}건 승률 {y_wins}/{len(y_closed)} 합계 {y_sum:+.2f}%")
    else:
        out.append(f"  - 어제({yesterday}) 마감: 0건")
    # 누적
    if closed:
        c_sum = sum((t.get("return_pct") or 0) for t in closed)
        c_wins = sum(1 for t in closed if (t.get("return_pct") or 0) > 0)
        out.append(f"  - 누적: {len(closed)}건 승률 {c_wins}/{len(closed)} ({c_wins*100//len(closed)}%) 합계 {c_sum:+.1f}%")
    return out


def _section_cron() -> list[str]:
    """cron 정합 섹션 — BitCoin_Trade 라인 카운트 (lessons #36)."""
    out: list[str] = ["⏰ cron 정합"]
    is_server = os.path.exists("/home/ubuntu/BitCoin_Trade")
    if not is_server:
        out.append("  - (로컬 실행 — crontab SKIP)")
        return out
    try:
        cron_txt = subprocess.check_output(["crontab", "-l"], timeout=5).decode()
        lines = [ln for ln in cron_txt.splitlines() if "BitCoin_Trade" in ln and not ln.lstrip().startswith("#")]
        n = len(lines)
        # baseline 9 (기존 8 + daily_check briefing 신설)
        status = "OK" if n >= 9 else "⚠ 누락 의심"
        out.append(f"  - BitCoin_Trade 라인: {n}개 (baseline 9+) — {status}")
        # daily_check briefing 등록 확인
        has_briefing = any("daily_check.py" in ln for ln in lines)
        out.append(f"  - 아침 브리핑(daily_check.py): {'등록됨' if has_briefing else '⚠ 미등록'}")
    except Exception as e:
        out.append(f"  - crontab 조회 실패: {type(e).__name__}")
    return out


def _section_anomalies() -> list[str]:
    """이상 항목 요약 — state↔balance 불일치, 5연패 cooldown, 서킷브레이커."""
    out: list[str] = ["🚨 이상 항목"]
    anomalies: list[str] = []
    state = _load_json(MULTI_STATE)
    # cooldown_until (5연패 자동 중단)
    cd = state.get("cooldown_until", 0)
    if cd:
        try:
            cd_dt = datetime.fromtimestamp(cd, tz=KST) if isinstance(cd, (int, float)) else None
            if cd_dt and cd_dt > datetime.now(tz=KST):
                anomalies.append(f"5연패 cooldown 진행 중 (해제 예정: {cd_dt:%Y-%m-%d %H:%M} KST)")
        except Exception:
            pass
    # 서킷브레이커
    cb = state.get("circuit_breaker", {})
    if isinstance(cb, dict):
        level = cb.get("level", 0)
        if level and int(level) >= 1:
            anomalies.append(f"서킷브레이커 L{level} 발동 중")
    # 좀비 (서버 한정) — bot 섹션에서 이미 다뤘지만 요약 재확인
    # state ↔ balance drift는 별도 헬스체크가 매시 5분 담당 (여기선 요약만)
    if not anomalies:
        out.append("  - 없음")
    else:
        for a in anomalies:
            out.append(f"  - {a}")
    return out


def _build_briefing() -> str:
    now = datetime.now(tz=KST)
    header = f"🌅 *아침 브리핑* — {now:%Y-%m-%d %H:%M} KST\n"
    sections: list[list[str]] = [
        _section_regime(),
        _section_bot(),
        _section_account(),
        _section_cron(),
        _section_anomalies(),
    ]
    body_parts: list[str] = []
    for sec in sections:
        body_parts.append("\n".join(sec))
    return header + "\n" + "\n\n".join(body_parts)


async def _send_briefing() -> bool:
    """아침 브리핑 텔레그램 발송.

    lessons #39 (20260802_1) — send_message는 이제 status 200 여부를 반영한 bool을
    반환하며 Markdown 400은 자동 plain fallback. 여기서는 반환값을 신뢰해
    성공/실패 로그와 exit code를 명확히 남긴다 (cron 로그 진단 근거).
    """
    msg = _build_briefing()
    print("─" * 60)
    print(msg)
    print("─" * 60)
    try:
        from services.execution.telegram_bot import send_message  # noqa: WPS433
        ok = await send_message(msg)
        if ok:
            print("[아침 브리핑] 텔레그램 발송 성공")
            return True
        print("[아침 브리핑] 텔레그램 발송 실패 — send_message가 False 반환 (상세는 [telegram] 로그 참조)")
        return False
    except Exception as e:
        print(f"[아침 브리핑] 텔레그램 발송 실패: {type(e).__name__} ({e})")
        return False


# ═══════════════════════════════════════════════════════════════════
# 엔트리
# ═══════════════════════════════════════════════════════════════════
async def main() -> None:
    parser = argparse.ArgumentParser(description="일일 페이퍼 트레이딩 체크 + 아침 브리핑")
    parser.add_argument("--notify", action="store_true", help="텔레그램 아침 브리핑 발송")
    parser.add_argument("--skip-console", action="store_true", help="콘솔 페이퍼 체크 SKIP (--notify 전용 모드)")
    args = parser.parse_args()

    if not args.skip_console:
        try:
            await _run_console_check()
        except Exception as e:
            print(f"[콘솔 체크 오류] {type(e).__name__}: {e}")

    if args.notify:
        ok = await _send_briefing()
        # lessons #39 — 발송 실패 시 non-zero exit → cron 실행 자체는 성공했다는 착각 차단
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
