"""vol_reversal 전략 DRY-RUN 가상 매매 스크립트.

4시간마다 실행하여 vol_reversal 전략의 실전 신호를 추적.
실제 주문 없이 가상 포지션만 관리하여 검증 데이터 축적.

실행: python scripts/dryrun_vol_reversal.py
cron: 5 */4 * * * cd /home/ubuntu/BitCoin_Trade && .venv/bin/python scripts/dryrun_vol_reversal.py >> /var/log/vol_reversal_dryrun.log 2>&1
"""
import sys, io, asyncio, json
from pathlib import Path
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.market_data.fetcher import fetch_ohlcv
from services.strategies import get_strategy
from services.execution.scanner import get_krw_market_coins
from services.alerting.notifier import send

STATE_FILE = ROOT / "workspace" / "vol_reversal_dryrun_state.json"
MAX_POSITIONS = 5
FEE = 0.0005
INITIAL_CAPITAL = 245_000  # 가상 자본


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "positions": {},
        "closed_trades": [],
        "strategy_start": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        "last_updated": "",
    }


def save_state(state: dict):
    state["last_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


async def main():
    now = datetime.now(tz=timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n[{now_str}] vol_reversal DRY-RUN 스캔")

    state = load_state()
    positions = state.get("positions", {})
    strategy_fn = get_strategy("vol_reversal", vol_threshold=2.0)

    # 1. 보유 종목 청산 확인
    from datetime import timedelta
    end = now
    start = end - timedelta(days=30)  # 30일 (4h봉 180개, 충분)
    start_str = start.strftime("%Y-%m-%dT00:00:00Z")
    end_str = end.strftime("%Y-%m-%dT00:00:00Z")

    exits = []
    for symbol, pos in list(positions.items()):
        try:
            raw = await fetch_ohlcv(symbol, "4h", start_str, end_str, use_cache=False)
            df = __import__("pandas").DataFrame(raw)
            if len(df) < 25:
                continue

            sig = strategy_fn(df)
            latest_signal = int(sig.iloc[-1])
            latest_close = float(df["close"].iloc[-1])

            # 고점 갱신
            pos["highest"] = max(pos.get("highest", pos["entry_price"]), latest_close)

            if latest_signal == 0:
                ret_pct = (latest_close / pos["entry_price"] - 1) * 100
                exits.append((symbol, latest_close, ret_pct))
                state["closed_trades"].append({
                    "symbol": symbol,
                    "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"],
                    "exit_date": now.strftime("%Y-%m-%d %H:%M"),
                    "exit_price": latest_close,
                    "return_pct": round(ret_pct, 2),
                })
                # 자본 복귀
                invested = pos.get("amount_krw", INITIAL_CAPITAL / MAX_POSITIONS)
                state["capital"] += invested * (1 + ret_pct / 100)
                del positions[symbol]
                print(f"  [매도] {symbol} @ {latest_close:,.0f} ({ret_pct:+.1f}%)")
        except Exception as e:
            print(f"  {symbol} 청산 확인 오류: {e}")

    # 2. 신규 진입 스캔
    available_slots = MAX_POSITIONS - len(positions)
    entries = []

    if available_slots > 0:
        coins = get_krw_market_coins()[:50]  # 상위 50종목만 (스캔 시간 제한)
        print(f"  스캔: {len(coins)}개 종목")

        for coin in coins:
            symbol = coin["symbol"]
            if symbol in positions:
                continue
            try:
                raw = await fetch_ohlcv(symbol, "4h", start_str, end_str, use_cache=False)
                df = __import__("pandas").DataFrame(raw)
                if len(df) < 25:
                    continue

                sig = strategy_fn(df)
                if len(sig) < 2:
                    continue
                prev = int(sig.iloc[-2])
                curr = int(sig.iloc[-1])

                if prev == 0 and curr == 1:
                    latest_close = float(df["close"].iloc[-1])
                    slot_amount = state["capital"] * 0.95 / available_slots

                    if slot_amount < 5000:
                        continue

                    positions[symbol] = {
                        "entry_date": now.strftime("%Y-%m-%d %H:%M"),
                        "entry_price": latest_close,
                        "highest": latest_close,
                        "amount_krw": slot_amount,
                    }
                    state["capital"] -= slot_amount
                    entries.append((symbol, latest_close))
                    available_slots -= 1
                    print(f"  [매수] {symbol} @ {latest_close:,.0f} ({slot_amount:,.0f} KRW)")

                    if available_slots <= 0:
                        break

                await asyncio.sleep(0.15)
            except Exception:
                continue

    state["positions"] = positions
    save_state(state)

    # 3. 성적 집계
    closed = state.get("closed_trades", [])
    n_trades = len(closed)
    wins = [t for t in closed if t["return_pct"] > 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0
    avg_ret = sum(t["return_pct"] for t in closed) / n_trades if n_trades > 0 else 0

    # 평가 자산
    total_val = state["capital"]
    for sym, pos in positions.items():
        total_val += pos.get("amount_krw", 0)

    print(f"\n  보유: {len(positions)}/{MAX_POSITIONS}")
    print(f"  거래: {n_trades}건, 승률: {win_rate:.0f}%, 평균: {avg_ret:+.1f}%")
    print(f"  평가: {total_val:,.0f} KRW (초기 {INITIAL_CAPITAL:,.0f})")

    # 4. 텔레그램 보고 (거래 발생 시에만)
    if entries or exits:
        msg = f"🔬 *vol\\_reversal DRY-RUN* ({now.strftime('%H:%M')} UTC)\n"
        for sym, price in entries:
            msg += f"  📥 {sym} @ {price:,.0f}\n"
        for sym, price, ret in exits:
            emoji = "🟢" if ret > 0 else "🔴"
            msg += f"  {emoji} {sym} @ {price:,.0f} ({ret:+.1f}%)\n"
        msg += f"\n보유: {len(positions)}/{MAX_POSITIONS}"
        msg += f"\n성적: {n_trades}건, {win_rate:.0f}%, {avg_ret:+.1f}%"
        msg += f"\n평가: {total_val:,.0f} KRW"
        await send(msg)


if __name__ == "__main__":
    asyncio.run(main())
