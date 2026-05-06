"""운영 추론 — MLFilter 클래스 (multi_trader가 import).

핵심 정책 (lessons #21 역케이스):
    - **fail-open**: 모델 파일 없거나 로드 실패 → passes() 항상 True (기존 로직 보존)
    - **싱글톤 권장**: 모듈 import 시 1회 로드, 메모리 상주
    - **카탈로그 일치 강제**: meta의 feature_columns ≠ 현재 config.FEATURE_COLUMNS → fail-open
    - **OHLCV 자동 fetch (P8-23)**: ohlcv 미주입 시 ccxt 4h fetch + 60s LRU 캐시
    - **ML_SHADOW_MODE (P8-23)**: passes()가 항상 True 반환 — 점수만 기록, 차단 X

사용 예 (multi_trader.py)::

    from services.ml.inference import get_filter
    flt = get_filter()
    score = flt.score(symbol, ohlcv_df=None)  # None이면 자동 fetch
    if not flt.passes(score):
        return  # 매수 차단 (단 ML_SHADOW_MODE=1이면 항상 통과)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from services.ml import config as _cfg
from services.ml.config import (
    CURRENT_MODEL_META,
    CURRENT_MODEL_PATH,
    FEATURE_COLUMNS,
    ML_FILTER_THRESHOLD,
)
from services.ml.features import MarketContext, compute_features, features_to_vector

log = logging.getLogger(__name__)


class MLFilter:
    """매수 신호 필터. 모델 부재/오류 시 fail-open."""

    def __init__(
        self,
        model_path: Path = CURRENT_MODEL_PATH,
        meta_path: Path = CURRENT_MODEL_META,
        threshold: float = ML_FILTER_THRESHOLD,
        *,
        enabled: Optional[bool] = None,
    ):
        self.model_path = model_path
        self.meta_path = meta_path
        self.threshold = threshold
        # enabled None이면 호출 시점에 환경변수를 다시 평가 (테스트/런타임 토글 지원)
        self.enabled = enabled if enabled is not None else (
            __import__("os").getenv("ML_FILTER_ENABLED", "0") == "1"
        )
        self.model = None
        self.meta: Optional[dict] = None
        self._load_failure: Optional[str] = None
        self._load_if_possible()

    # ── 내부: 모델 로드 ───────────────────────────────
    def _load_if_possible(self) -> None:
        if not self.enabled:
            self._load_failure = "disabled (ML_FILTER_ENABLED!=1)"
            return
        if not self.model_path.exists():
            self._load_failure = f"model not found: {self.model_path}"
            log.warning(self._load_failure)
            return
        try:
            self.model = joblib.load(self.model_path)
            if self.meta_path.exists():
                self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                meta_cols = self.meta.get("feature_columns", [])
                if meta_cols and meta_cols != FEATURE_COLUMNS:
                    self._load_failure = (
                        f"feature mismatch: meta={len(meta_cols)} vs config={len(FEATURE_COLUMNS)}"
                    )
                    log.error(self._load_failure)
                    self.model = None
                    return
            log.info("MLFilter loaded: %s threshold=%.2f", self.model_path.name, self.threshold)
        except Exception as e:  # joblib/pickle 호환성 문제 방어
            self._load_failure = f"load error: {e}"
            log.exception("MLFilter load failed")
            self.model = None

    # ── 공개 API ──────────────────────────────────────
    @property
    def is_active(self) -> bool:
        """추론 가능 여부 (False면 fail-open 모드)."""
        return self.model is not None

    @property
    def status(self) -> str:
        return "active" if self.is_active else f"fail-open ({self._load_failure})"

    def score(
        self,
        symbol: str,
        ohlcv_df: Optional[pd.DataFrame] = None,
        at_ts: Optional[pd.Timestamp] = None,
        market_ctx: Optional[MarketContext] = None,
    ) -> float:
        """0~1 점수 반환. fail-open 모드(모델 부재/오류)에선 항상 1.0.

        Args:
            ohlcv_df: None이면 ccxt 4h 자동 fetch + 60s LRU cache (P8-23)
            at_ts: None이면 현재 시각
        """
        if not self.is_active:
            return 1.0
        try:
            if at_ts is None:
                at_ts = pd.Timestamp.now(tz="UTC")
            if ohlcv_df is None:
                ohlcv_df = _fetch_ohlcv_cached(symbol)
                if ohlcv_df is None or len(ohlcv_df) < 50:
                    return 1.0  # fetch 실패 → fail-open
            feat = compute_features(symbol, ohlcv_df, at_ts, market_ctx)
            x = features_to_vector(feat).reshape(1, -1)
            x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
            return float(self.model.predict_proba(x)[0][1])
        except Exception as e:
            log.warning("score failed %s @%s: %s — fail-open", symbol, at_ts, e)
            return 1.0

    def passes(self, score: float) -> bool:
        """gate 판정. 다음 조건 중 하나라도 True면 통과:
           1) 모델 미로드 (fail-open)
           2) ML_SHADOW_MODE=1 (점수만 기록, 차단 X — P8-23 안전 운영)
           3) score ≥ threshold (실제 LIVE 게이트)
        """
        if not self.is_active:
            return True
        if os.getenv("ML_SHADOW_MODE", "1") == "1":
            return True  # shadow 모드 — 차단 없이 점수만 누적
        return score >= self.threshold


# ── OHLCV 자동 fetch + 60s LRU 캐시 (P8-23) ───────────────
# ccxt upbit 인스턴스 (lessons #21: 모듈 싱글톤 + enableRateLimit)
_ccxt_instance = None
_ccxt_lock = threading.Lock()
# 캐시: symbol → (timestamp, DataFrame). TTL 60초.
_ohlcv_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_OHLCV_CACHE_TTL = 60.0


def _get_ccxt() -> "ccxt.upbit":  # type: ignore  # noqa: F821
    global _ccxt_instance
    if _ccxt_instance is None:
        with _ccxt_lock:
            if _ccxt_instance is None:
                import ccxt
                _ccxt_instance = ccxt.upbit({"enableRateLimit": True})
    return _ccxt_instance


def _fetch_ohlcv_cached(symbol: str, timeframe: str = "4h", limit: int = 50) -> Optional[pd.DataFrame]:
    """심볼의 4h OHLCV를 LRU 캐시로 조회. 인증 불필요(공개 API).

    symbol: 'KRW-BTC' (Upbit 형식). ccxt는 'BTC/KRW' 사용 → 자동 변환.
    """
    now = time.monotonic()
    cached = _ohlcv_cache.get(symbol)
    if cached is not None and (now - cached[0]) < _OHLCV_CACHE_TTL:
        return cached[1]

    # 'KRW-BTC' → 'BTC/KRW'
    if "-" in symbol:
        a, b = symbol.split("-", 1)
        ccxt_sym = f"{b}/{a}" if a in ("KRW", "USDT", "BTC") else f"{a}/{b}"
    elif "/" in symbol:
        ccxt_sym = symbol
    else:
        return None

    try:
        ex = _get_ccxt()
        bars = ex.fetch_ohlcv(ccxt_sym, timeframe=timeframe, limit=limit)
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts")
        _ohlcv_cache[symbol] = (now, df)
        # 캐시 크기 제한 (200 종목 × 50봉 = 약 8MB)
        if len(_ohlcv_cache) > 250:
            # 가장 오래된 항목 제거
            oldest = min(_ohlcv_cache.items(), key=lambda kv: kv[1][0])
            _ohlcv_cache.pop(oldest[0], None)
        return df
    except Exception as e:
        log.warning("ohlcv fetch failed %s: %s", symbol, e)
        return None


# ── 싱글톤 ────────────────────────────────────────────
_lock = threading.Lock()
_instance: Optional[MLFilter] = None


def get_filter() -> MLFilter:
    """프로세스당 1회 로드. multi_trader가 매수 분기마다 호출해도 안전."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = MLFilter()
    return _instance


def reset_filter() -> None:
    """테스트/재로드용 — 운영에서는 호출 X."""
    global _instance
    with _lock:
        _instance = None
