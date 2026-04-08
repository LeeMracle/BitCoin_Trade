#!/usr/bin/env python3
"""자비스 실행기 — 범용 분할매매 전략 자동 실행.

사용자가 자비스(Claude)와 논의하여 결정한 매매 전략을
서버 cron으로 자동 실행한다.

설정: workspace/jarvis_strategies.json
상태: workspace/jarvis_state.json
로그: workspace/jarvis_log.jsonl

실행:
  python scripts/jarvis_executor.py              # 전체 전략 체크
  python scripts/jarvis_executor.py --dry-run    # 주문 없이 조건만 확인
  python scripts/jarvis_executor.py --status     # 현재 전략 상태 출력

cron 예시 (매시 정각):
  0 * * * * cd ~/BitCoin_Trade && .venv/bin/python scripts/jarvis_executor.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "services" / ".env")

import ccxt
import numpy as np

# 공용 None-safe 헬퍼 (docs/lint_layer.md 참조)
from services.common.ccxt_utils import fmt_num as _fmt_num
from services.common.ccxt_utils import resolve_fill as _resolve_fill

KST = timezone(timedelta(hours=9))
STRATEGIES_FILE = ROOT / "workspace" / "jarvis_strategies.json"
STATE_FILE = ROOT / "workspace" / "jarvis_state.json"
LOG_FILE = ROOT / "workspace" / "jarvis_log.jsonl"
MIN_ORDER_KRW = 5_000
MAX_SYMBOLS = 5  # 동시 관리 종목 상한


# ═══════════════════════════════════════════════════════════════
# 유틸리티
# ═══════════════════════════════════════════════════════════════

def _now_kst() -> datetime:
    return datetime.now(tz=KST)


def _log(msg: str):
    print(f"[{_now_kst():%H:%M:%S}] {msg}", flush=True)


def _append_log(entry: dict):
    entry["logged_at"] = _now_kst().isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except Exception:
        return False


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# 시장 데이터
# ═══════════════════════════════════════════════════════════════

def _create_exchange() -> ccxt.upbit:
    return ccxt.upbit({
        "apiKey": os.environ.get("UPBIT_ACCESS_KEY", ""),
        "secret": os.environ.get("UPBIT_SECRET_KEY", ""),
        "enableRateLimit": True,
    })


def fetch_indicators(exchange: ccxt.upbit, symbol: str) -> dict:
    """종목의 기술적 지표 일괄 계산."""
    ticker = exchange.fetch_ticker(symbol)
    price = ticker["last"]

    candles = exchange.fetch_ohlcv(symbol, "1d", limit=60)
    if len(candles) < 20:
        return {"price": price, "error": "데이터 부족"}

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]

    # SMA
    sma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
    sma50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else None

    # Donchian Channel 20
    dc20_high = float(max(highs[-20:])) if len(highs) >= 20 else None
    dc20_low = float(min(lows[-20:])) if len(lows) >= 20 else None

    # ATR14
    trs = []
    for i in range(1, len(candles)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr14 = float(np.mean(trs[-14:])) if len(trs) >= 14 else None

    # RSI14
    rsi = None
    if len(closes) >= 15:
        deltas = np.diff(closes[-15:])
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.001
        rs = avg_gain / avg_loss
        rsi = float(100 - 100 / (1 + rs))

    # F&G
    fg = None
    try:
        resp = urllib.request.urlopen(
            "https://api.alternative.me/fng/?limit=1", timeout=5
        ).read()
        fg = float(json.loads(resp)["data"][0]["value"])
    except Exception:
        pass

    return {
        "price": price,
        "sma20": sma20,
        "sma50": sma50,
        "dc20_high": dc20_high,
        "dc20_low": dc20_low,
        "atr14": atr14,
        "rsi14": rsi,
        "fg": fg,
    }


def get_holding(exchange: ccxt.upbit, symbol: str) -> float:
    """특정 종목 보유 수량 조회."""
    currency = symbol.split("/")[0]
    bal = exchange.fetch_balance()
    return float(bal.get(currency, {}).get("free", 0))


# ═══════════════════════════════════════════════════════════════
# 조건 평가
# ═══════════════════════════════════════════════════════════════

CONDITION_OPS = {
    "price >": lambda ind, val: ind["price"] > val,
    "price <": lambda ind, val: ind["price"] < val,
    "price > sma20": lambda ind, _: ind["sma20"] and ind["price"] > ind["sma20"],
    "price < sma20": lambda ind, _: ind["sma20"] and ind["price"] < ind["sma20"],
    "price > sma50": lambda ind, _: ind["sma50"] and ind["price"] > ind["sma50"],
    "price < sma50": lambda ind, _: ind["sma50"] and ind["price"] < ind["sma50"],
    "price > dc20_high": lambda ind, _: ind["dc20_high"] and ind["price"] > ind["dc20_high"],
    "price < dc20_low": lambda ind, _: ind["dc20_low"] and ind["price"] < ind["dc20_low"],
    "fg >": lambda ind, val: ind["fg"] is not None and ind["fg"] > val,
    "fg <": lambda ind, val: ind["fg"] is not None and ind["fg"] < val,
    "rsi >": lambda ind, val: ind["rsi14"] is not None and ind["rsi14"] > val,
    "rsi <": lambda ind, val: ind["rsi14"] is not None and ind["rsi14"] < val,
}


def evaluate_condition(condition: str, indicators: dict) -> bool:
    """조건 문자열을 평가한다.

    예: "price > sma20", "fg > 50", "price < 100000000"
    """
    condition = condition.strip()

    # 완전 일치 키 먼저 시도 (예: "price > sma20")
    if condition in CONDITION_OPS:
        try:
            return CONDITION_OPS[condition](indicators, 0)
        except (TypeError, ValueError):
            return False

    # 접두사 매칭 (예: "fg > 50", "price > 100000000")
    for key, fn in CONDITION_OPS.items():
        if condition.startswith(key):
            remainder = condition[len(key):].strip()
            if not remainder:
                continue
            try:
                val = float(remainder)
                return fn(indicators, val)
            except (ValueError, TypeError):
                continue

    _log(f"  [경고] 알 수 없는 조건: {condition}")
    return False


# ═══════════════════════════════════════════════════════════════
# 주문 실행
# ═══════════════════════════════════════════════════════════════

def execute_sell(exchange: ccxt.upbit, symbol: str,
                 ratio: float, dry_run: bool) -> dict:
    """분할 매도 실행."""
    holding = get_holding(exchange, symbol)
    sell_amount = holding * ratio

    # 평가금액 체크
    ticker = exchange.fetch_ticker(symbol)
    sell_value = sell_amount * ticker["last"]

    if sell_value < MIN_ORDER_KRW:
        return {"status": "skip", "reason": f"주문금액 {sell_value:,.0f}원 < 최소 {MIN_ORDER_KRW:,}원"}

    if dry_run:
        return {
            "status": "dry_run",
            "amount": sell_amount,
            "est_value": sell_value,
            "price": ticker["last"],
        }

    # 실제 매도
    order = exchange.create_market_sell_order(symbol, sell_amount)
    filled_amount = order.get("amount") or order.get("filled") or sell_amount
    cost, price = _resolve_fill(exchange, order, symbol, amount_hint=filled_amount)
    return {
        "status": "executed",
        "order_id": order.get("id"),
        "amount": filled_amount,
        "cost": cost,
        "price": price,
    }


def execute_buy(exchange: ccxt.upbit, symbol: str,
                amount_krw: float, dry_run: bool) -> dict:
    """매수 실행."""
    if amount_krw < MIN_ORDER_KRW:
        return {"status": "skip", "reason": f"주문금액 {amount_krw:,.0f}원 < 최소 {MIN_ORDER_KRW:,}원"}

    ticker = exchange.fetch_ticker(symbol)

    if dry_run:
        return {
            "status": "dry_run",
            "est_amount": amount_krw / ticker["last"],
            "est_value": amount_krw,
            "price": ticker["last"],
        }

    order = exchange.create_market_buy_order(symbol, None, params={"cost": amount_krw})
    cost, price = _resolve_fill(exchange, order, symbol)
    filled_amount = order.get("amount") or order.get("filled")
    if filled_amount is None and price:
        filled_amount = amount_krw / price
    return {
        "status": "executed",
        "order_id": order.get("id"),
        "amount": filled_amount,
        "cost": cost if cost is not None else amount_krw,
        "price": price,
    }


# ═══════════════════════════════════════════════════════════════
# 메인 엔진
# ═══════════════════════════════════════════════════════════════

def process_strategy(symbol: str, strategy: dict,
                     state: dict, exchange: ccxt.upbit,
                     force_dry_run: bool) -> list[str]:
    """단일 종목 전략 처리. 실행 결과 메시지 리스트 반환."""
    msgs = []
    side = strategy.get("side", "sell")
    steps = strategy.get("steps", [])
    dry_run = force_dry_run or strategy.get("dry_run", True)
    mode = "DRY" if dry_run else "LIVE"

    # BATA 보유종목 교차검증
    bata_state_file = ROOT / "workspace" / "multi_trading_state.json"
    if bata_state_file.exists():
        bata = json.loads(bata_state_file.read_text(encoding="utf-8"))
        bata_positions = bata.get("positions", {})
        if symbol in bata_positions and side == "sell":
            msg = f"[{symbol}] BATA 추적 종목 — 자비스 매도 스킵 (충돌 방지)"
            _log(msg)
            msgs.append(msg)
            return msgs

    # 지표 조회
    _log(f"[{symbol}] 지표 조회 중...")
    indicators = fetch_indicators(exchange, symbol)
    if "error" in indicators:
        msg = f"[{symbol}] 지표 조회 실패: {indicators['error']}"
        _log(msg)
        msgs.append(msg)
        return msgs

    price = indicators["price"]
    _log(f"  현재가: {price:,.0f} | SMA20: {indicators.get('sma20', 'N/A')}"
         f" | DC20: {indicators.get('dc20_low', '?')}~{indicators.get('dc20_high', '?')}"
         f" | F&G: {indicators.get('fg', '?')}")

    # 종목별 상태
    sym_state = state.get(symbol, {})

    # 손절 우선 체크 (steps에서 priority="stop_loss" 또는 id에 "sl" 포함)
    stop_steps = [s for s in steps if "sl" in s.get("id", "").lower()
                  or s.get("priority") == "stop_loss"]
    normal_steps = [s for s in steps if s not in stop_steps]

    # 손절 → 일반 순서로 처리
    ordered_steps = stop_steps + normal_steps

    for step in ordered_steps:
        step_id = step["id"]

        # 이미 실행된 단계 스킵
        if sym_state.get(step_id, {}).get("done"):
            continue

        condition = step["condition"]
        ratio = step.get("ratio", 1.0)

        met = evaluate_condition(condition, indicators)

        if not met:
            _log(f"  [{step_id}] 조건 미충족: {condition}")
            continue

        # 조건 충족!
        _log(f"  [{step_id}] 조건 충족: {condition}")

        if side == "sell":
            # 손절은 전량 — ratio 강제 1.0
            if step in stop_steps:
                ratio = 1.0
            result = execute_sell(exchange, symbol, ratio, dry_run)
        else:
            buy_amount = step.get("amount_krw", 0)
            result = execute_buy(exchange, symbol, buy_amount, dry_run)

        # 상태 저장
        sym_state[step_id] = {
            "done": True,
            "executed_at": _now_kst().isoformat(),
            "result": result,
        }
        state[symbol] = sym_state

        # 메시지 생성
        if result["status"] == "dry_run":
            msg = (f"[자비스-{mode}] {symbol} {side.upper()} 조건 충족\n"
                   f"  단계: {step_id} | 조건: {condition}\n"
                   f"  예상: {_fmt_num(result.get('est_value') or result.get('amount'))}원 "
                   f"@ {_fmt_num(result.get('price'))}")
        elif result["status"] == "executed":
            msg = (f"[자비스-LIVE] {symbol} {side.upper()} 실행!\n"
                   f"  단계: {step_id} | 조건: {condition}\n"
                   f"  체결: {_fmt_num(result.get('cost'))}원 @ {_fmt_num(result.get('price'))}"
                   f" (수량 {_fmt_num(result.get('amount'), ',.8f')})")
        else:
            msg = f"[자비스] {symbol} {step_id}: {result.get('reason', 'skip')}"

        _log(msg)
        msgs.append(msg)

        _append_log({
            "symbol": symbol, "side": side, "step_id": step_id,
            "condition": condition, "result": result, "mode": mode,
        })

        # 손절 실행 시 나머지 단계 모두 done 처리
        if step in stop_steps and result["status"] in ("executed", "dry_run"):
            for s in normal_steps:
                sym_state[s["id"]] = {
                    "done": True,
                    "executed_at": _now_kst().isoformat(),
                    "result": {"status": "cancelled", "reason": "손절로 인한 취소"},
                }
            break

    return msgs


def run(force_dry_run: bool = False):
    """전체 전략 실행."""
    strategies = _load_json(STRATEGIES_FILE)
    state = _load_json(STATE_FILE)

    if not strategies:
        _log("등록된 전략 없음. workspace/jarvis_strategies.json 확인.")
        return

    exchange = _create_exchange()
    all_msgs = []

    for symbol, strategy in strategies.items():
        if not strategy.get("active", False):
            _log(f"[{symbol}] 비활성 — 스킵")
            continue

        try:
            msgs = process_strategy(symbol, strategy, state, exchange, force_dry_run)
            all_msgs.extend(msgs)
        except Exception as e:
            err_msg = f"[자비스] {symbol} 처리 오류: {e}"
            _log(err_msg)
            all_msgs.append(err_msg)
            _append_log({"symbol": symbol, "error": str(e)})

        time.sleep(0.5)  # API rate limit

    # 상태 저장
    _save_json(STATE_FILE, state)

    # 텔레그램 보고 (변동 있을 때만)
    if all_msgs:
        report = f"[자비스 실행기] {_now_kst():%H:%M} KST\n\n" + "\n\n".join(all_msgs)
        _send_telegram(report)
        _log("텔레그램 보고 발송")
    else:
        _log("변동 없음 — 보고 생략")


def show_status():
    """현재 전략 및 상태 출력."""
    strategies = _load_json(STRATEGIES_FILE)
    state = _load_json(STATE_FILE)

    if not strategies:
        print("등록된 전략 없음")
        return

    for symbol, strategy in strategies.items():
        active = "활성" if strategy.get("active") else "비활성"
        dry = "DRY-RUN" if strategy.get("dry_run", True) else "LIVE"
        side = strategy.get("side", "?").upper()
        print(f"\n{'='*50}")
        print(f"{symbol} [{side}] — {active} ({dry})")
        print(f"{'='*50}")

        sym_state = state.get(symbol, {})
        for step in strategy.get("steps", []):
            step_id = step["id"]
            done = sym_state.get(step_id, {}).get("done", False)
            status = "완료" if done else "대기"
            print(f"  [{status}] {step_id}: {step['condition']} (비중: {(step.get('ratio') or 1.0):.0%})")
            if done:
                result = sym_state[step_id].get("result", {})
                print(f"         실행: {result.get('status')} @ {sym_state[step_id].get('executed_at', '')}")


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--status" in args:
        show_status()
    elif "--dry-run" in args:
        _log("=== 자비스 실행기 (DRY-RUN) ===")
        run(force_dry_run=True)
    else:
        _log("=== 자비스 실행기 ===")
        run()
