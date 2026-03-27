"""페이퍼 트레이딩 상태 관리 — JSON 파일 기반 영속화."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "paper_trading_state.json"


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    return_pct: float = 0.0
    status: str = "open"  # open / closed


@dataclass
class PaperState:
    """페이퍼 트레이딩 전체 상태."""
    initial_capital: float = 10_000_000.0
    capital: float = 10_000_000.0       # 현금 잔고
    position_btc: float = 0.0           # BTC 보유량
    entry_price: float = 0.0            # 진입 가격
    highest_since_entry: float = 0.0    # 진입 후 최고가
    trailing_stop: float = 0.0          # 현재 트레일링스탑
    is_holding: bool = False            # 포지션 보유 여부
    trades: list[dict] = field(default_factory=list)
    last_updated: str = ""
    last_close: float = 0.0

    @property
    def equity(self) -> float:
        return self.capital + self.position_btc * self.last_close

    @property
    def total_return(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return self.equity / self.initial_capital - 1

    @property
    def n_trades(self) -> int:
        return len([t for t in self.trades if t.get("status") == "closed"])

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.get("status") == "closed"]
        if not closed:
            return 0.0
        wins = [t for t in closed if t.get("return_pct", 0) > 0]
        return len(wins) / len(closed)

    def open_position(self, price: float, date: str, fee_rate: float = 0.0005):
        """매수 진입."""
        cost = self.capital * (1 - fee_rate)
        self.position_btc = cost / price
        self.entry_price = price
        self.highest_since_entry = price
        self.trailing_stop = 0.0
        self.capital = 0.0
        self.is_holding = True
        self.trades.append({
            "entry_date": date,
            "entry_price": round(price, 0),
            "exit_date": "",
            "exit_price": 0.0,
            "return_pct": 0.0,
            "status": "open",
        })

    def close_position(self, price: float, date: str, fee_rate: float = 0.0005):
        """매도 청산."""
        proceeds = self.position_btc * price * (1 - fee_rate)
        ret_pct = (price / self.entry_price) * (1 - fee_rate) ** 2 - 1

        self.capital = proceeds
        self.position_btc = 0.0
        self.is_holding = False

        # 마지막 open 거래 업데이트
        for t in reversed(self.trades):
            if t.get("status") == "open":
                t["exit_date"] = date
                t["exit_price"] = round(price, 0)
                t["return_pct"] = round(ret_pct, 6)
                t["status"] = "closed"
                break

        self.entry_price = 0.0
        self.highest_since_entry = 0.0
        self.trailing_stop = 0.0


def save_state(state: PaperState, path: Path = STATE_FILE):
    state.last_updated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)


def load_state(path: Path = STATE_FILE) -> PaperState:
    if not path.exists():
        return PaperState()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return PaperState(**data)
