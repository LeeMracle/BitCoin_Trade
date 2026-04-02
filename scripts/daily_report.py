"""일일 자동 보고 스크립트 (텔레그램).

매일 09:10 KST (= UTC 00:10) 실행.
composite(스윙) + VB(변동성 돌파) 현황을 텔레그램으로 발송.

실행:
  python scripts/daily_report.py

Cron (AWS, UTC):
  10 0 * * * cd /home/ubuntu/BitCoin_Trade && .venv/bin/python scripts/daily_report.py >> /var/log/btc_report.log 2>&1

Windows 작업 스케줄러:
  schtasks /create /tn "BTC_DailyReport" /tr "...\\python.exe ...\\scripts\\daily_report.py" /sc daily /st 09:10
"""
import sys, io, asyncio, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send

# 상태 파일 경로
MULTI_STATE = ROOT / "workspace" / "multi_trading_state.json"
VB_STATE = ROOT / "workspace" / "vb_state.json"

KST = timezone(timedelta(hours=9))


def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _build_report() -> str:
    """텔레그램 일일 보고 메시지 생성."""
    now_kst = datetime.now(tz=KST)
    date_str = now_kst.strftime("%Y-%m-%d")
    lines = [f"📋 *일일 보고* ({date_str})\n"]

    # ── 1. 업비트 실계좌 잔고 ──
    try:
        from services.execution.upbit_client import get_balance
        bal = get_balance()
        lines.append("💰 *계좌*")
        lines.append(f"  KRW: {bal['krw']:,.0f}")
        lines.append(f"  총평가: {bal['total_krw']:,.0f}")
    except Exception:
        lines.append("💰 *계좌*: 조회 실패 (API 키 미설정?)")

    # ── 2. Composite(스윙) 현황 ──
    lines.append("")
    multi = _load_json(MULTI_STATE)
    positions = multi.get("positions", {})
    closed = multi.get("closed_trades", [])

    lines.append(f"📈 *스윙 (composite)*")
    lines.append(f"  보유: {len(positions)}/5종목")

    if positions:
        for sym, pos in positions.items():
            entry_p = pos.get("entry_price", 0)
            stop_p = pos.get("trail_stop", 0)
            lines.append(f"  • {sym}")
            lines.append(f"    진입: {entry_p:,.0f} | 스탑: {stop_p:,.0f}")

    if closed:
        wins = sum(1 for t in closed if t.get("return_pct", 0) > 0)
        total_ret = sum(t.get("return_pct", 0) for t in closed)
        lines.append(f"  거래: {len(closed)}회 | 승률: {wins}/{len(closed)} ({wins*100//len(closed)}%)")
        lines.append(f"  누적수익: {total_ret:+.1f}%")
    else:
        lines.append(f"  거래: 0회")

    # ── 3. VB(변동성 돌파) 현황 ──
    lines.append("")
    vb = _load_json(VB_STATE)
    vb_pos = vb.get("positions", {})
    vb_hist = vb.get("history", [])

    from services.execution.config import VB_ENABLED, VB_DRY_RUN
    mode_str = "DRY-RUN" if VB_DRY_RUN else "실전"
    status_str = f"{'활성' if VB_ENABLED else '비활성'} ({mode_str})"

    lines.append(f"⚡ *VB (변동성 돌파)* — {status_str}")
    lines.append(f"  보유: {len(vb_pos)}종목")

    if vb_pos:
        for sym, pos in vb_pos.items():
            entry_p = pos.get("entry_price", 0)
            lines.append(f"  • {sym} @ {entry_p:,.0f}")

    if vb_hist:
        wins = sum(1 for h in vb_hist if h.get("return_pct", 0) > 0)
        avg_ret = sum(h.get("return_pct", 0) for h in vb_hist) / len(vb_hist)
        lines.append(f"  거래: {len(vb_hist)}건 | 승률: {wins}/{len(vb_hist)} ({wins*100//len(vb_hist)}%)")
        lines.append(f"  평균수익: {avg_ret:+.2f}%")
        # 최근 3건
        for h in vb_hist[-3:]:
            e = "🟢" if h.get("return_pct", 0) > 0 else "🔴"
            sym = h.get("symbol", "?")
            ret = h.get("return_pct", 0)
            lines.append(f"    {e} {sym} {ret:+.1f}%")
    else:
        lines.append(f"  거래: 0건")

    return "\n".join(lines)


async def main():
    print(f"[{datetime.now(tz=KST):%Y-%m-%d %H:%M}] 일일 보고 발송 중...")

    msg = _build_report()
    print(msg)

    ok = await send(msg)
    if ok:
        print("텔레그램 발송 성공")
    else:
        print("텔레그램 발송 실패 (토큰/채팅ID 확인 필요)")


if __name__ == "__main__":
    asyncio.run(main())
