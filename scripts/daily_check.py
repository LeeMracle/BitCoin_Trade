"""일일 페이퍼 트레이딩 체크 스크립트.

매일 09:00 KST (업비트 일봉 마감 후) 실행 권장.

실행 방법:
  cd BitCoin_Trade
  .venv/Scripts/python scripts/daily_check.py

Windows 작업 스케줄러 등록 예시:
  schtasks /create /tn "BTC_PaperTrading" /tr "D:\\...\\BitCoin_Trade\\.venv\\Scripts\\python.exe D:\\...\\BitCoin_Trade\\scripts\\daily_check.py" /sc daily /st 09:05
"""
import sys, io, asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.paper_trading.runner import run_daily, fetch_recent_ohlcv
from services.paper_trading import strategy_rsi_ema


async def main():
    print("=" * 60)
    print("일일 페이퍼 트레이딩 체크")
    print("=" * 60)

    # 메인 전략: Donchian(50) + ATR(14)x3.0
    print("\n[메인 전략] Donchian(50) + ATR(14)x3.0")
    print("-" * 40)
    await run_daily()

    # 보조 전략: RSI(10) + EMA(150) — 관찰만
    print("\n[보조 전략] RSI(10)>50/<45 + EMA(150) — 관찰용")
    print("-" * 40)
    try:
        df = await fetch_recent_ohlcv(days=200)
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


if __name__ == "__main__":
    asyncio.run(main())
