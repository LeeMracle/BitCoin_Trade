"""ML 시스템 단일 설정 출처 (lessons #19 — 자체정의 금지, 항상 import).

운영/학습/검증 모듈은 본 파일의 상수만 import 한다.
환경변수로 ON/OFF 제어 가능 — 기본은 OFF (fail-open).
"""

from __future__ import annotations

import os
from pathlib import Path

# ── 디렉터리 ─────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
FEATURE_DIR = DATA_DIR / "features"
MODEL_DIR = DATA_DIR / "models"
SHADOW_LOG_DIR = PROJECT_ROOT / "workspace" / "ml_shadow"

# ── 모델 ────────────────────────────────────────────
CURRENT_MODEL_PATH = MODEL_DIR / "current.pkl"
CURRENT_MODEL_META = MODEL_DIR / "current.meta.json"

# ── 운영 토글 (환경변수 우선) ────────────────────────
ML_FILTER_ENABLED = os.getenv("ML_FILTER_ENABLED", "0") == "1"
ML_FILTER_THRESHOLD = float(os.getenv("ML_FILTER_THRESHOLD", "0.55"))
ML_SHADOW_MODE = os.getenv("ML_SHADOW_MODE", "1") == "1"  # 점수 기록만, 차단 X

# ── 라벨링 정책 ─────────────────────────────────────
LABEL_TARGET_PCT = 0.05         # +5% 도달 = positive
LABEL_HORIZON_BARS = 96         # 4일 (15분봉 기준 96봉)
LABEL_SLIPPAGE_PCT = 0.002      # 0.2% 슬리피지 가정 (보수적 라벨)

# ── Feature 카탈로그 (학습/추론 공용 순서 보장) ─────
# v2: 18 → 23 (MACD/BB/Stoch/BTC상관/1d EMA200 추가)
FEATURE_COLUMNS: list[str] = [
    # 기술 지표 (기본)
    "rsi_14", "atr_14_pct", "ema_dist_50", "ema_dist_200",
    "dc_breakout_strength", "vol_ratio_20",
    # 기술 지표 (v2 보강)
    "macd_hist", "bb_width_20", "stoch_k_14",
    # 시장 컨텍스트
    "btc_trend_30d", "btc_dominance", "fear_greed",
    "btc_corr_30d",                  # v2: 해당 종목과 BTC의 30일 상관
    "daily_ema200_dist",             # v2: 1d EMA200 이격
    # 종목 메타
    "volume_krw_24h", "market_cap_rank", "days_since_listing",
    # 시간/캘린더
    "hour_of_day", "day_of_week", "is_weekend",
    # 최근 성과
    "last_7d_return", "max_drawdown_30d", "consecutive_up_days",
]

# ── 학습 파라미터 (트래커 버전과 일치 필수) ──────────
XGB_PARAMS_DEFAULT = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "max_depth": 4,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "random_state": 20260504,
}

WALK_FORWARD_FOLDS = 6


def ensure_dirs() -> None:
    """필수 디렉터리 보장 (idempotent)."""
    for d in (DATA_DIR, FEATURE_DIR, MODEL_DIR, SHADOW_LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
