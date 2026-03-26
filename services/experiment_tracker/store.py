"""SQLite 기반 실험 추적 저장소."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "experiments.db"


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def init_schema(db_path: Path | None = None) -> None:
    with _conn(db_path) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                strategy_id   TEXT NOT NULL,
                description   TEXT DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id          TEXT PRIMARY KEY,
                experiment_id   TEXT NOT NULL,
                params_json     TEXT NOT NULL,
                metrics_json    TEXT NOT NULL,
                artifact_paths  TEXT NOT NULL,
                logged_at       TEXT NOT NULL,
                FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
            );
        """)


def create_experiment(
    name: str, strategy_id: str, description: str = "", db_path: Path | None = None
) -> dict:
    experiment_id = str(uuid.uuid4())[:12]
    created_at = _now()
    with _conn(db_path) as con:
        con.execute(
            "INSERT INTO experiments VALUES (?,?,?,?,?)",
            (experiment_id, name, strategy_id, description, created_at),
        )
    return {"experiment_id": experiment_id, "created_at": created_at}


def log_run(
    experiment_id: str,
    run_id: str,
    params: dict,
    metrics: dict,
    artifact_paths: list[str],
    db_path: Path | None = None,
) -> dict:
    logged_at = _now()
    with _conn(db_path) as con:
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?)",
            (
                run_id,
                experiment_id,
                json.dumps(params, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(artifact_paths, ensure_ascii=False),
                logged_at,
            ),
        )
    return {"run_id": run_id, "logged_at": logged_at}


def compare_runs(
    experiment_id: str,
    run_ids: list[str],
    sort_by: str = "sharpe",
    db_path: Path | None = None,
) -> dict:
    with _conn(db_path) as con:
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            rows = con.execute(
                f"SELECT run_id, metrics_json, params_json FROM runs "
                f"WHERE experiment_id=? AND run_id IN ({placeholders})",
                [experiment_id, *run_ids],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT run_id, metrics_json, params_json FROM runs WHERE experiment_id=?",
                [experiment_id],
            ).fetchall()

    summaries = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        summaries.append({
            "run_id": row["run_id"],
            "sort_key": metrics.get(sort_by, 0),
            "metrics": metrics,
            "params": json.loads(row["params_json"]),
        })

    summaries.sort(key=lambda x: x["sort_key"], reverse=True)
    best = summaries[0]["run_id"] if summaries else None
    return {"runs": summaries, "best_run_id": best}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
