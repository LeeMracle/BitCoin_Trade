"""백테스트 아티팩트 저장 — workspace/runs/{run_id}/"""
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

RUNS_DIR = Path(__file__).parent.parent.parent / "workspace" / "runs"


def save_artifacts(
    run_id: str,
    config: dict,
    metrics,
    equity_curve: pd.DataFrame,
    trade_log: pd.DataFrame,
) -> Path:
    artifact_dir = RUNS_DIR / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    (artifact_dir / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (artifact_dir / "metrics.json").write_text(
        json.dumps(asdict(metrics), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    equity_curve.to_csv(artifact_dir / "equity_curve.csv", index=False)
    trade_log.to_csv(artifact_dir / "trade_log.csv", index=False)

    return artifact_dir
