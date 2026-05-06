"""ML 신호 필터 효과 분석.

shadow JSONL 누적 → 매칭된 outcome과 결정을 join하여 효과 정량화.

핵심 지표:
    - **차단의 정확도**: ML이 차단(will_buy=false)한 신호 중 실제 +5% 미도달 비율 = TN
    - **차단의 비용 (false negative)**: ML이 차단했지만 실제 도달한 신호 비율 = FN
    - **허용의 정확도**: ML이 허용한 신호 중 실제 도달 비율 = TP
    - **허용의 비용 (false positive)**: 허용했지만 미도달 비율 = FP
    - **가상 PnL**: 차단/허용 결정에 따른 단순 +5% 가정 손익 비교 (vs 무필터)

사용:
    PYTHONUTF8=1 python scripts/ml_effect_analysis.py            # 최근 30일
    PYTHONUTF8=1 python scripts/ml_effect_analysis.py --days 90  # 90일
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml.config import LABEL_TARGET_PCT, SHADOW_LOG_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ml_effect")


def _load_records(days: int) -> tuple[list[dict], list[dict]]:
    """최근 N일 + 매칭 가능한 신호일까지 모두 스캔.
    decisions, outcomes 분리 반환.
    """
    decisions: list[dict] = []
    outcomes: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(SHADOW_LOG_DIR.glob("*.jsonl")):
        try:
            stamp = datetime.strptime(path.stem.replace(".local-test", ""), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamp < cutoff:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("kind") == "outcome":
                outcomes.append(rec)
            else:
                decisions.append(rec)
    return decisions, outcomes


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()

    decisions, outcomes = _load_records(args.days)
    log.info("=" * 60)
    log.info("ML 신호 필터 효과 분석 (최근 %d일)", args.days)
    log.info("=" * 60)
    log.info("결정 (decisions):     %d건", len(decisions))
    log.info("outcome 매칭:        %d건", len(outcomes))

    # outcome 매핑 (signal_ts + symbol)
    outcome_map = {(o["signal_ts"], o["symbol"]): o for o in outcomes}

    # ml_active 결정만 추출 (모델이 실제 추론한 케이스)
    matched = []
    for d in decisions:
        if not d.get("ml_active"):
            continue
        key = (d.get("signal_ts", ""), d.get("symbol", ""))
        o = outcome_map.get(key)
        if o is None:
            continue
        matched.append({**d, "reached": o["reached_target"], "outcome_pct": o["outcome_pct"]})

    if not matched:
        log.info("\nml_active=true & outcome 매칭된 결정 없음 — 분석 불가 (운영 데이터 부족)")
        return 0

    log.info("ml_active 매칭:      %d건", len(matched))

    # confusion matrix
    tp = sum(1 for m in matched if m["will_buy"] and m["reached"])
    fp = sum(1 for m in matched if m["will_buy"] and not m["reached"])
    tn = sum(1 for m in matched if not m["will_buy"] and not m["reached"])
    fn = sum(1 for m in matched if not m["will_buy"] and m["reached"])

    log.info("\n[Confusion Matrix]")
    log.info("              실제 도달       실제 미도달")
    log.info("ML 허용  →   TP %4d         FP %4d", tp, fp)
    log.info("ML 차단  →   FN %4d         TN %4d", fn, tn)

    n = len(matched)
    accuracy = (tp + tn) / n if n else 0
    precision = tp / (tp + fp) if (tp + fp) else 0
    recall = tp / (tp + fn) if (tp + fn) else 0
    block_rate = (fn + tn) / n if n else 0

    log.info("\n[지표]")
    log.info("  Accuracy:   %.3f  (TP+TN)/N", accuracy)
    log.info("  Precision:  %.3f  TP/(TP+FP) — 허용한 신호 중 실제 도달률", precision)
    log.info("  Recall:     %.3f  TP/(TP+FN) — 실제 도달 신호 중 허용 비율", recall)
    log.info("  차단률:     %.3f  ML이 차단한 비율", block_rate)

    # 가상 PnL (단순화: 도달=+5%, 미도달=평균 outcome_pct)
    # 시나리오 A: ML 무시 (모두 매수) → sum(outcome_pct)
    # 시나리오 B: ML 게이트 (will_buy만 매수) → sum(outcome_pct where will_buy)
    pnl_no_ml = sum(m["outcome_pct"] for m in matched)
    pnl_with_ml = sum(m["outcome_pct"] for m in matched if m["will_buy"])
    log.info("\n[가상 PnL — outcome_pct 단순 합산]")
    log.info("  무필터:     %+.4f (모든 신호 매수)", pnl_no_ml)
    log.info("  ML 필터:    %+.4f (허용 신호만 매수)", pnl_with_ml)
    log.info("  ML 효과:    %+.4f", pnl_with_ml - pnl_no_ml)

    # 점수 분포
    scores = [m["score"] for m in matched]
    if scores:
        from statistics import mean, stdev
        log.info("\n[점수 분포]")
        log.info("  mean: %.3f, std: %.3f, min: %.3f, max: %.3f",
                 mean(scores), stdev(scores) if len(scores) > 1 else 0,
                 min(scores), max(scores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
