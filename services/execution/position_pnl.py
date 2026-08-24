"""포지션 손익 산식 — 단일 진실 원천 (lessons #45).

## 왜 별도 모듈인가

매도 경로가 셋이다: `realtime_monitor._check_tp_levels`(부분 익절),
`realtime_monitor._execute_sell`(트레일링스탑), `multi_trader.run_daily_cycle`(일일 회전).
셋이 각자 `return_pct`를 계산하면 반드시 갈라진다(교훈 #19).
`multi_trader`는 `realtime_monitor`가 import하므로 역방향 import가 불가능 —
그래서 양쪽 모두가 의존할 수 있는 중립 모듈에 둔다.

## 왜 money-weighted인가

`return_pct`를 **마지막 체결가만으로** 계산하면 부분 익절로 확보한 이익이 통계에서
사라진다. 예: +5%에 절반을 실현한 뒤 +1%에 트레일링스탑 이탈 → `+1%`로 기록.
실제 성과는 `(0.5 × 5%) + (0.5 × 1%) = +3%`다.

ADR 20260823-1의 판정 지표(승률 48% / 평균 +0.75% / 표본 30건)가 이 값 위에 서 있으므로,
실현손익 KRW 합계 ÷ 진입금 으로 통일한다.
"""
from __future__ import annotations

from services.execution.config import FEE_RATE


def record_realized(pos: dict, exec_price: float, qty: float) -> float:
    """매도 1건의 순손익(편도 수수료 2회 반영)을 포지션에 누적하고 그 값을 반환한다.

    Args:
        pos:        state["positions"][symbol] — 제자리에서 갱신된다
        exec_price: 확정 평균 체결가 (lessons #43 — 신호가 아님)
        qty:        이번에 매도한 수량
    """
    entry = float(pos.get("entry_price") or 0)
    net = exec_price * qty * (1 - FEE_RATE) - entry * qty * (1 + FEE_RATE)
    pos["realized_pl_krw"] = float(pos.get("realized_pl_krw") or 0.0) + net
    pos["realized_qty"] = float(pos.get("realized_qty") or 0.0) + qty
    return net


def position_return_pct(pos: dict, fallback_price: float = 0.0) -> float:
    """포지션 전체 수익률(%) — 부분 익절 실현분까지 합산한 money-weighted 값.

    실현손익이 추적되지 않은 구버전 포지션은 `fallback_price` 기준 단순 수익률로
    폴백한다(수수료는 반영). 폴백 값도 없으면 0.0.
    """
    entry = float(pos.get("entry_price") or 0)
    realized = pos.get("realized_pl_krw")
    basis = float(pos.get("entry_amount_krw") or 0)
    if basis <= 0 and entry > 0:
        basis = entry * float(pos.get("entry_qty") or 0)
    if realized is not None and basis > 0:
        return float(realized) / basis * 100.0
    if entry > 0 and fallback_price > 0:
        return (fallback_price * (1 - FEE_RATE)) / (entry * (1 + FEE_RATE)) * 100.0 - 100.0
    return 0.0


def rebuild_realized_from_exchange(exchange, symbol: str, pos: dict) -> dict | None:
    """거래소 매도 체결 이력으로 포지션의 실현손익을 처음부터 재구성한다.

    ## 왜 필요한가

    사용자가 업비트 앱에서 **직접 매도**하면 그 대금은 봇의 `realized_pl_krw` 에
    잡히지 않는다. 그 상태로 포지션을 정리하면 `return_pct` 가 TP분만 반영하거나
    (부분 익절이 있었던 경우) 정리 시점 시장가로 계산되어 **실제와 무관한 값**이
    `closed_trades` 에 남는다. ADR 20260823-1 의 판정 지표가 그 위에 서 있으므로
    체결 이력을 진실 원천으로 삼아야 한다 (lessons #10 "state 는 거래소 미러").

    ## 업비트 특성 (lessons #43)

    - `fetch_closed_orders` 의 매도 항목은 `average`/`price`/`cost` 가 **전부 None**
      → `fetch_order(uuid)` 재조회로만 확정 체결가를 얻는다 (실측 확인)
    - 시장가 **매수**는 잔여 KRW 환불로 `canceled` 종료라 목록에 잡히지 않는다.
      따라서 이 함수는 **매도만** 대상으로 한다
    - 같은 종목을 재진입한 경우 이전 포지션의 매도가 섞이므로 `entry_date` 이후로 한정한다

    수수료는 `record_realized()` 와 동일한 `FEE_RATE` 산식을 쓴다. 거래소가 돌려주는
    실제 fee 와 대조했을 때 일치하므로(실측 STX 27,834.2원 동일), 산식을 하나로
    유지하는 편이 경로 간 불일치를 막는다 (교훈 #19).

    Args:
        exchange: ccxt 거래소 인스턴스
        symbol:   'POL/KRW' 형식
        pos:      state["positions"][symbol] — **변경하지 않는다**(호출자가 결정)

    Returns:
        {"realized_pl_krw", "realized_qty", "last_exec_price", "n_orders"}
        또는 조회 실패 시 None (호출자가 기존 값으로 폴백)
    """
    from datetime import datetime, timezone

    from services.execution.upbit_client import order_exec_price

    entry_date = pos.get("entry_date") or ""
    since_ms = None
    if entry_date:
        try:
            dt = datetime.strptime(entry_date, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            since_ms = int(dt.timestamp() * 1000)
        except ValueError:
            since_ms = None

    try:
        orders = exchange.fetch_closed_orders(symbol, limit=50)
    except Exception:
        return None

    probe = dict(pos)
    probe.pop("realized_pl_krw", None)
    probe.pop("realized_qty", None)
    last_exec = 0.0
    n = 0

    for o in sorted(orders, key=lambda x: x.get("timestamp") or 0):
        if (o.get("side") or "").lower() != "sell":
            continue
        ts = o.get("timestamp")
        # 재진입 종목: 이전 포지션의 매도를 배제 (실측 POL — 08-22 05:36/07:06 매도는
        # 10:09 진입한 현 포지션과 무관하다)
        if since_ms is not None and ts is not None and int(ts) < since_ms:
            continue
        try:
            settled = exchange.fetch_order(o["id"], symbol)
        except Exception:
            return None  # 일부만 반영하면 오히려 더 틀린다 — 전부 실패 처리
        qty = float(settled.get("filled") or 0)
        px = order_exec_price(settled)
        if qty <= 0 or not px:
            continue
        record_realized(probe, float(px), qty)
        last_exec = float(px)
        n += 1

    if n == 0:
        return None
    return {
        "realized_pl_krw": probe["realized_pl_krw"],
        "realized_qty": probe["realized_qty"],
        "last_exec_price": last_exec,
        "n_orders": n,
    }
