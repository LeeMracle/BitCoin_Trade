"""BTC 단독 학습 vs 11종 통합 학습 비교.

평가 방식:
    - 각 fold의 validation set은 BTC 신호만 추출 → BTC 추론 우위 비교
    - "BTC만 학습" vs "전체 학습" 두 모델로 같은 BTC validation에서 평가
    - 같은 walk-forward 시간 분할 사용
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.ml import feature_store
from services.ml.config import FEATURE_COLUMNS, ML_FILTER_THRESHOLD, XGB_PARAMS_DEFAULT


def split_btc_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["symbol"] == "BTC-KRW"].sort_values("signal_ts").reset_index(drop=True)


def to_xy(df: pd.DataFrame):
    X = df[FEATURE_COLUMNS].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values
    y = df["label"].astype(int).values
    return X, y


def main() -> int:
    df_all = feature_store.read_range(pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp.now(tz="UTC"))
    df_all = df_all.sort_values("signal_ts").reset_index(drop=True)
    df_btc = split_btc_only(df_all)
    print(f"전체: {len(df_all)} rows, positive {df_all['label'].mean():.1%}")
    print(f"BTC : {len(df_btc)} rows, positive {df_btc['label'].mean():.1%}")

    # BTC 시계열에 walk-forward
    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)

    btc_X, btc_y = to_xy(df_btc)
    rows = []

    for fold, (tr_btc, va_btc) in enumerate(tscv.split(btc_X)):
        if btc_y[tr_btc].sum() == 0 or btc_y[va_btc].sum() == 0:
            continue
        # 시간 cutoff = train 마지막 시점
        cutoff = df_btc.iloc[tr_btc[-1]]["signal_ts"]
        # 통합 모델: cutoff 이전 전체 (BTC + 알트)
        train_all = df_all[df_all["signal_ts"] <= cutoff]
        Xa, ya = to_xy(train_all)

        # 모델 1: BTC 단독
        m_btc = xgb.XGBClassifier(**XGB_PARAMS_DEFAULT)
        m_btc.fit(btc_X[tr_btc], btc_y[tr_btc])
        proba_btc = m_btc.predict_proba(btc_X[va_btc])[:, 1]

        # 모델 2: 통합 학습
        m_all = xgb.XGBClassifier(**XGB_PARAMS_DEFAULT)
        m_all.fit(Xa, ya)
        proba_all = m_all.predict_proba(btc_X[va_btc])[:, 1]

        auc_btc = roc_auc_score(btc_y[va_btc], proba_btc)
        auc_all = roc_auc_score(btc_y[va_btc], proba_all)
        prec_btc = precision_score(btc_y[va_btc], (proba_btc >= ML_FILTER_THRESHOLD).astype(int), zero_division=0)
        prec_all = precision_score(btc_y[va_btc], (proba_all >= ML_FILTER_THRESHOLD).astype(int), zero_division=0)

        rows.append({
            "fold": fold,
            "n_train_btc": len(tr_btc),
            "n_train_all": len(train_all),
            "n_val_btc": len(va_btc),
            "auc_btc_only": auc_btc,
            "auc_all": auc_all,
            "prec_btc_only": prec_btc,
            "prec_all": prec_all,
        })

    print()
    print(f"{'fold':<4} {'tr_btc':<7} {'tr_all':<7} {'val':<5} "
          f"{'AUC btc':<10} {'AUC all':<10} {'diff':<8} "
          f"{'Prec btc':<10} {'Prec all':<10}")
    print("-" * 80)
    for r in rows:
        print(f"{r['fold']:<4} {r['n_train_btc']:<7} {r['n_train_all']:<7} {r['n_val_btc']:<5} "
              f"{r['auc_btc_only']:.4f}     {r['auc_all']:.4f}     "
              f"{r['auc_all'] - r['auc_btc_only']:+.4f}  "
              f"{r['prec_btc_only']:.4f}     {r['prec_all']:.4f}")
    print()
    mean_btc = np.mean([r["auc_btc_only"] for r in rows])
    mean_all = np.mean([r["auc_all"] for r in rows])
    print(f"평균 AUC: BTC단독={mean_btc:.4f}  통합={mean_all:.4f}  차={mean_all - mean_btc:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
