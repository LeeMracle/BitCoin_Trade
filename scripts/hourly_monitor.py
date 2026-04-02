"""1시간 단위 BIT 모니터링 보고 (텔레그램).

AWS 서버의 실제 봇 상태를 SSH로 조회하여 보고.
- 서비스 가동 상태
- 스윙(composite) 포지션 + 성과
- VB(변동성 돌파) DRY-RUN 성과
- 레짐(F&G), 잔고, 에러

실행:
  python scripts/hourly_monitor.py
"""
import sys, io, asyncio, json, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send

KST = timezone(timedelta(hours=9))

# AWS 접속 정보
AWS_HOST = "ubuntu@13.124.82.122"
PEM_CANDIDATES = [
    Path.home() / "Downloads" / "upbit-trading-key-seoul.pem",
    Path.home() / "upbit-trading-key-seoul.pem",
    Path.home() / ".ssh" / "upbit-trading-key-seoul.pem",
]
PROJECT_DIR = "/home/ubuntu/BitCoin_Trade"


def _find_pem() -> Path | None:
    for p in PEM_CANDIDATES:
        if p.exists():
            return p
    return None


def _ssh(cmd: str, timeout: int = 15) -> str:
    """AWS SSH 명령 실행."""
    pem = _find_pem()
    if not pem:
        return ""
    try:
        result = subprocess.run(
            ["ssh", "-i", str(pem), "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", AWS_HOST, cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception as e:
        return f"[SSH 오류: {e}]"


def _fetch_aws_data() -> dict:
    """AWS 서버에서 봇 상태 일괄 조회."""
    script = f"""cd {PROJECT_DIR} && .venv/bin/python -c '
import json
from pathlib import Path

data = {{}}

# 서비스 상태
import subprocess
r = subprocess.run(["systemctl", "is-active", "btc-trader"], capture_output=True, text=True)
data["service"] = r.stdout.strip()
r2 = subprocess.run(["systemctl", "show", "btc-trader", "--property=ActiveEnterTimestamp"], capture_output=True, text=True)
data["uptime"] = r2.stdout.strip().replace("ActiveEnterTimestamp=", "")

# 잔고
try:
    import os
    from dotenv import load_dotenv
    load_dotenv(Path("{PROJECT_DIR}/services/.env"))
    from services.execution.upbit_client import get_balance
    b = get_balance()
    data["balance"] = b
except Exception as e:
    data["balance"] = {{"error": str(e)}}

# 스윙 상태
p = Path("workspace/multi_trading_state.json")
data["swing"] = json.loads(p.read_text()) if p.exists() else {{}}

# VB 상태
p2 = Path("workspace/vb_state.json")
data["vb"] = json.loads(p2.read_text()) if p2.exists() else {{}}

# 최근 로그 (1시간)
import os
log_path = Path("workspace/multi_trading_log.jsonl")
recent = []
if log_path.exists():
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    for line in log_path.read_text().strip().split(chr(10)):
        try:
            e = json.loads(line)
            if e.get("logged_at","") >= cutoff:
                recent.append(e)
        except:
            pass
data["recent_log"] = recent

print(json.dumps(data, ensure_ascii=False))
'"""
    raw = _ssh(script, timeout=20)
    if not raw or raw.startswith("[SSH"):
        return {"error": raw or "SSH 연결 실패"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": f"JSON 파싱 실패: {raw[:200]}"}


async def _check_regime() -> tuple[str, int]:
    """F&G 지수 조회."""
    try:
        from services.market_data.fetcher import fetch_fear_greed
        today = datetime.now(tz=KST).strftime("%Y-%m-%d")
        yesterday = (datetime.now(tz=KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        fng = await fetch_fear_greed(yesterday, today)
        if fng:
            val = int(fng[-1]["value"])
            if val <= 25:
                return "CRISIS (극공포)", val
            elif val <= 40:
                return "FEAR (공포)", val
            elif val <= 60:
                return "NEUTRAL (중립)", val
            elif val <= 75:
                return "GREED (탐욕)", val
            else:
                return "EUPHORIA (극탐욕)", val
    except Exception:
        pass
    return "확인 불가", 0


async def build_report() -> str:
    """1시간 단위 모니터링 보고."""
    now_kst = datetime.now(tz=KST)
    time_str = now_kst.strftime("%H:%M")

    lines = [f"*[1시간단위] 모니터링보고*  {time_str} KST\n"]

    # AWS 데이터 조회
    data = _fetch_aws_data()
    if "error" in data and not isinstance(data.get("error"), dict):
        lines.append(f"서비스: 조회 실패 — {data['error']}")
        regime, fng_val = await _check_regime()
        lines.append(f"레짐: {regime} | F&G {fng_val}%")
        return "\n".join(lines)

    # 1. 서비스 상태
    svc = data.get("service", "unknown")
    uptime = data.get("uptime", "")
    svc_display = f"active (running)" if svc == "active" else svc
    lines.append(f"서비스: {svc_display}")

    # 2. 레짐
    regime, fng_val = await _check_regime()
    lines.append(f"레짐: {regime} | composite | F&G {fng_val}%")

    # 3. 잔고
    bal = data.get("balance", {})
    if "error" not in bal:
        total = bal.get("total_krw", 0)
        lines.append(f"평가금액: {total:,.0f}원")
    else:
        lines.append(f"평가금액: 조회 실패")

    # 4. 스윙 포지션
    swing = data.get("swing", {})
    positions = swing.get("positions", {})
    closed = swing.get("closed_trades", [])
    lines.append(f"보유: {len(positions)} 종목")

    if positions:
        for sym, pos in positions.items():
            entry = pos.get("entry_price", 0)
            highest = pos.get("highest", 0)
            stop = pos.get("trail_stop", 0)
            if entry > 0 and highest > 0:
                gain_pct = (highest / entry - 1) * 100
                lines.append(f"  {sym} 진입:{entry:,.0f} 고점:{highest:,.0f} ({gain_pct:+.1f}%)")
            else:
                lines.append(f"  {sym} 진입:{entry:,.0f} 스탑:{stop:,.0f}")

    # 5. 최근 활동
    lines.append("")
    prev_time = (now_kst - timedelta(hours=1)).strftime("%H:%M")
    recent = data.get("recent_log", [])
    lines.append("최근 활동:")
    if recent:
        for r in recent[-5:]:
            action = r.get("action", "?")
            symbol = r.get("symbol", "?")
            price = r.get("price", 0)
            lines.append(f"  {action} {symbol} @ {price:,.0f}")
    else:
        lines.append(f"  {prev_time}~{time_str} — 변동 없음")

    # 6. VB DRY-RUN (그림자 모드)
    lines.append("")
    vb = data.get("vb", {})
    vb_pos = vb.get("positions", {})
    vb_hist = vb.get("history", [])

    lines.append("그림자 모드 (VB DRY-RUN):")
    lines.append(f"  보유: {len(vb_pos)} 종목")
    if vb_pos:
        for sym, pos in vb_pos.items():
            lines.append(f"  {sym} @ {pos.get('entry_price',0):,.0f}")

    if vb_hist:
        wins = sum(1 for h in vb_hist if h.get("return_pct", 0) > 0)
        total_trades = len(vb_hist)
        avg_ret = sum(h.get("return_pct", 0) for h in vb_hist) / total_trades
        wr_pct = wins / total_trades * 100
        lines.append(f"  거래: {total_trades}건 | 승률 {wins}/{total_trades} ({wr_pct:.0f}%) | 평균 {avg_ret:+.2f}%")
        # 최근 3건
        for h in vb_hist[-3:]:
            e = "+" if h.get("return_pct", 0) >= 0 else ""
            lines.append(f"    {h.get('symbol','')} {e}{h.get('return_pct',0):.1f}% ({h.get('reason','')})")
    else:
        lines.append(f"  거래: 0건")

    # 7. 에러
    errors = []
    for h in vb_hist[-5:]:
        if "error" in str(h.get("reason", "")).lower():
            errors.append(f"VB {h.get('symbol','')}: {h.get('reason','')}")
    lines.append(f"\n에러: {', '.join(errors) if errors else '없음'}")

    # 8. 개선사항
    improvements = []
    if closed:
        swing_wins = sum(1 for t in closed if t.get("return_pct", 0) > 0)
        swing_wr = swing_wins / len(closed) * 100
        swing_total_ret = sum(t.get("return_pct", 0) for t in closed)
        if swing_wr < 30:
            improvements.append(f"스윙 승률 {swing_wr:.0f}% ({swing_wins}/{len(closed)}, 누적 {swing_total_ret:+.1f}%) — 극공포장 영향 모니터링")
    if vb_hist and len(vb_hist) >= 3:
        recent_vb = vb_hist[-3:]
        recent_losses = sum(1 for h in recent_vb if h.get("return_pct", 0) < -3)
        if recent_losses >= 2:
            improvements.append("VB 최근 3건 중 큰 손실 2건+ — K값/필터 검토")

    if improvements:
        lines.append("\n개선사항:")
        for imp in improvements:
            lines.append(f"  - {imp}")

    return "\n".join(lines)


async def main():
    now = datetime.now(tz=KST)
    hour = now.hour
    if hour < 9 or hour >= 18:
        print(f"[{now:%H:%M}] 모니터링 시간 외 (09:00~18:00) — 건너뜀")
        return

    report = await build_report()
    print(report)

    ok = await send(report)
    print(f"\n텔레그램 {'발송 성공' if ok else '발송 실패'}")


if __name__ == "__main__":
    asyncio.run(main())
