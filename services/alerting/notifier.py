"""텔레그램 알림 모듈.

plan 20260503 P3-3: 알림 3등급 (level=critical/report/silent).
- critical: 🚨 즉시 조치 필요 (인증 실패, 봇 down, 매수 차단 등)
- report:   📋 정기 보고 (일일 보고, 매매 결과 등)
- silent:   콘솔/로그만, 텔레그램 발송 안 함
"""
import os
import sys
from pathlib import Path
import aiohttp
from dotenv import load_dotenv

# 프로젝트 루트 기준 .env 로드
_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

LEVEL_CRITICAL = "critical"
LEVEL_REPORT = "report"
LEVEL_SILENT = "silent"

_LEVEL_PREFIX = {
    LEVEL_CRITICAL: "🚨 ",
    LEVEL_REPORT: "📋 ",
    LEVEL_SILENT: "",
}


async def send(message: str,
               parse_mode: str | None = "Markdown",
               level: str = LEVEL_REPORT) -> bool:
    """텔레그램 메시지 발송.

    Args:
        message: 발송 본문.
        parse_mode: Markdown/HTML/None. 헬스체크 섹션 등 escape 안 된 케이스는 None.
        level: critical/report/silent. silent는 발송 안 함, journalctl만.
    """
    if level == LEVEL_SILENT:
        # 텔레그램 발송 없이 stderr/journalctl로만 기록
        print(f"[silent] {message[:200]}", file=sys.stderr, flush=True)
        return True

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    # 등급 prefix 추가 (메시지 첫 줄에)
    prefix = _LEVEL_PREFIX.get(level, "")
    full_message = prefix + message if prefix and not message.startswith(prefix) else message

    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": full_message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ok = resp.status == 200
                if not ok and level == LEVEL_CRITICAL:
                    # cto P3 review #4: critical 실패 시 journalctl 강제 기록
                    print(f"[notifier-critical-FAIL] status={resp.status} message={message[:200]}",
                          file=sys.stderr, flush=True)
                return ok
    except Exception as e:
        if level == LEVEL_CRITICAL:
            print(f"[notifier-critical-FAIL] {type(e).__name__}: {message[:200]}",
                  file=sys.stderr, flush=True)
        return False


async def send_critical(message: str, parse_mode: str | None = None) -> bool:
    """🚨 critical 등급 — 즉시 조치 필요. 실패 시 journalctl 강제 기록."""
    return await send(message, parse_mode=parse_mode, level=LEVEL_CRITICAL)


async def send_report(message: str, parse_mode: str | None = "Markdown") -> bool:
    """📋 report 등급 — 정기 보고."""
    return await send(message, parse_mode=parse_mode, level=LEVEL_REPORT)


async def send_silent(message: str) -> bool:
    """silent 등급 — 텔레그램 미발송, journalctl만."""
    return await send(message, level=LEVEL_SILENT)


# 자주 쓰는 알림 템플릿
async def notify_trade(action: str, price: float, amount_krw: float, run_id: str = ""):
    emoji = "🟢" if action == "BUY" else "🔴"
    msg = (
        f"{emoji} *{action}* 신호\n"
        f"가격: {price:,.0f} KRW\n"
        f"금액: {amount_krw:,.0f} KRW\n"
        f"run_id: `{run_id}`"
    )
    return await send(msg)


async def notify_daily_summary(date: str, equity: float, daily_return: float):
    emoji = "📈" if daily_return >= 0 else "📉"
    msg = (
        f"{emoji} *일일 리포트* ({date})\n"
        f"평가금액: {equity:,.0f} KRW\n"
        f"일간 수익률: {daily_return:+.2f}%"
    )
    return await send(msg)


async def notify_error(error_msg: str):
    msg = f"⚠️ *오류 발생*\n```\n{error_msg[:500]}\n```"
    return await send(msg)
