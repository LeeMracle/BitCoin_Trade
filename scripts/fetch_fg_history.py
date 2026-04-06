"""Fear & Greed 지수 전체 이력 수집 스크립트.

Alternative.me API (limit=0) 으로 2018-01-01 ~ 현재까지 전체 수집하여
DuckDB macro 테이블에 upsert 한다.

실행:
    PYTHONUTF8=1 python scripts/fetch_fg_history.py
"""

import sys
import os
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
from datetime import datetime, timezone, date

from services.market_data.cache import init_schema, upsert_macro, select_macro

SERIES_ID = "FEAR_GREED"
API_URL = "https://api.alternative.me/fng/?limit=0&date_format=kr"
START_DATE = "2018-01-01"


def fetch_all_fg() -> list[dict]:
    """Alternative.me API에서 전체 F&G 이력 수집."""
    print(f"[fetch] API 요청: {API_URL}")
    resp = requests.get(API_URL, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    raw_items = data.get("data", [])
    print(f"[fetch] API 응답 건수: {len(raw_items)}")

    rows = []
    for item in raw_items:
        # date_format=kr 옵션 사용 시 timestamp 필드가 "YYYY-MM-DD" 형식 문자열로 반환
        # 단, 실제 응답 구조는 버전마다 다를 수 있으므로 양쪽 처리
        raw_ts = item.get("timestamp", "")

        # "YYYY-MM-DD" 형태인지 확인
        if len(raw_ts) == 10 and raw_ts[4] == "-":
            d = raw_ts
        else:
            # Unix timestamp 문자열로 처리
            try:
                d = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                print(f"[warn] timestamp 파싱 실패: {raw_ts}")
                continue

        if d < START_DATE:
            continue

        try:
            value = float(item["value"])
        except (ValueError, TypeError, KeyError):
            print(f"[warn] value 파싱 실패 — date={d}, item={item}")
            continue

        label = item.get("value_classification", "")

        rows.append({
            "date": d,
            "value": value,
            "label": label,
        })

    # 날짜 오름차순 정렬
    rows.sort(key=lambda x: x["date"])
    return rows


def verify_db(rows: list[dict]) -> None:
    """수집 완료 후 DB 검증."""
    today = date.today().isoformat()
    db_rows = select_macro(SERIES_ID, START_DATE, today)

    print("\n=== 수집 결과 검증 ===")
    print(f"  수집 건수 (API):  {len(rows):,}")
    print(f"  저장 건수 (DB):   {len(db_rows):,}")

    if db_rows:
        print(f"  최초 날짜:        {db_rows[0]['date']}")
        print(f"  최종 날짜:        {db_rows[-1]['date']}")
        print(f"  최신 값:          {db_rows[-1]['value']} ({db_rows[-1]['label']})")
    else:
        print("  [경고] DB에 데이터가 없습니다.")


def main() -> None:
    print("=== Fear & Greed 전체 이력 수집 시작 ===")
    print(f"  대상 기간: {START_DATE} ~ 현재")
    print(f"  series_id: {SERIES_ID}")

    # DB 스키마 초기화
    init_schema()
    print("[db] 스키마 초기화 완료")

    # API 수집
    rows = fetch_all_fg()

    if not rows:
        print("[오류] 수집된 데이터가 없습니다. API 응답을 확인하세요.")
        sys.exit(1)

    print(f"[fetch] 유효 데이터: {len(rows):,}건 ({rows[0]['date']} ~ {rows[-1]['date']})")

    # DB upsert
    upsert_macro(SERIES_ID, rows)
    print(f"[db] upsert 완료: {len(rows):,}건")

    # 검증
    verify_db(rows)

    print("\n=== 수집 완료 ===")


if __name__ == "__main__":
    main()
