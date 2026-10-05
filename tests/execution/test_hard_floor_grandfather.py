"""하드 손절 -6% 신규 진입분만 적용 — 소급 청산 방지 (ADR 20260930-1, 2026-10-05 독립 리뷰).

세 층을 검증한다:
  1) `hard_stop` 의 계산 — 실제 함수를 호출한다(복제 금지, lessons #49)
  2) `pre_deploy_check.check_hard_floor_grandfathered` 가 **실제 저장소 파일**에서 깨끗하다
  3) 같은 룰이 **위반을 잡는다**(역방향, lessons #44) — 변이 소스로 별칭·속성·import alias·
     줄바꿈·`.get()` 우회, 호출 누락, 필드 누락, LEGACY 변경을 모두 시험한다

재현: `py -m pytest tests/execution/test_hard_floor_grandfather.py -q`
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.execution.config import (  # noqa: E402
    HARD_STOP_LOSS_PCT, LEGACY_HARD_STOP_LOSS_PCT,
)
from services.execution.hard_stop import pos_hard_cap, pos_hard_floor  # noqa: E402


def _load_pdc():
    spec = importlib.util.spec_from_file_location("pdc_under_test", ROOT / "scripts" / "pre_deploy_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PDC = _load_pdc()
RM = "services/execution/realtime_monitor.py"
CFG = "services/execution/config.py"


# ─────────────────────────── 1) 계산 ───────────────────────────

def test_config_values_are_what_the_adr_says():
    assert HARD_STOP_LOSS_PCT == 0.06
    assert LEGACY_HARD_STOP_LOSS_PCT == 0.10


def test_legacy_position_keeps_10pct_floor():
    pos = {"entry_price": 100.0, "trail_stop": 90.0}          # 배포 이전 진입 — 필드 없음
    assert pos_hard_floor(pos) == pytest.approx(90.0)
    # 레벨 갱신 식: max(merged, floor) — 기존 손절선이 움직이지 않는다
    assert max(90.0, pos_hard_floor(pos)) == pytest.approx(90.0)


def test_new_position_uses_its_own_recorded_cap():
    pos = {"entry_price": 100.0, "hard_stop_pct": HARD_STOP_LOSS_PCT}
    assert pos_hard_floor(pos) == pytest.approx(94.0)


def test_recorded_cap_is_immune_to_later_config_change(monkeypatch):
    """config 를 나중에 0.03 으로 바꿔도 이미 기록된 포지션은 움직이지 않는다."""
    pos = {"entry_price": 100.0, "hard_stop_pct": 0.06}
    from services.execution import config
    monkeypatch.setattr(config, "HARD_STOP_LOSS_PCT", 0.03)
    assert pos_hard_floor(pos) == pytest.approx(94.0)


@pytest.mark.parametrize("bad", [None, 0, 0.0, -0.05, 1, 1.5, "abc", "", [], {}, math.nan, True])
def test_bad_cap_falls_back_to_legacy_never_to_narrower(bad):
    """0 이면 바닥=진입가(즉시 청산) — 비정상 값은 어떤 경우에도 좁게 해석하지 않는다."""
    pos = {"entry_price": 100.0, "hard_stop_pct": bad}
    assert pos_hard_cap(pos) == LEGACY_HARD_STOP_LOSS_PCT
    assert pos_hard_floor(pos) == pytest.approx(90.0)


def test_numeric_string_cap_is_accepted_as_number():
    assert pos_hard_cap({"hard_stop_pct": "0.06"}) == pytest.approx(0.06)


def test_entry_price_override_for_fill_correction_uses_position_cap():
    """체결가 보정(fix_entry_price_from_fills) — 바꾸는 건 기준가뿐, 캡은 포지션 것."""
    legacy = {"entry_price": 100.0}
    new = {"entry_price": 100.0, "hard_stop_pct": 0.06}
    assert pos_hard_floor(legacy, 110.0) == pytest.approx(99.0)   # 0.90
    assert pos_hard_floor(new, 110.0) == pytest.approx(103.4)     # 0.94


def test_todays_four_positions_would_not_be_liquidated():
    """2026-10-05 실제 상황: 4종목이 새 캡 아래 — 기존 보호가 유지돼야 한다."""
    for name, ret in (("BSV", -.088), ("QTUM", -.078), ("CKB", -.073), ("ONDO", -.067)):
        pos = {"entry_price": 100.0, "trail_stop": 90.0}
        price = 100.0 * (1 + ret)
        new_stop = max(pos["trail_stop"], pos_hard_floor(pos))
        assert price > new_stop, f"{name}: 소급 청산 발생"


# ─────────────────────────── 2) 실제 저장소 ───────────────────────────

def _real_sources() -> dict[str, str]:
    files = PDC._hard_floor_scan_files() + [CFG]
    return {f: (ROOT / f).read_text(encoding="utf-8") for f in files}


def test_scan_scope_covers_live_modules_and_manual_tools():
    scope = set(PDC._hard_floor_scan_files())
    for must in (RM, "scripts/fix_entry_price_from_fills.py", "scripts/fix_state_balance_mismatch.py",
                 "scripts/manual_close_position.py"):
        assert must in scope, f"스캔 범위 누락: {must}"
    assert "scripts/backtest_cross_check_ev.py" not in scope      # 백테스트는 새 진입 모사 — 제외가 맞다


def test_real_repository_is_clean():
    assert PDC.check_hard_floor_grandfathered(_real_sources()) == []


def test_default_call_reads_disk_and_is_clean():
    before = len(PDC.errors)
    assert PDC.check_hard_floor_grandfathered() == []
    assert len(PDC.errors) == before


def test_fix_scripts_no_longer_touch_the_current_cap():
    for f in ("scripts/fix_entry_price_from_fills.py", "scripts/fix_state_balance_mismatch.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "HARD_STOP_LOSS_PCT" not in src.replace("LEGACY_HARD_STOP_LOSS_PCT", ""), f
    assert "pos_hard_floor" in (ROOT / "scripts/fix_entry_price_from_fills.py").read_text(encoding="utf-8")
    st = (ROOT / "scripts/fix_state_balance_mismatch.py").read_text(encoding="utf-8")
    assert '"hard_stop_pct": LEGACY_HARD_STOP_LOSS_PCT' in st
    assert "highest * (1 - LEGACY_HARD_STOP_LOSS_PCT)" in st      # stop 과 바닥이 같은 캡


# ─────────────────────────── 3) 역방향: 룰이 위반을 잡는가 ───────────────────────────

def _mut(**edits):
    """실제 소스에 변이를 가한 sources. edits: {경로: (찾을 문자열, 바꿀 문자열) | 새 파일 전체 소스}"""
    src = _real_sources()
    for path, e in edits.items():
        if isinstance(e, tuple):
            old, new = e
            assert old in src[path], f"변이 앵커 없음: {path}: {old[:40]!r}"
            src[path] = src[path].replace(old, new, 1)
        else:
            src[path] = e
    return src


def _flags(src):
    return PDC.check_hard_floor_grandfathered(src)


def test_T01_direct_multiply_in_refresh_loop_is_caught():
    s = _mut(**{RM: ("hard_floor = pos_hard_floor(pos)\n            if IS_DAYTRADING:",
                     'hard_floor = pos["entry_price"] * (1 - HARD_STOP_LOSS_PCT)\n            if IS_DAYTRADING:')})
    assert any("HARD_STOP_LOSS_PCT" in f and "참조" in f for f in _flags(s))


def test_T02_alias_through_variable_is_caught():
    s = _mut(**{"scripts/fix_x.py":
                "from services.execution.config import HARD_STOP_LOSS_PCT\n"
                "def f(pos):\n    cap = HARD_STOP_LOSS_PCT\n    e = pos['entry_price']\n    return e * (1 - cap)\n"})
    fl = _flags(s)
    assert any("fix_x.py" in f for f in fl) and len(fl) >= 2          # import + 참조


def test_T03_import_alias_is_caught_even_in_allowed_file():
    s = _mut(**{RM: ("    HARD_STOP_LOSS_PCT, MAX_ATR_PCT,\n", "    HARD_STOP_LOSS_PCT as _CAP, MAX_ATR_PCT,\n")})
    assert any("import" in f for f in _flags(s))


def test_T04_module_attribute_form_is_caught():
    s = _mut(**{"scripts/manual_x.py":
                "from services.execution import config\n"
                "def f(pos):\n    return pos.get('entry_price') * (1 - config.HARD_STOP_LOSS_PCT)\n"})
    assert any("속성 참조" in f for f in _flags(s))


def test_T05_multiline_expression_is_caught():
    s = _mut(**{"scripts/fix_y.py":
                "from services.execution.config import HARD_STOP_LOSS_PCT\n"
                "def f(pos):\n    return (\n        pos['entry_price']\n        *\n        (1\n         -\n"
                "         HARD_STOP_LOSS_PCT)\n    )\n"})
    assert any("fix_y.py" in f for f in _flags(s))


def test_T06_old_fix_entry_price_code_is_caught():
    """리뷰가 적발한 원본 결함을 되돌리면 룰이 잡아야 한다."""
    s = _mut(**{"scripts/fix_entry_price_from_fills.py":
                ("hard_floor = pos_hard_floor(pos, real_price)", "hard_floor = real_price * (1 - HARD_STOP_LOSS_PCT)")})
    assert any("fix_entry_price_from_fills.py" in f for f in _flags(s))


def test_T07_reference_in_other_function_of_allowed_file_is_caught():
    s = _mut(**{RM: ("hard_floor = pos_hard_floor(pos)\n            if IS_DAYTRADING:",
                     "hard_floor = pos_hard_floor(pos) * 0 + HARD_STOP_LOSS_PCT\n            if IS_DAYTRADING:")})
    assert any("HARD_STOP_LOSS_PCT" in f for f in _flags(s))


def test_T08_missing_floor_calls_are_caught():
    s = _mut(**{RM: ("hard_floor = pos_hard_floor(pos)\n                if IS_DAYTRADING:",
                     "hard_floor = 0\n                if IS_DAYTRADING:")})
    assert any("호출" in f and "<2" in f for f in _flags(s))


def test_T09_missing_recorded_field_is_caught():
    s = _mut(**{RM: ('"hard_stop_pct": HARD_STOP_LOSS_PCT,', "")})
    assert any("hard_stop_pct" in f and "기록 없음" in f for f in _flags(s))


def test_T10_recorded_field_with_wrong_value_is_caught():
    s = _mut(**{RM: ('"hard_stop_pct": HARD_STOP_LOSS_PCT,', '"hard_stop_pct": 0.5,')})
    assert any("기록 없음" in f for f in _flags(s))


def test_T11_legacy_value_change_is_caught():
    s = _mut(**{CFG: ("LEGACY_HARD_STOP_LOSS_PCT = 0.10", "LEGACY_HARD_STOP_LOSS_PCT = 0.06")})
    assert any("LEGACY" in f for f in _flags(s))


def test_T12_syntax_error_is_reported_not_swallowed():
    s = _mut(**{"scripts/fix_z.py": "def broken(:\n"})
    assert any("파싱 실패" in f for f in _flags(s))


def test_T13_harmless_changes_pass():
    """주석·docstring·문자열에 이름이 있는 것, 무관한 새 파일은 위반이 아니다(오탐 방지)."""
    s = _mut(**{
        RM: ("def is_benign_ws_error(", '# HARD_STOP_LOSS_PCT 설명 주석\n"""HARD_STOP_LOSS_PCT docstring"""\n\ndef is_benign_ws_error('),
        "scripts/fix_ok.py": "def f(pos):\n    from services.execution.hard_stop import pos_hard_floor\n"
                             "    return pos_hard_floor(pos)\n    # HARD_STOP_LOSS_PCT\n",
    })
    assert _flags(s) == []


def test_T14_backtest_scripts_may_use_the_current_cap():
    """백테스트는 '새 진입'을 모사하므로 현행 캡 참조가 맞다 — 스캔 대상이 아니므로 통과."""
    assert "scripts/backtest_x.py" not in PDC._hard_floor_scan_files()
