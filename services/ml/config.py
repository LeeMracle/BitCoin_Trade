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

# ── 독립표본 계수 기준 (2026-09-09 확정) ─────────────
# ML 게이트 LIVE 전환 트리거인 "독립표본 N / 800" 의 N 을 정의한다.
# 이 정의는 scripts/ml_shadow_count.py 가 유일하게 구현하며,
# pre_deploy_check.check_ml_shadow_count_basis() 가 이탈을 잡는다.
#
# 표본 1건의 자격 — 아래 4개를 모두 만족하는 로그 행:
#   (1) 신호행일 것            — kind 필드 없음 (kind="outcome" 은 결과 라벨행)
#   (2) 실제 추론일 것         — ml_active is True (False/None 은 비활성 자리표시자 score=1.0)
#   (3) 기준일 이후일 것       — 파일명 날짜 >= SHADOW_SAMPLE_START
#   (4) 라벨이 붙었을 것      — 같은 signal_ts 의 outcome 행이 존재
# 그 후 (symbol, signal_type, UTC날짜) 중복을 제거한다.
#
# (3) 의 근거: 2026-08-25 이전 로그는 (a) 게이트 비활성으로 score=1.0 자리표시자이거나
#   (b) 중복 억제 이전이라 동일 신호가 최대 855회 복제돼 있다. 둘 다 표본이 아니다.
# (4) 의 근거: 800 이라는 목표치는 "도달률이 오르는지"의 검정력에서 나온 수이고,
#   도달률은 outcome 라벨 없이는 계산되지 않는다. 미라벨 신호는 분모에 들어갈 수 없다.
SHADOW_SAMPLE_START = "20260825"   # 중복억제 + 추론재개 배포일 (research/20260825_2)
SHADOW_SAMPLE_TARGET = 800         # 목표 표본 — 변경은 ADR 필요(도메인 파라미터)
SHADOW_DEDUP_KEY = ("symbol", "signal_type", "utc_date")

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
