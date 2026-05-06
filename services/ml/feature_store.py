"""Feature Store — Parquet 기반 학습 데이터 저장/조회.

학습 시 라벨러가 생성한 (features + label + meta) 행을 누적 저장.
파일 단위: 하루 1개 (`{YYYYMMDD}.parquet`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from services.ml.config import FEATURE_COLUMNS, FEATURE_DIR, ensure_dirs


META_COLUMNS = ["symbol", "signal_ts", "label", "label_horizon_bars"]
ALL_COLUMNS = META_COLUMNS + FEATURE_COLUMNS


def _path_for(date: pd.Timestamp) -> Path:
    return FEATURE_DIR / f"{date.strftime('%Y%m%d')}.parquet"


def write_rows(rows: Iterable[dict]) -> int:
    """행 dict 리스트를 날짜별 parquet에 append (덮어쓰기 머지).

    Returns: 저장된 행 수
    """
    ensure_dirs()
    df = pd.DataFrame(list(rows))
    if df.empty:
        return 0

    missing = [c for c in ALL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"feature_store missing columns: {missing}")

    df["signal_ts"] = pd.to_datetime(df["signal_ts"], utc=True)
    df["_date"] = df["signal_ts"].dt.tz_convert("UTC").dt.date

    n = 0
    for date_val, group in df.groupby("_date"):
        path = _path_for(pd.Timestamp(date_val))
        out = group.drop(columns="_date")[ALL_COLUMNS]
        if path.exists():
            existing = pd.read_parquet(path)
            out = pd.concat([existing, out], ignore_index=True)
            out = out.drop_duplicates(subset=["symbol", "signal_ts"], keep="last")
        out.to_parquet(path, index=False)
        n += len(group)
    return n


def read_range(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """[start, end) 기간의 모든 feature 행을 반환."""
    ensure_dirs()
    frames = []
    start_day = start.tz_convert("UTC").normalize() if start.tzinfo else start.tz_localize("UTC").normalize()
    end_day = end.tz_convert("UTC").normalize() if end.tzinfo else end.tz_localize("UTC").normalize()
    for path in sorted(FEATURE_DIR.glob("*.parquet")):
        try:
            stamp = pd.Timestamp(path.stem).tz_localize("UTC")
        except ValueError:
            continue
        if stamp < start_day or stamp >= end_day + pd.Timedelta(days=1):
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=ALL_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["signal_ts"] = pd.to_datetime(df["signal_ts"], utc=True)
    return df[(df["signal_ts"] >= start) & (df["signal_ts"] < end)].reset_index(drop=True)


def stats(start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None) -> dict:
    """저장된 데이터 요약 (행수, positive 비율, 기간)."""
    if start is None:
        start = pd.Timestamp("2020-01-01", tz="UTC")
    if end is None:
        end = pd.Timestamp.now(tz="UTC")
    df = read_range(start, end)
    if df.empty:
        return {"rows": 0}
    return {
        "rows": len(df),
        "positive_rate": float(df["label"].mean()),
        "symbols": int(df["symbol"].nunique()),
        "first_signal": str(df["signal_ts"].min()),
        "last_signal": str(df["signal_ts"].max()),
    }
