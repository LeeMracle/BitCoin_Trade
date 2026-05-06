"""XGBoost 학습 + Walk-Forward CV + Model Registry 저장.

핵심:
    - 시간 기반 fold (sklearn TimeSeriesSplit) — 미래 누설 차단 (lessons #1)
    - 모델 + 메타(JSON: feature 목록, 학습 기간, AUC, threshold) 함께 저장
    - registry는 단순 파일 시스템 — current.pkl 심볼릭 링크 / 윈도우는 복사
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

import xgboost as xgb

from services.ml import feature_store
from services.ml.config import (
    CURRENT_MODEL_META,
    CURRENT_MODEL_PATH,
    FEATURE_COLUMNS,
    MODEL_DIR,
    ML_FILTER_THRESHOLD,
    WALK_FORWARD_FOLDS,
    XGB_PARAMS_DEFAULT,
    ensure_dirs,
)

log = logging.getLogger(__name__)


@dataclass
class TrainResult:
    model_path: Path
    meta_path: Path
    metrics: dict = field(default_factory=dict)


def _walk_forward_metrics(X: np.ndarray, y: np.ndarray, n_splits: int) -> dict:
    """시간순 walk-forward CV로 fold별 AUC/precision 산출."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_metrics = []
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        if y[tr_idx].sum() == 0 or y[va_idx].sum() == 0:
            log.warning("fold %d skipped (single-class)", fold)
            continue
        m = xgb.XGBClassifier(**XGB_PARAMS_DEFAULT)
        m.fit(X[tr_idx], y[tr_idx], verbose=False)
        proba = m.predict_proba(X[va_idx])[:, 1]
        pred = (proba >= ML_FILTER_THRESHOLD).astype(int)
        fold_metrics.append({
            "fold": fold,
            "n_train": int(len(tr_idx)),
            "n_val": int(len(va_idx)),
            "auc": float(roc_auc_score(y[va_idx], proba)),
            "precision": float(precision_score(y[va_idx], pred, zero_division=0)),
            "recall": float(recall_score(y[va_idx], pred, zero_division=0)),
            "positive_rate_val": float(y[va_idx].mean()),
        })
    return {
        "folds": fold_metrics,
        "mean_auc": float(np.mean([f["auc"] for f in fold_metrics])) if fold_metrics else 0.0,
        "mean_precision": float(np.mean([f["precision"] for f in fold_metrics])) if fold_metrics else 0.0,
    }


def _materialize_current(model_path: Path, meta_path: Path) -> None:
    """current.pkl / current.meta.json 을 최신 버전으로 가리키게 한다.

    POSIX는 심볼릭 링크, Windows는 복사 (권한 이슈 회피).
    """
    if CURRENT_MODEL_PATH.exists() or CURRENT_MODEL_PATH.is_symlink():
        CURRENT_MODEL_PATH.unlink()
    if CURRENT_MODEL_META.exists() or CURRENT_MODEL_META.is_symlink():
        CURRENT_MODEL_META.unlink()

    if platform.system() == "Windows":
        shutil.copy2(model_path, CURRENT_MODEL_PATH)
        shutil.copy2(meta_path, CURRENT_MODEL_META)
    else:
        CURRENT_MODEL_PATH.symlink_to(model_path.name)
        CURRENT_MODEL_META.symlink_to(meta_path.name)


def train(
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    version: Optional[str] = None,
    promote_current: bool = True,
) -> TrainResult:
    """[start, end) 범위 feature_store 데이터로 학습 + 저장.

    Args:
        version: 미지정 시 'YYYYMMDDHHMM'
        promote_current: current.pkl 심볼릭 갱신 여부
    """
    ensure_dirs()
    df = feature_store.read_range(start, end)
    if df.empty:
        raise RuntimeError(f"no training data in [{start}, {end})")

    # NaN/Inf 안전 처리 — 학습 robust
    X = df[FEATURE_COLUMNS].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y = df["label"].astype(int).values

    if y.sum() == 0 or y.sum() == len(y):
        raise RuntimeError(f"single-class labels (positive={y.sum()}/{len(y)})")

    # Walk-forward CV
    cv_metrics = _walk_forward_metrics(X, y, n_splits=min(WALK_FORWARD_FOLDS, max(2, len(df) // 50)))

    # 최종 모델 (전체 데이터로 fit)
    model = xgb.XGBClassifier(**XGB_PARAMS_DEFAULT)
    model.fit(X, y, verbose=False)

    # 저장
    if version is None:
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    model_path = MODEL_DIR / f"signal_filter_{version}.pkl"
    meta_path = MODEL_DIR / f"signal_filter_{version}.meta.json"

    joblib.dump(model, model_path)
    meta = {
        "version": version,
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_columns": FEATURE_COLUMNS,
        "feature_count": len(FEATURE_COLUMNS),
        "n_samples": int(len(df)),
        "n_positive": int(y.sum()),
        "positive_rate": float(y.mean()),
        "train_start": str(start),
        "train_end": str(end),
        "threshold": ML_FILTER_THRESHOLD,
        "xgb_params": XGB_PARAMS_DEFAULT,
        "cv_metrics": cv_metrics,
        "feature_importance": dict(zip(FEATURE_COLUMNS, [float(x) for x in model.feature_importances_])),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    if promote_current:
        _materialize_current(model_path, meta_path)

    log.info("model saved: %s (mean AUC=%.3f)", model_path, cv_metrics["mean_auc"])
    return TrainResult(model_path=model_path, meta_path=meta_path, metrics=cv_metrics)
