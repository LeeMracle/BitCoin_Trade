"""텔레그램 알림 모듈."""
import os
from pathlib import Path
import aiohttp
from dotenv import load_dotenv

# 프로젝트 루트 기준 .env 로드
_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status == 200
    except Exception:
        return False


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
