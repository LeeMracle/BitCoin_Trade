"""틱 지연 계측 — 봇이 판단에 쓰는 가격이 얼마나 낡았는지 잰다.

## 왜 (2026-08-27 KERNEL)

    09:50 UTC  KERNEL 밴드(57.2) 돌파, 분봉 O 56 H 57.8 C 57
    09:55 UTC  봇이 "가격 57" 로 판단해 매수 -> 체결 67.8 (+18.9%)

09:55 분봉(O 65.4 H 67.8 L 65)에는 **57원 거래가 없다.** 체결가는 6/6 모두 당시
분봉 안이었으므로 주문 집행은 정상이고, 어긋난 것은 **입력(신호가)** 이다.
즉 호가창 슬리피지가 아니라 봇이 과거를 보고 있었다.

## 무엇을 재는가 — 지표 하나로는 범인을 못 가린다

    price_lag_ms = now - trade_timestamp   전송 지연 + **거래 희소성**
    msg_lag_ms   = now - timestamp         전송 지연만 (업비트가 메시지를 만든 시각)
    handler_ms   = _handle_tick 1회 소요   우리가 얼마나 오래 붙잡았나

price_lag 와 msg_lag 의 **차이가 곧 거래 희소성**이다. 이 구분이 없으면 진단이 틀린다:
저유동성 알트가 20분간 거래가 없으면 `trade_timestamp` 는 20분 전이 맞고 그건 지연이
아니다. 2026-08-27 첫 관측이 정확히 그 함정이었다 — price_lag p99 가 3.5~23분인데
handler 는 p99 0.2ms. 희소성을 지연으로 읽을 뻔했다.

`price_lag` 만 보면 "네트워크가 느렸다"와 "우리 루프가 밀렸다"를 구분할 수 없다.
가설(async 안의 동기 `get_balance()` + `_retry_on_429` 1s/4s/16s 백오프가 루프를
멈춰 메시지가 백로그로 쌓인다)이 맞다면 **느린 handler 직후 price_lag 가 치솟는
톱니**가 나와야 한다. 두 계열이 시간축에서 맞물리는지가 판정 기준이다.

구독이 `isOnlyRealtime: True` 라 스냅샷이 오지 않는다 — 큰 지연은 아티팩트가 아니다.

## 시계 오차

`now` 는 우리 서버 시계, `timestamp` 는 업비트 시계다. 서버 시계가 앞서면 지연이
음수로 나온다. `min` 을 함께 보고하는 이유이고, 음수가 크면 **측정값 전체가 그만큼
치우친 것**이므로 NTP 부터 확인해야 한다.

## 이 모듈은 계측만 한다

지연을 이유로 신호를 버리는 동작은 전략 변경이라 별도 ADR 이 필요하다
(CLAUDE.md 작업 규칙). 여기서는 기록·요약만 제공한다.
"""

from __future__ import annotations

import time as _time
from collections import deque

from services.execution.config import (
    TICK_LAG_REPORT_INTERVAL_SEC,
    TICK_LAG_SLOW_HANDLER_MS,
    TICK_LAG_WINDOW,
    WS_STALE_LAG_MS,
    WS_STALE_MIN_SAMPLES,
    WS_STALE_RECONNECT_COOLDOWN_SEC,
)


def _pct(sorted_vals: list[float], q: float) -> float:
    """분위수 (선형보간 없음 — 표본이 수천이라 인덱스 근사로 충분)."""
    if not sorted_vals:
        return 0.0
    i = int(len(sorted_vals) * q)
    return sorted_vals[min(i, len(sorted_vals) - 1)]


class TickLagTracker:
    """틱 지연 롤링 집계. 이벤트 루프 안에서 호출되므로 O(1) 경로만 쓴다.

    분위수는 요약 시점(기본 15분)에만 정렬한다 — 틱마다 정렬하면
    종목수 x 틱빈도 곱으로 폭발한다(교훈 #14 계열).
    """

    def __init__(self, window: int = TICK_LAG_WINDOW):
        self._price_lag: deque[float] = deque(maxlen=window)
        # 전송 지연만. price_lag 와 나눠 봐야 "낡은 가격"의 원인이 갈린다.
        self._msg_lag: deque[float] = deque(maxlen=window)
        # 신선도 판정용 **단기** 창. 통계용(_msg_lag, 5000)과 분리한다 —
        # 5000개는 약 4분치라 열화를 늦게 알아채고, 회복도 늦게 반영된다.
        self._recent_msg: deque[float] = deque(maxlen=200)
        # 강제 재연결 직후 재판정 금지 (재연결 루프 방지)
        self._reconnect_block_until = 0.0
        self._handler: deque[float] = deque(maxlen=window)
        # 종목별 최근 지연 — 매수 판단 시점의 신선도를 기록하기 위한 것.
        self._latest: dict[str, dict] = {}
        # 느린 handler 직후 지연이 치솟는지 보기 위한 (mono, price_lag) 쌍
        self._slow_events: deque[tuple[float, float, str]] = deque(maxlen=200)
        self._last_report_mono = _time.monotonic()
        self._n = 0

    # ── 기록 ────────────────────────────────────────────────
    def record_tick(self, symbol: str, data: dict, now_ms: float | None = None) -> float | None:
        """틱 1건의 지연을 기록하고 price_lag_ms 를 반환. 실패 시 None.

        업비트 ticker 필드: `timestamp`(메시지 생성), `trade_timestamp`(체결 시각).
        둘 다 epoch ms. 없으면 조용히 통과한다 — 계측 실패가 매매를 막으면 안 된다.
        """
        now_ms = _time.time() * 1000 if now_ms is None else now_ms
        trade_ts = data.get("trade_timestamp") or data.get("timestamp")
        if not trade_ts:
            return None
        try:
            price_lag = now_ms - float(trade_ts)
        except (TypeError, ValueError):
            return None
        msg_ts = data.get("timestamp")
        msg_lag = (now_ms - float(msg_ts)) if msg_ts else None
        if msg_lag is not None:
            self._msg_lag.append(msg_lag)
            self._recent_msg.append(msg_lag)
        self._price_lag.append(price_lag)
        self._n += 1
        self._latest[symbol] = {
            "price_lag_ms": price_lag,
            "msg_lag_ms": msg_lag,
            "mono": _time.monotonic(),
        }
        return price_lag

    def record_handler(self, elapsed_ms: float, symbol: str = "") -> None:
        """_handle_tick 1회 소요를 기록. 임계 초과분은 별도 보관."""
        self._handler.append(elapsed_ms)
        if elapsed_ms >= TICK_LAG_SLOW_HANDLER_MS:
            self._slow_events.append((_time.monotonic(), elapsed_ms, symbol))

    # ── 신선도 판정 ─────────────────────────────────────────
    def is_degraded(self) -> tuple[bool, float]:
        """연결이 "썩었는지" 판정. (판정, 최근 지연 중앙값 ms).

        ## 왜 중앙값인가

        단발 지각 한 건으로 재연결하면 안 된다. 반대로 평균은 큰 outlier 하나에
        끌려간다. 중앙값은 "표본의 절반이 이만큼 늦다"를 뜻하므로 **지속적 열화**에만
        반응한다 — 실측 열화 구간이 정확히 그 모습이었다(p50 자체가 164~732초).

        ## 왜 별도 감시가 필요한가

        기존 `wait_for(ws.receive(), timeout=300)` 은 **침묵**만 잡는다. receive() 가
        반환되기만 하면 타이머가 리셋되므로, 12분 늦은 메시지가 계속 오는 상태는
        영원히 통과한다. liveness 감시로 freshness 를 지킬 수 없다.
        """
        if len(self._recent_msg) < WS_STALE_MIN_SAMPLES:
            return False, 0.0
        if _time.monotonic() < self._reconnect_block_until:
            return False, 0.0
        v = sorted(self._recent_msg)
        med = v[len(v) // 2]
        return med > WS_STALE_LAG_MS, med

    def note_reconnect(self) -> None:
        """강제 재연결 시 호출. 낡은 표본을 버리고 쿨다운을 건다.

        버리지 않으면 재연결 직후에도 옛 지연값이 중앙값을 지배해 **즉시 다시**
        판정이 서고 재연결 루프에 빠진다.
        """
        self._recent_msg.clear()
        self._reconnect_block_until = _time.monotonic() + WS_STALE_RECONNECT_COOLDOWN_SEC

    def latest(self, symbol: str) -> dict | None:
        """해당 종목의 마지막 틱 지연. 매수 로그에 실어 사후 대조용으로 쓴다."""
        return self._latest.get(symbol)

    # ── 요약 ────────────────────────────────────────────────
    def due(self) -> bool:
        return (_time.monotonic() - self._last_report_mono) >= TICK_LAG_REPORT_INTERVAL_SEC

    def summary(self, reset_timer: bool = True) -> dict:
        if reset_timer:
            self._last_report_mono = _time.monotonic()
        lags = sorted(self._price_lag)
        msgs = sorted(self._msg_lag)
        hs = sorted(self._handler)
        return {
            "m_p50": _pct(msgs, 0.50), "m_p90": _pct(msgs, 0.90),
            "m_p99": _pct(msgs, 0.99),
            "m_max": msgs[-1] if msgs else 0.0, "m_n": len(msgs),
            "n": len(lags), "total": self._n,
            "p50": _pct(lags, 0.50), "p90": _pct(lags, 0.90),
            "p99": _pct(lags, 0.99),
            "max": lags[-1] if lags else 0.0,
            # 시계 오차 지표 — 크게 음수면 서버 시계가 앞선 것이다
            "min": lags[0] if lags else 0.0,
            "h_p50": _pct(hs, 0.50), "h_p99": _pct(hs, 0.99),
            "h_max": hs[-1] if hs else 0.0,
            "slow_n": len(self._slow_events),
        }

    def format_summary(self) -> str:
        s = self.summary()
        if s["n"] == 0:
            return "  [틱지연] 표본 없음"
        skew = "  ⚠ 시계 오차 의심(NTP 확인)" if s["min"] < -1000 else ""
        recent = ""
        if self._slow_events:
            t, ms, sym = self._slow_events[-1]
            ago = _time.monotonic() - t
            recent = f" | 최근 느린 handler {ms:,.0f}ms ({sym or '?'}, {ago:,.0f}s 전)"
        return (
            f"  [틱지연] n={s['n']:,} 누적={s['total']:,} | "
            f"가격지연 p50 {s['p50']:,.0f} / p90 {s['p90']:,.0f} / "
            f"p99 {s['p99']:,.0f} / 최대 {s['max']:,.0f}ms (최소 {s['min']:,.0f})"
            f"{skew}\n"
            f"           전송지연 p50 {s['m_p50']:,.0f} / p90 {s['m_p90']:,.0f} / "
            f"p99 {s['m_p99']:,.0f} / 최대 {s['m_max']:,.0f}ms  "
            f"(가격지연과의 차이 = 거래 희소성)\n"
            f"           handler p50 {s['h_p50']:,.1f} / p99 {s['h_p99']:,.1f} / "
            f"최대 {s['h_max']:,.0f}ms | {TICK_LAG_SLOW_HANDLER_MS:,}ms↑ {s['slow_n']}건(누적){recent}"
        )
