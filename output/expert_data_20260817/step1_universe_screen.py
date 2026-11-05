"""Step 1 — 유니버스 사전 스크리닝 (v3, 실용적).

전략:
1. upbit /v1/market/all + /v1/ticker 로 KRW 마켓 + 현재 24h 거래대금 획득
2. 현재 24h 거래대금 >= 5억 만 후속 fetch 대상으로 선정
3. 상장일·기간별 유동성은 step2에서 실제 데이터 fetch 후 재판정 (백테스트 루프의 시점별 재계산이 진짜 기준)

목적: fetch 대상 축소 (283 -> ~60 예상). 백테스트 정합성은 시점별 재계산이 담당.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

import aiohttp

OUT_DIR = Path(__file__).resolve().parent
MIN_VOLUME_KRW_CURRENT = 5e8  # 사전 컷 기준: 오늘 24h >= 5억


async def fetch_ticker_bulk(session: aiohttp.ClientSession) -> tuple[list[str], dict[str, float]]:
    async with session.get("https://api.upbit.com/v1/market/all",
                            params={"isDetails": "false"}) as r:
        markets = await r.json()
    krw_syms_upbit = [m["market"] for m in markets if m["market"].startswith("KRW-")]

    result = {}
    for i in range(0, len(krw_syms_upbit), 100):
        chunk = krw_syms_upbit[i:i + 100]
        async with session.get("https://api.upbit.com/v1/ticker",
                                params={"markets": ",".join(chunk)}) as r:
            data = await r.json()
        for row in data:
            m = row["market"]
            sym = m.replace("KRW-", "") + "/KRW"
            result[sym] = float(row.get("acc_trade_price_24h", 0.0))
        await asyncio.sleep(0.1)
    return sorted(result.keys()), result


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        syms, ticker = await fetch_ticker_bulk(session)
    print(f"[step1] KRW markets: {len(syms)}")

    passed = []
    all_rows = []
    for sym in syms:
        v = ticker[sym]
        row = {"symbol": sym, "current_24h_krw": v,
               "screen_pass": v >= MIN_VOLUME_KRW_CURRENT}
        all_rows.append(row)
        if row["screen_pass"]:
            passed.append(row)

    passed.sort(key=lambda r: r["current_24h_krw"], reverse=True)

    out = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_markets": len(syms),
        "screen_passed": len(passed),
        "min_volume_krw_current": MIN_VOLUME_KRW_CURRENT,
        "candidates": passed,
        "all_results": all_rows,
    }
    (OUT_DIR / "universe_screen.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[step1] passed (>= 5억): {len(passed)}/{len(syms)}")
    print("\ntop 50 by current 24h volume:")
    for r in passed[:50]:
        print(f"  {r['symbol']:14s} vol_24h {r['current_24h_krw']/1e8:8.2f}억")


if __name__ == "__main__":
    asyncio.run(main())
