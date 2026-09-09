"""ML shadow 독립표본 계수 — 이 숫자의 **단일 진실 원천**.

배경: ROADMAP 에 손으로 적힌 "독립표본 186 / 800" 이 어떤 정의로도 재현되지 않았다
(2026-09-09). 정의가 사람 머릿속에만 있었기 때문이다. 이후 이 숫자를 인용하는 모든 곳은
문서를 베끼지 말고 본 스크립트를 실행한다.

정의는 services.ml.config 의 SHADOW_SAMPLE_* 상수에 있다 (lessons #19 — 자체정의 금지).

사용:
    py scripts/ml_shadow_count.py              # 사람이 읽는 요약
    py scripts/ml_shadow_count.py --json       # 기계가 읽는 형태
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ml.config import (  # noqa: E402
    SHADOW_DEDUP_KEY,
    SHADOW_LOG_DIR,
    SHADOW_SAMPLE_START,
    SHADOW_SAMPLE_TARGET,
)

OUTCOME_KIND = "outcome"


def _load_rows(log_dir: Path) -> list[dict]:
    """shadow 로그 전량을 (_date 태그를 붙여) 읽는다. 깨진 행은 건너뛴다."""
    rows: list[dict] = []
    for path in sorted(log_dir.glob("2026*.jsonl")):
        if not path.name.endswith(".jsonl"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["_date"] = path.stem[:8]
            rows.append(rec)
    return rows


def _dedup_key(rec: dict) -> tuple[str, str, str]:
    """SHADOW_DEDUP_KEY 를 실제 값으로 해석한다."""
    return (rec.get("symbol", ""), rec.get("signal_type", ""), rec["_date"])


def count(log_dir: Path = SHADOW_LOG_DIR) -> dict:
    """독립표본 수와 그 산출 과정을 함께 돌려준다.

    산출 과정을 같이 내는 이유: 숫자만 보면 다음 사람이 또 "어떻게 센 거지"를 묻는다.
    """
    rows = _load_rows(log_dir)
    outcomes = {r.get("signal_ts"): r for r in rows if r.get("kind") == OUTCOME_KIND}

    # (1) 신호행  (2) 실제 추론  (3) 기준일 이후
    signals = [
        r
        for r in rows
        if r.get("kind") is None
        and r.get("ml_active") is True
        and r["_date"] >= SHADOW_SAMPLE_START
    ]
    # (4) outcome 라벨 존재
    labeled = [r for r in signals if r.get("signal_ts") in outcomes]

    # 중복 제거 — 시간순 첫 건만 남긴다 (라이브 log_decision 의 억제 의미와 동일)
    seen: set[tuple[str, str, str]] = set()
    sample: list[dict] = []
    for rec in sorted(labeled, key=lambda x: x.get("ts_utc", "")):
        key = _dedup_key(rec)
        if key in seen:
            continue
        seen.add(key)
        sample.append(rec)

    reached = sum(
        1 for r in sample if outcomes[r["signal_ts"]].get("reached_target") is True
    )
    return {
        "sample_n": len(sample),
        "target": SHADOW_SAMPLE_TARGET,
        "remaining": max(0, SHADOW_SAMPLE_TARGET - len(sample)),
        "base_rate_pct": round(100 * reached / len(sample), 1) if sample else None,
        "would_block_pct": (
            round(100 * sum(1 for r in sample if r.get("would_block")) / len(sample), 1)
            if sample
            else None
        ),
        "breakdown": {
            "rows_total": len(rows),
            "rows_outcome": len(outcomes),
            "signals_after_start": len(signals),
            "labeled": len(labeled),
            "dropped_unlabeled": len(signals) - len(labeled),
            "dropped_duplicate": len(labeled) - len(sample),
        },
        "dedup_key": list(SHADOW_DEDUP_KEY),
        "sample_start": SHADOW_SAMPLE_START,
        "duplicates": [
            f"{k[0]} {k[2]}"
            for k, v in Counter(_dedup_key(r) for r in labeled).items()
            if v > 1
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument(
        "--log-dir",
        type=Path,
        default=SHADOW_LOG_DIR,
        help="shadow 로그 위치 (기본: 서버 경로와 동일한 workspace/ml_shadow). "
             "서버에서 내려받은 사본을 셀 때 지정한다.",
    )
    args = ap.parse_args()

    res = count(args.log_dir)
    if res["breakdown"]["rows_total"] == 0:
        print(f"⚠ 로그가 없다: {args.log_dir}", file=sys.stderr)
        print("  서버에서 내려받아라:", file=sys.stderr)
        print("  scp -i $PEM -r ubuntu@13.124.82.122:"
              "/home/ubuntu/BitCoin_Trade/workspace/ml_shadow ./output/", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    b = res["breakdown"]
    print(f"ML shadow 독립표본  {res['sample_n']} / {res['target']}"
          f"  (잔여 {res['remaining']})")
    print(f"  기준일 {res['sample_start']} 이후 · 중복키 {tuple(res['dedup_key'])}")
    print()
    print("  산출 과정")
    print(f"    로그 전체 행           {b['rows_total']:>6}")
    print(f"    ├ outcome 라벨행       {b['rows_outcome']:>6}  (분모 아님)")
    print(f"    └ 기준일 이후 추론신호 {b['signals_after_start']:>6}")
    print(f"        ├ 미라벨 제외      {b['dropped_unlabeled']:>6}")
    print(f"        ├ 중복 제외        {b['dropped_duplicate']:>6}")
    print(f"        └ 표본             {res['sample_n']:>6}")
    print()
    print(f"  기저 도달률   {res['base_rate_pct']}%")
    print(f"  would_block   {res['would_block_pct']}%  (LIVE였다면 차단됐을 비율)")
    if res["duplicates"]:
        print()
        print(f"  ⚠ 중복 {len(res['duplicates'])}건: {', '.join(res['duplicates'])}")
        print("    원인: _seen_keys 가 프로세스 메모리라 재시작 시 소실 (lessons #7 재발)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
