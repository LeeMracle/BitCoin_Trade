# -*- coding: utf-8 -*-
"""배포 전 QA 검증 스크립트 — scripts/qa_validate.py

전략 전환 시 발생하는 반복 버그를 배포 전에 자동으로 탐지합니다.

검증 항목:
  1. 전략 로드 테스트
  2. 데이터 조회 테스트
  3. 신호 생성 테스트
  4. 레벨 계산 시뮬레이션
  5. state 파일 검증
  6. config 일관성 체크

사용법:
  python scripts/qa_validate.py

  # deploy 스크립트에 통합
  python scripts/qa_validate.py && bash scripts/deploy_to_aws.sh

FAIL이 1개라도 있으면 exit code 1을 반환합니다.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 프로젝트 루트를 sys.path에 추가 ───────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# UTF-8 강제 설정
os.environ.setdefault("PYTHONUTF8", "1")


# ── 결과 집계 ─────────────────────────────────────────────
_results: list[tuple[str, str, str]] = []  # (status, label, detail)


def _record(status: str, label: str, detail: str = "") -> None:
    """검증 결과를 기록하고 즉시 출력한다."""
    _results.append((status, label, detail))
    tag = f"[{status}]"
    line = f"{tag} {label}"
    if detail:
        line += f": {detail}"
    print(line)


def _pass(label: str, detail: str = "") -> None:
    _record("PASS", label, detail)


def _fail(label: str, detail: str = "") -> None:
    _record("FAIL", label, detail)


# ═══════════════════════════════════════════════════════════
# 검증 1: 전략 로드 테스트
# ═══════════════════════════════════════════════════════════

def check_strategy_load() -> tuple[object | None, str | None]:
    """config.py의 STRATEGY/STRATEGY_KWARGS로 get_strategy() 호출 성공 여부를 확인한다.

    Returns:
        (strategy_fn, strategy_name_detail) — 실패 시 (None, None)
    """
    label = "전략 로드"
    try:
        from services.execution.config import STRATEGY, STRATEGY_KWARGS
        from services.strategies import get_strategy, STRATEGY_REGISTRY

        if STRATEGY not in STRATEGY_REGISTRY:
            available = ", ".join(STRATEGY_REGISTRY.keys())
            _fail(label, f"'{STRATEGY}'는 레지스트리에 없음. 사용 가능: {available}")
            return None, None

        strategy_fn = get_strategy(STRATEGY, **STRATEGY_KWARGS)
        kwargs_repr = ", ".join(f"{k}={v}" for k, v in STRATEGY_KWARGS.items()) if STRATEGY_KWARGS else "기본값"
        _pass(label, f"{STRATEGY} ({kwargs_repr})")
        return strategy_fn, STRATEGY
    except Exception as exc:
        _fail(label, str(exc))
        return None, None


# ═══════════════════════════════════════════════════════════
# 검증 2: 데이터 조회 테스트
# ═══════════════════════════════════════════════════════════

async def check_data_fetch(strategy_name: str | None) -> tuple[object | None, int]:
    """BTC/KRW OHLCV 조회 가능 여부 및 최소 봉 수 충족 여부를 확인한다.

    Returns:
        (df, bar_count) — 실패 시 (None, 0)
    """
    label = "데이터 조회"
    try:
        from services.execution.config import DONCHIAN_PERIOD, STRATEGY_KWARGS
        from services.market_data.fetcher import fetch_ohlcv
        import pandas as pd

        # daytrading 전략은 4h, 그 외는 1d
        is_daytrading = (strategy_name == "daytrading")
        timeframe = "4h" if is_daytrading else "1d"

        # 필요 최소 봉 수 계산
        dc_period = STRATEGY_KWARGS.get("dc_period", DONCHIAN_PERIOD)
        trend_period = STRATEGY_KWARGS.get("trend_period", 50)
        min_bars = dc_period + trend_period + 5

        # 조회 기간: min_bars + 여유 20%
        if is_daytrading:
            # 4h봉: 하루 6봉 → 필요 일수 × 1.3 (여유)
            fetch_days = max(120, int(min_bars / 6 * 1.3) + 10)
        else:
            fetch_days = max(min_bars + 20, 100)

        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=fetch_days)

        raw = await fetch_ohlcv(
            "BTC/KRW",
            timeframe,
            start.strftime("%Y-%m-%dT00:00:00Z"),
            end.strftime("%Y-%m-%dT00:00:00Z"),
            use_cache=False,
        )

        df = pd.DataFrame(raw)
        actual_bars = len(df)

        if actual_bars < min_bars:
            _fail(label, f"BTC/KRW {timeframe}, {actual_bars}봉 조회 (최소 {min_bars}봉 필요)")
            return None, actual_bars

        _pass(label, f"BTC/KRW {timeframe}, {actual_bars}봉 (최소 {min_bars})")
        return df, actual_bars

    except Exception as exc:
        _fail(label, str(exc))
        return None, 0


# ═══════════════════════════════════════════════════════════
# 검증 3: 신호 생성 테스트
# ═══════════════════════════════════════════════════════════

def check_signal_generation(strategy_fn, df) -> bool:
    """strategy_fn(df)가 올바른 signal Series를 반환하는지 확인한다."""
    label = "신호 생성"
    try:
        import pandas as pd

        if strategy_fn is None or df is None:
            _fail(label, "전략 함수 또는 데이터 없음 (이전 단계 실패)")
            return False

        signals = strategy_fn(df)

        # 기본 유효성 검사
        if not isinstance(signals, pd.Series):
            _fail(label, f"반환 타입 오류: {type(signals).__name__} (pd.Series 필요)")
            return False

        if len(signals) != len(df):
            _fail(label, f"signal 길이 불일치: {len(signals)} != {len(df)}")
            return False

        unique_vals = set(signals.dropna().unique())
        if not unique_vals.issubset({0, 1}):
            _fail(label, f"signal 값 오류: {unique_vals} (0 또는 1만 허용)")
            return False

        last_signal = int(signals.iloc[-1])
        _pass(label, f"signal Series {len(signals)}개, 마지막={last_signal}")
        return True

    except Exception as exc:
        _fail(label, str(exc))
        return False


# ═══════════════════════════════════════════════════════════
# 검증 4: 레벨 계산 시뮬레이션
# ═══════════════════════════════════════════════════════════

async def check_level_simulation(strategy_name: str | None) -> bool:
    """realtime_monitor의 _DT_LOOKBACK_DAYS 로직을 재현하여
    BTC/KRW 기준 레벨 계산에 충분한 데이터가 확보되는지 확인한다."""
    label = "레벨 시뮬"
    try:
        from services.execution.config import (
            DONCHIAN_PERIOD, STRATEGY_KWARGS, MIN_LISTING_DAYS
        )
        from services.market_data.fetcher import fetch_ohlcv
        import pandas as pd

        is_daytrading = (strategy_name == "daytrading")
        timeframe = "4h" if is_daytrading else "1d"

        # realtime_monitor._DT_LOOKBACK_DAYS 로직과 동일하게 계산
        if is_daytrading:
            lookback_days = 120
        else:
            lookback_days = max(MIN_LISTING_DAYS + 10, DONCHIAN_PERIOD + 80)

        dc_period = STRATEGY_KWARGS.get("dc_period", DONCHIAN_PERIOD)
        trend_period = STRATEGY_KWARGS.get("trend_period", 50)
        min_bars = dc_period + trend_period + 5

        # 실제 조회 시뮬
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=lookback_days)

        raw = await fetch_ohlcv(
            "BTC/KRW",
            timeframe,
            start.strftime("%Y-%m-%dT00:00:00Z"),
            end.strftime("%Y-%m-%dT00:00:00Z"),
            use_cache=True,
        )
        df = pd.DataFrame(raw)
        actual_bars = len(df)

        if actual_bars < min_bars:
            _fail(
                label,
                f"lookback={lookback_days}일 → {actual_bars}봉 (min_bars={min_bars}) — 레벨 계산 불가",
            )
            return False

        _pass(label, f"lookback={lookback_days}일, min_bars={min_bars} → {actual_bars}봉 OK")
        return True

    except Exception as exc:
        _fail(label, str(exc))
        return False


# ═══════════════════════════════════════════════════════════
# 검증 5: state 파일 검증
# ═══════════════════════════════════════════════════════════

def check_state_file() -> bool:
    """workspace/multi_trading_state.json의 구조적 일관성을 검증한다.

    - strategy_start 필드 존재 여부
    - positions의 필수 필드 검사
    - 5연패 카운트가 현재 전략 거래(strategy_start 이후)만 집계하는지 확인
    """
    label = "state 검증"
    state_path = _PROJECT_ROOT / "workspace" / "multi_trading_state.json"

    # 파일 없음 → 경고 아닌 PASS (최초 배포 허용)
    if not state_path.exists():
        _pass(label, "state 파일 없음 (최초 배포 — 정상)")
        return True

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError as exc:
        _fail(label, f"JSON 파싱 오류: {exc}")
        return False
    except Exception as exc:
        _fail(label, f"파일 읽기 오류: {exc}")
        return False

    issues: list[str] = []

    # strategy_start 필드 존재 확인
    strategy_start = state.get("strategy_start")
    if strategy_start is None:
        issues.append("strategy_start 필드 없음 — 5연패 카운트가 전략 전환 이전 거래까지 포함될 수 있음")
    else:
        # 날짜 형식 검증
        try:
            datetime.strptime(strategy_start, "%Y-%m-%d")
        except ValueError:
            issues.append(f"strategy_start 형식 오류: '{strategy_start}' (YYYY-MM-DD 필요)")

    # positions 필수 필드 검사
    positions = state.get("positions", {})
    required_pos_fields = {"entry_date", "entry_price", "highest", "trail_stop"}
    for symbol, pos in positions.items():
        if not isinstance(pos, dict):
            issues.append(f"positions.{symbol}: dict가 아님")
            continue
        missing = required_pos_fields - set(pos.keys())
        if missing:
            issues.append(f"positions.{symbol}: 필수 필드 누락 {missing}")

    # 5연패 카운트 검증 — strategy_start 이후 거래만 카운트하는지 시뮬
    if strategy_start:
        closed = state.get("closed_trades", [])
        all_losses_tail = 0
        for t in reversed(closed):
            if t.get("return_pct", 0) <= 0:
                all_losses_tail += 1
            else:
                break

        current_trades = [t for t in closed if t.get("exit_date", "") >= strategy_start]
        current_losses_tail = 0
        for t in reversed(current_trades):
            if t.get("return_pct", 0) <= 0:
                current_losses_tail += 1
            else:
                break

        if all_losses_tail != current_losses_tail and all_losses_tail >= 5:
            # 이전 전략 기록은 보존하되, 현재 전략 기준으로 분리 처리됨 → 경고만
            print(f"  [WARN] 이전 전략 연속손실 {all_losses_tail}건 이력 존재 "
                  f"(현재 전략 기준 {current_losses_tail}건 — 정상 분리됨)")

    if issues:
        _fail(label, "; ".join(issues))
        return False

    detail_parts = []
    if strategy_start:
        detail_parts.append(f"strategy_start={strategy_start}")
    if positions:
        detail_parts.append(f"포지션 {len(positions)}개")
    else:
        detail_parts.append("포지션 없음")
    _pass(label, ", ".join(detail_parts) if detail_parts else "정상")
    return True


# ═══════════════════════════════════════════════════════════
# 검증 6: config 일관성 체크
# ═══════════════════════════════════════════════════════════

def check_config_consistency() -> bool:
    """config.py 설정값들의 상호 일관성을 검증한다.

    - STRATEGY가 유효한 레지스트리 키인지
    - STRATEGY_KWARGS의 키가 make 함수 파라미터와 일치하는지
    - DONCHIAN_PERIOD와 STRATEGY_KWARGS["dc_period"]의 정합성
    """
    label = "config 정합성"
    try:
        from services.execution.config import STRATEGY, STRATEGY_KWARGS, DONCHIAN_PERIOD
        from services.strategies import STRATEGY_REGISTRY

        issues: list[str] = []

        # STRATEGY 유효성
        if STRATEGY not in STRATEGY_REGISTRY:
            available = ", ".join(STRATEGY_REGISTRY.keys())
            issues.append(f"STRATEGY='{STRATEGY}' 레지스트리에 없음. 사용 가능: {available}")
            _fail(label, "; ".join(issues))
            return False

        # make 함수 파라미터 일치 여부
        make_fn = STRATEGY_REGISTRY[STRATEGY]
        sig = inspect.signature(make_fn)
        valid_params = set(sig.parameters.keys())

        invalid_kwargs = {k: v for k, v in STRATEGY_KWARGS.items() if k not in valid_params}
        if invalid_kwargs:
            issues.append(
                f"STRATEGY_KWARGS에 유효하지 않은 파라미터: {invalid_kwargs} "
                f"(허용: {valid_params})"
            )

        # DONCHIAN_PERIOD와 STRATEGY_KWARGS["dc_period"] 정합성
        if "dc_period" in valid_params:
            # make 함수가 dc_period 파라미터를 가짐
            kwarg_dc = STRATEGY_KWARGS.get("dc_period")
            if kwarg_dc is not None and kwarg_dc != DONCHIAN_PERIOD:
                issues.append(
                    f"DONCHIAN_PERIOD({DONCHIAN_PERIOD}) != STRATEGY_KWARGS.dc_period({kwarg_dc}) "
                    f"— realtime_monitor는 DONCHIAN_PERIOD를, strategy는 dc_period를 사용하므로 불일치"
                )

        if issues:
            _fail(label, "; ".join(issues))
            return False

        # 정상 요약
        detail_parts = [f"STRATEGY={STRATEGY}"]
        if STRATEGY_KWARGS:
            kwargs_repr = ", ".join(f"{k}={v}" for k, v in STRATEGY_KWARGS.items())
            detail_parts.append(f"STRATEGY_KWARGS={{{kwargs_repr}}}")
        if "dc_period" in valid_params:
            dc_period = STRATEGY_KWARGS.get("dc_period", DONCHIAN_PERIOD)
            detail_parts.append(f"DC기간={dc_period}")
        _pass(label, ", ".join(detail_parts))
        return True

    except Exception as exc:
        _fail(label, str(exc))
        return False


# ═══════════════════════════════════════════════════════════
# 메인 진입점
# ═══════════════════════════════════════════════════════════

async def _run_all() -> int:
    """모든 검증을 순서대로 실행하고, FAIL 개수를 반환한다."""
    now_utc = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[QA 검증] {now_utc}")
    print("─" * 45)

    # 1. 전략 로드
    strategy_fn, strategy_name = check_strategy_load()

    # 2. 데이터 조회
    df, bar_count = await check_data_fetch(strategy_name)

    # 3. 신호 생성
    check_signal_generation(strategy_fn, df)

    # 4. 레벨 계산 시뮬레이션
    await check_level_simulation(strategy_name)

    # 5. state 파일 검증
    check_state_file()

    # 6. config 일관성 체크
    check_config_consistency()

    # ── 결과 집계 ──
    print("─" * 45)
    pass_count = sum(1 for s, _, _ in _results if s == "PASS")
    fail_count = sum(1 for s, _, _ in _results if s == "FAIL")
    print(f"결과: {pass_count} PASS / {fail_count} FAIL")

    if fail_count > 0:
        print()
        print("[배포 차단] FAIL 항목을 수정한 후 재실행하세요.")

    return fail_count


def main() -> None:
    fail_count = asyncio.run(_run_all())
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
