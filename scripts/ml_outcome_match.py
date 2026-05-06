"""Shadow JSONL outcome 매칭 cron 진입점.

사용:
    PYTHONUTF8=1 python scripts/ml_outcome_match.py            # 최근 3일
    PYTHONUTF8=1 python scripts/ml_outcome_match.py --days 7   # 최근 7일
    PYTHONUTF8=1 python scripts/ml_outcome_match.py --date 20260503  # 단일 날짜

cron 권장: 매일 KST 03:00 (UTC 18:00) — 어제 결정의 24h 호라이즌이 충분히 지난 시점.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import outcome_matcher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ml_outcome_match")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=3, help="최근 N일 (기본 3)")
    p.add_argument("--date", default=None, help="단일 날짜 YYYYMMDD")
    args = p.parse_args()

    if args.date:
        r = outcome_matcher.match_date(args.date)
        log.info("결과: %s", r)
    else:
        results = outcome_matcher.match_recent_days(args.days)
        total_matched = sum(r.get("matched", 0) for r in results)
        total_reached = sum(r.get("reached_target", 0) for r in results)
        log.info("=" * 60)
        log.info("전체 매칭: %d건, 도달: %d건 (%.1f%%)",
                 total_matched, total_reached,
                 100 * total_reached / max(total_matched, 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
