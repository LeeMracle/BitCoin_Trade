"""XGBoost vs LightGBM 비교 (동일 데이터셋, 동일 walk-forward 분할).

사용:
    PYTHONUTF8=1 python scripts/ml_compare_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

import lightgbm as lgb
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import feature_store
from services.ml.config import (
    FEATURE_COLUMNS,
    ML_FILTER_THRESHOLD,
    WALK_FORWARD_FOLDS,
    XGB_PARAMS_DEFAULT,
)


def evaluate(model_name: str, model_factory, X, y, n_splits) -> dict:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_metrics = []
    for fold, (tr, va) in enumerate(tscv.split(X)):
        if y[tr].sum() == 0 or y[va].sum() == 0:
            continue
        m = model_factory()
        m.fit(X[tr], y[tr])
        proba = m.predict_proba(X[va])[:, 1]
        pred = (proba >= ML_FILTER_THRESHOLD).astype(int)
        fold_metrics.append({
            "fold": fold,
            "auc": float(roc_auc_score(y[va], proba)),
            "precision": float(precision_score(y[va], pred, zero_division=0)),
            "recall": float(recall_score(y[va], pred, zero_division=0)),
        })
    aucs = [f["auc"] for f in fold_metrics]
    precs = [f["precision"] for f in fold_metrics]
    recs = [f["recall"] for f in fold_metrics]
    return {
        "model": model_name,
        "mean_auc": float(np.mean(aucs)),
        "std_auc": float(np.std(aucs)),
        "mean_precision": float(np.mean(precs)),
        "mean_recall": float(np.mean(recs)),
        "folds": fold_metrics,
    }


def main() -> int:
    df = feature_store.read_range(pd.Timestamp("2022-01-01", tz="UTC"),
                                  pd.Timestamp.now(tz="UTC"))
    print(f"데이터셋: {len(df)} rows | positive {df['label'].mean():.1%} | features {len(FEATURE_COLUMNS)}")
    X = df[FEATURE_COLUMNS].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y = df["label"].astype(int).values

    n_splits = min(WALK_FORWARD_FOLDS, max(2, len(df) // 50))

    xgb_factory = lambda: xgb.XGBClassifier(**XGB_PARAMS_DEFAULT)
    lgb_factory = lambda: lgb.LGBMClassifier(
        objective="binary",
        n_estimators=300,
        learning_rate=0.05,
        max_depth=4,
        num_leaves=15,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=5,
        random_state=20260504,
        verbose=-1,
    )

    results = []
    for name, factory in (("XGBoost", xgb_factory), ("LightGBM", lgb_factory)):
        r = evaluate(name, factory, X, y, n_splits)
        results.append(r)

    print()
    print(f"{'Model':<10} {'AUC (mean ± std)':<22} {'Precision':<10} {'Recall':<10}")
    print("-" * 56)
    for r in results:
        print(f"{r['model']:<10} {r['mean_auc']:.4f} ± {r['std_auc']:.4f}     "
              f"{r['mean_precision']:.4f}     {r['mean_recall']:.4f}")

    print()
    print("Fold별 AUC:")
    print(f"  {'fold':<6} {'XGBoost':<10} {'LightGBM':<10}  diff")
    for fold in range(n_splits):
        x_auc = next((f["auc"] for f in results[0]["folds"] if f["fold"] == fold), None)
        l_auc = next((f["auc"] for f in results[1]["folds"] if f["fold"] == fold), None)
        if x_auc is not None and l_auc is not None:
            print(f"  {fold:<6} {x_auc:.4f}     {l_auc:.4f}     {l_auc - x_auc:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
