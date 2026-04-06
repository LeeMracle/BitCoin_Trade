"""
regime_tagger.py — BTC/KRW 시장 레짐 태깅 스크립트

목적:
    2017-10 ~ 2026-04 BTC/KRW 일봉 데이터를 시장 레짐(상승/횡보/하락/극공포/과열)으로
    구간 태깅하여 레짐별 백테스트 분리 분석의 기반 데이터를 생성한다.

레짐 정의:
    1차 (200EMA 기반, 전 기간 적용):
        BULL     : 종가 > 200EMA AND 200EMA 20일 기울기 > 0
        SIDEWAYS : 종가가 200EMA ±5% 이내 OR 200EMA 기울기 ≈ 0
        BEAR     : 종가 < 200EMA AND 200EMA 기울기 < 0

    2차 보정 (F&G, 2018-02 이후):
        CRISIS   : F&G < 20  → 1차 결과 무시, 강제 오버라이드
        EUPHORIA : F&G > 80  AND 1차 결과 == BULL → BULL 내 세분류

    특수:
        WARMUP   : 최초 200봉 (EMA 안정화 구간) — 태깅 불가

산출물:
    output/regime_tags.csv    — 일별 레짐 레이블
    output/regime_summary.md  — 레짐별 통계 요약
"""

import sys
import os
from pathlib import Path

# UTF-8 출력 강제
os.environ.setdefault("PYTHONUTF8", "1")

import duckdb
import pandas as pd

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "cache.duckdb"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_CSV = OUTPUT_DIR / "regime_tags.csv"
OUTPUT_MD = OUTPUT_DIR / "regime_summary.md"

# ─────────────────────────────────────────────
# 파라미터
# ─────────────────────────────────────────────
EMA_PERIOD = 200          # 200EMA
SLOPE_WINDOW = 20         # EMA 기울기 계산 윈도우 (일)
SLOPE_THRESHOLD = 0.0     # 기울기 ≈ 0 판정 임계값 (정규화 후 절댓값)
SIDEWAYS_BAND = 0.05      # 200EMA ±5% 이내 → 횡보 조건
FG_CRISIS = 20            # F&G < 20 → CRISIS
FG_EUPHORIA = 80          # F&G > 80 → EUPHORIA (BULL 전제)

EXCHANGE = "upbit"
SYMBOL = "BTC/KRW"
TIMEFRAME = "1d"
FG_SERIES_ID = "FEAR_GREED"


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
def load_ohlcv(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """BTC/KRW 일봉 데이터 로드."""
    rows = con.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE exchange = ? AND symbol = ? AND timeframe = ?
        ORDER BY ts
        """,
        [EXCHANGE, SYMBOL, TIMEFRAME],
    ).fetchall()

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.normalize()
    df = df.set_index("date").drop(columns=["ts"])
    return df


def load_fear_greed(con: duckdb.DuckDBPyConnection) -> pd.Series:
    """Fear & Greed Index 로드 → date 인덱스 Series."""
    rows = con.execute(
        """
        SELECT date, value
        FROM macro
        WHERE series_id = ?
        ORDER BY date
        """,
        [FG_SERIES_ID],
    ).fetchall()

    df = pd.DataFrame(rows, columns=["date", "fg_value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")["fg_value"]
    return df


# ─────────────────────────────────────────────
# 레짐 태깅 로직
# ─────────────────────────────────────────────
def compute_ema200(close: pd.Series) -> pd.Series:
    """200EMA 계산 (pandas ewm, adjust=False — 표준 지수이동평균)."""
    return close.ewm(span=EMA_PERIOD, adjust=False).mean()


def compute_slope(ema: pd.Series, window: int = SLOPE_WINDOW) -> pd.Series:
    """
    EMA 기울기 계산.
    방법: (현재 EMA - window일 전 EMA) / window일 전 EMA
    → 상대 변화율(%)이므로 스케일 불변
    """
    prev = ema.shift(window)
    slope = (ema - prev) / prev
    return slope


def tag_regime_primary(
    close: pd.Series,
    ema: pd.Series,
    slope: pd.Series,
) -> pd.Series:
    """
    1차 레짐 태깅 (200EMA 기반).
    반환: BULL / SIDEWAYS / BEAR / WARMUP
    """
    n = len(close)
    labels = pd.Series(index=close.index, dtype=str)

    for i in range(n):
        # WARMUP: EMA가 충분히 안정화되지 않은 구간
        # ewm은 첫 봉부터 계산되나 EMA_PERIOD 개 이전은 불안정
        if i < EMA_PERIOD - 1:
            labels.iloc[i] = "WARMUP"
            continue

        c = close.iloc[i]
        e = ema.iloc[i]
        s = slope.iloc[i]

        if pd.isna(e) or pd.isna(s):
            labels.iloc[i] = "WARMUP"
            continue

        deviation = (c - e) / e  # 종가가 EMA 대비 얼마나 벗어났는지

        # 횡보: 종가가 EMA ±5% 이내 OR 기울기 ≈ 0
        if abs(deviation) <= SIDEWAYS_BAND or abs(s) <= SLOPE_THRESHOLD:
            labels.iloc[i] = "SIDEWAYS"
        elif c > e and s > 0:
            labels.iloc[i] = "BULL"
        elif c < e and s < 0:
            labels.iloc[i] = "BEAR"
        else:
            # 불일치 케이스 (종가 > EMA이나 기울기 < 0, 또는 반대)
            labels.iloc[i] = "SIDEWAYS"

    return labels


def apply_fg_correction(
    primary: pd.Series,
    fg: pd.Series,
) -> pd.Series:
    """
    2차 F&G 보정.
    - F&G < 20 → CRISIS (1차 무시)
    - F&G > 80 AND 1차 == BULL → EUPHORIA
    F&G 없는 날은 1차 결과 유지.
    """
    result = primary.copy()

    # F&G가 존재하는 날짜만 처리
    common_idx = primary.index.intersection(fg.index)

    for date in common_idx:
        fg_val = fg.loc[date]
        if pd.isna(fg_val):
            continue
        if fg_val < FG_CRISIS:
            result.loc[date] = "CRISIS"
        elif fg_val > FG_EUPHORIA and primary.loc[date] == "BULL":
            result.loc[date] = "EUPHORIA"

    return result


# ─────────────────────────────────────────────
# 요약 통계
# ─────────────────────────────────────────────
def build_summary(tagged: pd.DataFrame) -> str:
    """레짐별 통계 요약 마크다운 생성."""
    # WARMUP 제외 유효 데이터
    valid = tagged[tagged["regime"] != "WARMUP"]
    total_valid = len(valid)

    regime_order = ["BULL", "EUPHORIA", "SIDEWAYS", "BEAR", "CRISIS", "WARMUP"]

    lines = []
    lines.append("# 시장 레짐 태깅 요약\n")
    lines.append(f"- 분석 기간: {tagged.index.min().date()} ~ {tagged.index.max().date()}")
    lines.append(f"- 전체 일수: {len(tagged):,}일")
    lines.append(f"- WARMUP 제외 유효 일수: {total_valid:,}일")
    lines.append(f"- 생성 기준일: 2026-04-05\n")

    lines.append("## 레짐별 통계\n")
    lines.append("| 레짐 | 일수 | 비중(%) | 비고 |")
    lines.append("|------|-----:|-------:|------|")

    regime_notes = {
        "BULL": "종가 > 200EMA, 기울기 > 0",
        "EUPHORIA": "BULL + F&G > 80 (과열)",
        "SIDEWAYS": "200EMA ±5% 이내 또는 기울기 ≈ 0",
        "BEAR": "종가 < 200EMA, 기울기 < 0",
        "CRISIS": "F&G < 20 (극공포, 2018-02~)",
        "WARMUP": "200EMA 워밍업 구간 (최초 200봉)",
    }

    counts = tagged["regime"].value_counts()
    for r in regime_order:
        cnt = counts.get(r, 0)
        if r == "WARMUP":
            pct = cnt / len(tagged) * 100
        else:
            pct = cnt / total_valid * 100 if total_valid > 0 else 0
        note = regime_notes.get(r, "")
        lines.append(f"| {r} | {cnt:,} | {pct:.1f}% | {note} |")

    lines.append("")

    # 레짐별 기간 목록
    lines.append("## 레짐별 주요 구간\n")
    for r in ["BULL", "EUPHORIA", "SIDEWAYS", "BEAR", "CRISIS"]:
        subset = valid[valid["regime"] == r]
        if subset.empty:
            continue

        # 연속 구간 추출
        periods = []
        in_period = False
        start = None
        prev_date = None

        for date in subset.index:
            if not in_period:
                start = date
                in_period = True
            elif (date - prev_date).days > 1:
                # 불연속 → 구간 종료
                periods.append((start, prev_date))
                start = date
            prev_date = date

        if in_period:
            periods.append((start, prev_date))

        # 30일 이상 구간만 표시 (짧은 구간 노이즈 제거)
        significant = [(s, e) for s, e in periods if (e - s).days >= 30]

        lines.append(f"### {r} ({len(subset):,}일, {len(significant)}개 주요 구간)")
        if significant:
            for s, e in significant[:20]:  # 최대 20개 표시
                duration = (e - s).days + 1
                lines.append(f"  - {s.date()} ~ {e.date()} ({duration}일)")
        else:
            lines.append("  - 30일 이상 연속 구간 없음")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("BATA 시장 레짐 태거 시작")
    print("=" * 60)

    # 출력 디렉토리 생성
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 데이터 로드 ──
    print(f"\n[1/5] DuckDB 연결: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH), read_only=True)

    print("[2/5] BTC/KRW 일봉 로드 중...")
    ohlcv = load_ohlcv(con)
    print(f"      로드 완료: {len(ohlcv):,}행, {ohlcv.index.min().date()} ~ {ohlcv.index.max().date()}")

    print("[3/5] Fear & Greed Index 로드 중...")
    fg = load_fear_greed(con)
    print(f"      로드 완료: {len(fg):,}행, {fg.index.min().date()} ~ {fg.index.max().date()}")

    con.close()

    # ── 지표 계산 ──
    print("[4/5] 200EMA 및 기울기 계산 중...")
    close = ohlcv["close"]
    ema200 = compute_ema200(close)
    slope = compute_slope(ema200)

    # ── 레짐 태깅 ──
    print("      1차 태깅 (200EMA 기반) 수행 중...")
    primary = tag_regime_primary(close, ema200, slope)

    primary_counts = primary.value_counts().to_dict()
    print(f"      1차 결과: {primary_counts}")

    print("      2차 보정 (F&G 오버라이드) 수행 중...")
    final = apply_fg_correction(primary, fg)

    final_counts = final.value_counts().to_dict()
    print(f"      최종 결과: {final_counts}")

    # ── 결과 DataFrame 조립 ──
    tagged = pd.DataFrame({
        "date": ohlcv.index,
        "close": ohlcv["close"],
        "ema200": ema200.round(0),
        "fg_value": fg.reindex(ohlcv.index),  # F&G 없는 날은 NaN
        "slope_pct": (slope * 100).round(4),  # 퍼센트로 변환
        "regime": final,
    })
    tagged = tagged.reset_index(drop=True)
    tagged["date"] = tagged["date"].dt.date  # date 타입 → YYYY-MM-DD 문자열

    # ── CSV 저장 ──
    print(f"[5/5] 결과 저장 중...")
    tagged.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"      CSV 저장: {OUTPUT_CSV}")

    # ── 요약 통계 ──
    # 요약용 DataFrame (date를 인덱스로)
    tagged_idx = tagged.copy()
    tagged_idx["date"] = pd.to_datetime(tagged_idx["date"])
    tagged_idx = tagged_idx.set_index("date")

    summary_md = build_summary(tagged_idx)
    OUTPUT_MD.write_text(summary_md, encoding="utf-8")
    print(f"      요약 MD 저장: {OUTPUT_MD}")

    # ── 콘솔 출력 ──
    print()
    print("=" * 60)
    print("레짐 태깅 완료 — 요약 통계")
    print("=" * 60)
    print(summary_md)

    # 샘플 확인 (WARMUP 경계 전후)
    print("\n[샘플] 처음 5행:")
    print(tagged.head(5).to_string(index=False))
    print("\n[샘플] 200봉 경계 전후 (196~204행):")
    print(tagged.iloc[195:205].to_string(index=False))
    print("\n[샘플] 최근 5행:")
    print(tagged.tail(5).to_string(index=False))

    print("\n완료.")
    return tagged


if __name__ == "__main__":
    main()
