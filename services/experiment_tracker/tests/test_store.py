"""experiment_tracker store 단위 테스트 — in-memory SQLite 사용."""
import pytest
from pathlib import Path

from experiment_tracker.store import (
    compare_runs,
    create_experiment,
    init_schema,
    log_run,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_experiments.db"
    init_schema(db_path)
    return db_path


def test_create_experiment(db):
    result = create_experiment("MA Cross", "ma_cross_v1", db_path=db)
    assert "experiment_id" in result
    assert "created_at" in result


def test_log_run_and_compare(db):
    exp = create_experiment("Test Exp", "strat_001", db_path=db)
    exp_id = exp["experiment_id"]

    log_run(
        experiment_id=exp_id,
        run_id="run_001",
        params={"symbol": "BTC/KRW", "timeframe": "1d", "fee_rate": 0.0005},
        metrics={"sharpe": 1.5, "calmar": 2.0, "max_drawdown": -0.15, "total_return": 0.42},
        artifact_paths=["workspace/runs/run_001/metrics.json"],
        db_path=db,
    )
    log_run(
        experiment_id=exp_id,
        run_id="run_002",
        params={"symbol": "BTC/KRW", "timeframe": "4h", "fee_rate": 0.0005},
        metrics={"sharpe": 0.8, "calmar": 1.2, "max_drawdown": -0.25, "total_return": 0.20},
        artifact_paths=["workspace/runs/run_002/metrics.json"],
        db_path=db,
    )

    result = compare_runs(exp_id, [], sort_by="sharpe", db_path=db)
    assert len(result["runs"]) == 2
    assert result["best_run_id"] == "run_001"
    assert result["runs"][0]["metrics"]["sharpe"] > result["runs"][1]["metrics"]["sharpe"]


def test_compare_specific_runs(db):
    exp = create_experiment("Filtered", "strat_002", db_path=db)
    exp_id = exp["experiment_id"]

    for i in range(3):
        log_run(
            experiment_id=exp_id,
            run_id=f"run_{i:03d}",
            params={},
            metrics={"sharpe": float(i)},
            artifact_paths=[],
            db_path=db,
        )

    result = compare_runs(exp_id, ["run_000", "run_002"], db_path=db)
    assert len(result["runs"]) == 2
