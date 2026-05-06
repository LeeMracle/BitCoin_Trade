"""ML LIVE 1주 평가 — 자동 텔레그램 보고 (P8-27 / P5-30 통합).

평가 기간: 활성화일(인자 또는 기본 5-5) ~ 오늘
출력: 텔레그램 마크다운 메시지

평가 항목:
    1. 실거래 PF/승률/Expectancy (closed_trades from state)
    2. ML 차단 통계 (record_block / shadow JSONL)
    3. 차단 outcome calibration (ml_effect_analysis 재사용)
    4. P5-30 매매 빈도 변화
    5. 권고 (강화 / 유지 / 롤백)

사용:
    PYTHONUTF8=1 python scripts/ml_weekly_review.py
    PYTHONUTF8=1 python scripts/ml_weekly_review.py --start 2026-05-05 --end 2026-05-12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402
from services.ml.config import SHADOW_LOG_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("ml_weekly")

KST = timezone(timedelta(hours=9))
PROJECT = Path("/home/ubuntu/BitCoin_Trade") if Path("/home/ubuntu/BitCoin_Trade").exists() else ROOT


def _load_state() -> dict:
    p = PROJECT / "workspace" / "multi_trading_state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _filter_closed_in_period(closed: list[dict], start: datetime, end: datetime) -> list[dict]:
    out = []
    for t in closed:
        try:
            ed = datetime.strptime(t["exit_date"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if start <= ed <= end:
                out.append(t)
        except Exception:
            continue
    return out


def _trade_metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t.get("return_pct", 0) > 0]
    losses = [t for t in trades if t.get("return_pct", 0) <= 0]
    pw = len(wins) / n
    aw = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0
    al = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0
    gp = sum(t["return_pct"] for t in wins) if wins else 0
    gl = -sum(t["return_pct"] for t in losses) if losses else 0
    pf = gp / gl if gl > 0 else float("inf")
    return {
        "n": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": pw, "avg_win": aw, "avg_loss": al,
        "profit_factor": pf,
        "expectancy": pw * aw + (1 - pw) * al,
        "total": sum(t.get("return_pct", 0) for t in trades),
    }


def _shadow_stats(start: datetime, end: datetime) -> dict:
    """shadow JSONL에서 결정 분포 + outcome 매칭 결과."""
    decisions = []
    outcomes = []
    shadow_dir = PROJECT / "workspace" / "ml_shadow"
    if not shadow_dir.exists():
        return {"decisions": 0, "outcomes": 0}
    for path in sorted(shadow_dir.glob("*.jsonl")):
        try:
            stamp = datetime.strptime(path.stem.split(".")[0], "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < start - timedelta(days=2) or stamp > end + timedelta(days=2):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("kind") == "outcome":
                outcomes.append(rec)
            else:
                try:
                    ts = datetime.fromisoformat(rec.get("ts_utc", "").replace("Z", "+00:00"))
                    if start <= ts <= end:
                        decisions.append(rec)
                except Exception:
                    continue

    n = len(decisions)
    if n == 0:
        return {"decisions": 0, "outcomes": len(outcomes)}
    n_buy = sum(1 for r in decisions if r.get("will_buy"))
    n_block = n - n_buy
    n_active = sum(1 for r in decisions if r.get("ml_active"))
    score_active = [r["score"] for r in decisions if r.get("ml_active")]
    block_active = [r for r in decisions if r.get("ml_active") and not r.get("will_buy")]

    # outcome confusion (ml_active 결정만)
    outcome_map = {(o["signal_ts"], o["symbol"]): o for o in outcomes}
    tp = fp = tn = fn = 0
    for d in decisions:
        if not d.get("ml_active"):
            continue
        key = (d.get("signal_ts", ""), d.get("symbol", ""))
        o = outcome_map.get(key)
        if o is None:
            continue
        reached = o["reached_target"]
        if d["will_buy"] and reached: tp += 1
        elif d["will_buy"] and not reached: fp += 1
        elif not d["will_buy"] and reached: fn += 1
        else: tn += 1
    matched = tp + fp + tn + fn

    return {
        "decisions": n, "buys": n_buy, "blocks": n_block,
        "ml_active": n_active,
        "block_rate": n_block / n if n else 0,
        "mean_score": sum(score_active) / len(score_active) if score_active else 0,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "matched": matched,
    }


def _build_message(start_kst: datetime, end_kst: datetime,
                    trades_metrics: dict, shadow: dict, threshold: float) -> str:
    lines = []
    lines.append(f"📊 *ML LIVE 1주 평가* ({start_kst:%m-%d}~{end_kst:%m-%d})")
    lines.append("")
    lines.append("*실거래 (closed)*")
    if trades_metrics["n"] == 0:
        lines.append("  • 거래 0건 — 평가 불가")
    else:
        m = trades_metrics
        lines.append(f"  • 거래: {m['n']}건 (승 {m['wins']}/패 {m['losses']})")
        lines.append(f"  • 승률: {m['win_rate']*100:.1f}%")
        lines.append(f"  • 평균: 승 {m['avg_win']*100:+.2f}% / 패 {m['avg_loss']*100:+.2f}%")
        lines.append(f"  • PF: {m['profit_factor']:.2f}")
        lines.append(f"  • Expectancy: {m['expectancy']*100:+.3f}%/거래")
        lines.append(f"  • 누적: {m['total']:+.2f}%")
    lines.append("")

    lines.append(f"*ML 차단 (threshold {threshold:.2f})*")
    if shadow["decisions"] == 0:
        lines.append("  • 의사결정 0건 — shadow log 비어있음")
    else:
        s = shadow
        lines.append(f"  • 결정: {s['decisions']}건 (buy {s['buys']}/block {s['blocks']})")
        lines.append(f"  • 차단률: {s['block_rate']*100:.1f}%")
        lines.append(f"  • 평균 score: {s['mean_score']:.3f}")
        if s["matched"] > 0:
            lines.append("")
            lines.append("*Outcome 매칭 (정확도)*")
            lines.append(f"  • TP {s['tp']} / FP {s['fp']} / TN {s['tn']} / FN {s['fn']}")
            acc = (s["tp"] + s["tn"]) / s["matched"]
            lines.append(f"  • Accuracy: {acc*100:.1f}%")
    lines.append("")

    # 권고
    lines.append("*권고*")
    if trades_metrics["n"] == 0:
        lines.append("  ⚠️ 매수 0건 — threshold 너무 높음 검토 (0.40 완화 또는 SHADOW 복귀)")
    elif trades_metrics["profit_factor"] >= 1.1:
        lines.append("  ✅ PF≥1.1 → threshold 0.50 강화 검토")
    elif trades_metrics["profit_factor"] >= 1.0:
        lines.append("  🟡 PF≥1.0 break-even — 1주 더 0.45 유지 후 재평가")
    elif trades_metrics["profit_factor"] >= 0.95:
        lines.append("  🟡 PF<1.0 — 0.45 1주 더 모니터링, 다음주도 미달 시 SHADOW 복귀")
    else:
        lines.append("  🔴 PF<0.95 — SHADOW 복귀 권장 (`ML_SHADOW_MODE=1`)")

    lines.append("")
    lines.append(f"_평가 시각: {datetime.now(tz=KST):%Y-%m-%d %H:%M KST}_")
    return "\n".join(lines)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-05-05", help="평가 시작일 (KST)")
    p.add_argument("--end", default=None, help="평가 종료일 (KST). 미지정=오늘")
    p.add_argument("--threshold", type=float, default=0.45)
    p.add_argument("--no-send", action="store_true", help="텔레그램 발송 안 함 (출력만)")
    args = p.parse_args()

    start_kst = datetime.fromisoformat(args.start).replace(tzinfo=KST)
    end_kst = datetime.fromisoformat(args.end).replace(tzinfo=KST) if args.end else datetime.now(tz=KST)
    start_utc = start_kst.astimezone(timezone.utc)
    end_utc = end_kst.astimezone(timezone.utc)

    log.info(f"평가 기간: {start_kst:%Y-%m-%d %H:%M} ~ {end_kst:%Y-%m-%d %H:%M} KST")

    state = _load_state()
    closed = state.get("closed_trades", [])
    period_trades = _filter_closed_in_period(closed, start_utc, end_utc)
    trades_metrics = _trade_metrics(period_trades)
    shadow = _shadow_stats(start_utc, end_utc)

    msg = _build_message(start_kst, end_kst, trades_metrics, shadow, args.threshold)
    print(msg)

    if not args.no_send:
        ok = await send(msg, parse_mode="Markdown")
        log.info(f"텔레그램 발송: {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
