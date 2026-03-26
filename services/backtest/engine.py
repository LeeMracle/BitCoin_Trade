"""백테스트 엔진 — bar-close 실행 가정, long/flat only (업비트 현물).

설계 가정:
  - 신호는 bar 종가 기준으로 생성되고, 다음 bar 시가에 체결 (look-ahead bias 방지)
  - 수수료: fee_rate (왕복) — 진입/청산 각각 적용
  - 슬리피지: slippage_bps (basis points) — 체결가에 불리하게 적용
  - 포지션: long/flat only (숏 없음)
  - 결측 데이터 처리: NaN 신호는 보유 유지 (hold)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import uuid

import numpy as np
import pandas as pd

from .metrics import compute_metrics
from .models import Metrics, RunResult
from .report import save_artifacts

# 기본 파라미터
DEFAULT_PARAMS = {
    "initial_capital": 10_000_000,  # 1천만원 KRW
    "fee_rate": 0.0005,             # 0.05% (업비트 기본 수수료)
    "slippage_bps": 5,              # 5bp
}


class BacktestEngine:
    def run(
        self,
        strategy_fn: Callable[[pd.DataFrame], pd.Series],
        ohlcv: pd.DataFrame,
        params: dict | None = None,
    ) -> RunResult:
        """
        strategy_fn: ohlcv DataFrame을 받아 signal Series를 반환하는 함수.
                     signal 값: 1 (매수/보유), 0 (청산/대기)
        ohlcv: columns [ts, open, high, low, close, volume], ts는 Unix ms
        """
        cfg = {**DEFAULT_PARAMS, **(params or {})}
        run_id = _make_run_id()

        df = ohlcv.copy().reset_index(drop=True)
        df["ts"] = df["ts"].astype(np.int64)

        # 전략 신호 생성
        signal: pd.Series = strategy_fn(df).reindex(df.index).ffill().fillna(0)

        equity_rows = []
        trade_rows = []

        capital = float(cfg["initial_capital"])
        fee_rate = float(cfg["fee_rate"])
        slippage_bps = float(cfg["slippage_bps"])
        position = 0.0       # BTC 보유량
        entry_price = 0.0
        entry_ts = 0

        for i in range(len(df) - 1):
            sig = int(signal.iloc[i])
            # 다음 bar 시가로 체결
            exec_price = df["open"].iloc[i + 1]
            exec_price_buy = exec_price * (1 + slippage_bps / 10_000)
            exec_price_sell = exec_price * (1 - slippage_bps / 10_000)

            if sig == 1 and position == 0:
                # 진입
                cost = capital * (1 - fee_rate)
                position = cost / exec_price_buy
                entry_price = exec_price_buy
                entry_ts = int(df["ts"].iloc[i + 1])
                capital = 0.0

            elif sig == 0 and position > 0:
                # 청산
                proceeds = position * exec_price_sell * (1 - fee_rate)
                ret_pct = (exec_price_sell / entry_price) * (1 - fee_rate) ** 2 - 1
                trade_rows.append({
                    "entry_ts": entry_ts,
                    "exit_ts": int(df["ts"].iloc[i + 1]),
                    "side": "long",
                    "entry_price": round(entry_price, 0),
                    "exit_price": round(exec_price_sell, 0),
                    "return_pct": round(ret_pct, 6),
                })
                capital = proceeds
                position = 0.0

            # 현재 자산 평가
            current_close = df["close"].iloc[i + 1]
            equity = capital + position * current_close
            equity_rows.append({"ts": int(df["ts"].iloc[i + 1]), "equity": round(equity, 0)})

        # 마지막 포지션 청산 (열린 포지션 처리)
        if position > 0:
            last_close = df["close"].iloc[-1]
            proceeds = position * last_close * (1 - fee_rate)
            ret_pct = (last_close / entry_price) * (1 - fee_rate) ** 2 - 1
            trade_rows.append({
                "entry_ts": entry_ts,
                "exit_ts": int(df["ts"].iloc[-1]),
                "side": "long",
                "entry_price": round(entry_price, 0),
                "exit_price": round(last_close, 0),
                "return_pct": round(ret_pct, 6),
            })
            equity_rows[-1] = {"ts": int(df["ts"].iloc[-1]), "equity": round(proceeds, 0)}

        equity_df = pd.DataFrame(equity_rows)
        trade_df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame(
            columns=["entry_ts", "exit_ts", "side", "entry_price", "exit_price", "return_pct"]
        )

        metrics = compute_metrics(equity_df, trade_df)
        artifact_dir = save_artifacts(run_id, cfg, metrics, equity_df, trade_df)

        return RunResult(
            run_id=run_id,
            config=cfg,
            metrics=metrics,
            equity_curve=equity_df,
            trade_log=trade_df,
            artifact_dir=artifact_dir,
        )


def _make_run_id() -> str:
    from datetime import datetime, timezone
    date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{date_str}_{short_uuid}"
