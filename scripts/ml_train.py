"""ML 신호 필터 학습 진입점 (로컬 PC 전용).

사용:
    # 실데이터 학습
    PYTHONUTF8=1 python scripts/ml_train.py --start 2023-01-01 --end 2026-04-30

    # dry-run (dummy 데이터 생성 후 학습 — 코드 경로 무결성 검증)
    PYTHONUTF8=1 python scripts/ml_train.py --dry-run

AWS 서버에서는 절대 실행 금지 (메모리 부족, lessons #5).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import feature_store, labeler, trainer  # noqa: E402
from services.ml.config import ensure_dirs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ml_train")


def main() -> int:
    p = argparse.ArgumentParser(description="ML 신호 필터 학습")
    p.add_argument("--start", default="2023-01-01", help="학습 시작일 (UTC, YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="학습 종료일 (미지정=오늘)")
    p.add_argument("--version", default=None, help="모델 버전 (미지정=YYYYMMDDHHMM)")
    p.add_argument("--no-promote", action="store_true", help="current.pkl 갱신 안 함")
    p.add_argument("--dry-run", action="store_true", help="dummy 데이터로 파이프라인 검증")
    args = p.parse_args()

    ensure_dirs()

    if args.dry_run:
        log.info("[dry-run] dummy 데이터셋 생성 시작")
        n = labeler.make_dummy_dataset(n_trades=400)
        log.info("[dry-run] %d rows 생성, store stats=%s", n, feature_store.stats())

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC")

    log.info("학습 시작: [%s, %s)  promote=%s", start, end, not args.no_promote)
    result = trainer.train(start, end, version=args.version, promote_current=not args.no_promote)

    log.info("=" * 60)
    log.info("저장: %s", result.model_path)
    log.info("메타: %s", result.meta_path)
    log.info("CV mean AUC: %.4f", result.metrics.get("mean_auc", 0.0))
    log.info("CV mean Precision: %.4f", result.metrics.get("mean_precision", 0.0))
    log.info("=" * 60)

    # 메타 요약 stdout
    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    print(json.dumps({
        "version": meta["version"],
        "n_samples": meta["n_samples"],
        "positive_rate": round(meta["positive_rate"], 4),
        "mean_auc": round(meta["cv_metrics"]["mean_auc"], 4),
        "top_features": sorted(meta["feature_importance"].items(), key=lambda x: -x[1])[:5],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
