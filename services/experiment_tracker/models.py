from pydantic import BaseModel
from typing import Optional


class ExperimentRecord(BaseModel):
    experiment_id: str
    name: str
    strategy_id: str
    description: str = ""
    created_at: str


class RunRecord(BaseModel):
    run_id: str
    experiment_id: str
    params: dict
    metrics: dict
    artifact_paths: list[str]
    logged_at: str
