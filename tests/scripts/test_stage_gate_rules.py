"""ADR 20261112-1 단계적 재활성 룰 — 실제 저장소에서 깨끗하고, 위반을 잡는다 (lessons #44 역방향).

재현: `py -m pytest tests/scripts/test_stage_gate_rules.py -q`
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_pdc():
    spec = importlib.util.spec_from_file_location("pdc_stage_gate", ROOT / "scripts" / "pre_deploy_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


CFG = "services/execution/config.py"
RM = "services/execution/realtime_monitor.py"
MT = "services/execution/multi_trader.py"
SC = "services/execution/scanner.py"


def _sources() -> dict[str, str]:
    return {rel: _src(rel) for rel in (CFG, RM, MT, SC)}


# ── 1. 화이트리스트 전수 ─────────────────────────────────────────

def test_whitelist_rule_clean_on_repo():
    pdc = _load_pdc()
    assert pdc.check_symbol_whitelist_all_entry_paths(_sources()) == []


def test_whitelist_rule_catches_missing_guard_in_execute_buy():
    pdc = _load_pdc()
    s = _sources()
    s[RM] = s[RM].replace("if SYMBOL_WHITELIST and symbol not in SYMBOL_WHITELIST:\n            record_block(\"whitelist\", symbol)",
                          "if False:\n            record_block(\"whitelist\", symbol)")
    assert any("_execute_buy" in e for e in pdc.check_symbol_whitelist_all_entry_paths(s))


def test_whitelist_rule_catches_missing_guard_in_scanner_loop():
    pdc = _load_pdc()
    s = _sources()
    s[MT] = s[MT].replace("if SYMBOL_WHITELIST and symbol not in SYMBOL_WHITELIST:", "if False:")
    assert any("multi_trader" in e for e in pdc.check_symbol_whitelist_all_entry_paths(s))


def test_whitelist_rule_catches_universe_not_filtered():
    pdc = _load_pdc()
    s = _sources()
    s[SC] = s[SC].replace("if SYMBOL_WHITELIST:\n        _wl = set(SYMBOL_WHITELIST)", "if False:\n        _wl = set()")
    assert any("get_krw_market_coins" in e for e in pdc.check_symbol_whitelist_all_entry_paths(s))


def test_whitelist_rule_catches_missing_shadow_writer():
    pdc = _load_pdc()
    s = _sources()
    s[RM] = s[RM].replace("self._write_shadow_levels(now)", "pass")
    assert any("_write_shadow_levels" in e for e in pdc.check_symbol_whitelist_all_entry_paths(s))


def test_whitelist_rule_catches_constant_removed():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace('SYMBOL_WHITELIST = ["BTC/KRW", "ETH/KRW"]', "# removed")
    assert any("SYMBOL_WHITELIST" in e and "config" in e for e in pdc.check_symbol_whitelist_all_entry_paths(s))


# ── 2. 비중 × 슬롯 예산 ─────────────────────────────────────────

def test_budget_rule_clean_on_repo():
    pdc = _load_pdc()
    assert pdc.check_weight_slots_budget(_sources()) == []


def test_budget_rule_catches_overcommit():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace("MAX_POSITION_WEIGHT = 0.475", "MAX_POSITION_WEIGHT = 0.60")
    assert any("POSITION_RATIO" in e for e in pdc.check_weight_slots_budget(s))


def test_budget_rule_catches_slots_not_equal_whitelist():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace("MAX_POSITIONS = 2\n", "MAX_POSITIONS = 3\n")
    assert any("화이트리스트" in e for e in pdc.check_weight_slots_budget(s))


# ── 3. 단계 게이트 ───────────────────────────────────────────────

def test_stage_gate_clean_on_repo():
    pdc = _load_pdc()
    assert pdc.check_micro_order_stage_gate(_sources()) == []


def test_stage_gate_blocks_full_size_without_stage2_approval():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace("BUY_DISABLED = True\n", "BUY_DISABLED = False\n")
    # MICRO_ORDER_MODE=False 그대로 → 단계 2 = 승인 전
    assert any("단계 2" in e for e in pdc.check_micro_order_stage_gate(s))


def test_stage_gate_allows_stage1():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace("BUY_DISABLED = True\n", "BUY_DISABLED = False\n") \
                   .replace("MICRO_ORDER_MODE = False", "MICRO_ORDER_MODE = True")
    assert pdc.check_micro_order_stage_gate(s) == []


def test_stage_gate_catches_micro_amount_below_tp_floor():
    pdc = _load_pdc()
    s = _sources()
    s[CFG] = s[CFG].replace("MICRO_ORDER_KRW = 12_000", "MICRO_ORDER_KRW = 9_000")
    assert any("MICRO_ORDER_KRW" in e for e in pdc.check_micro_order_stage_gate(s))


def test_stage_gate_catches_micro_cap_missing_in_buy_path():
    pdc = _load_pdc()
    s = _sources()
    s[RM] = s[RM].replace("if MICRO_ORDER_MODE and order_amount > MICRO_ORDER_KRW:", "if False:")
    assert any("_execute_buy" in e for e in pdc.check_micro_order_stage_gate(s))


# ── 4. 레짐 EMA 일봉 창 깊이 (lessons #59) ───────────────────────

def test_ema_depth_clean_on_repo():
    pdc = _load_pdc()
    assert pdc.check_btc_ema_history_depth(_sources()) == []


def test_ema_depth_catches_direct_fetch_ohlcv():
    pdc = _load_pdc()
    s = _sources()
    s[SC] = s[SC].replace("candles = _fetch_btc_daily(exchange, _BTC_EMA_FETCH_LIMIT)\n            # 전체 창",
                          "candles = exchange.fetch_ohlcv('BTC/KRW', '1d', limit=_BTC_EMA_FETCH_LIMIT)\n            # 전체 창")
    assert any("fetch_btc_above_ema" in e and "직접 호출" in e for e in pdc.check_btc_ema_history_depth(s))


def test_ema_depth_catches_short_limit():
    pdc = _load_pdc()
    s = _sources()
    s[SC] = s[SC].replace("_BTC_EMA_FETCH_LIMIT = REGIME_FILTER_EMA_PERIOD * 3", "_BTC_EMA_FETCH_LIMIT = 200")
    assert any("_BTC_EMA_FETCH_LIMIT=200" in e for e in pdc.check_btc_ema_history_depth(s))


def test_ema_depth_catches_weak_length_guard():
    pdc = _load_pdc()
    s = _sources()
    s[SC] = s[SC].replace("if len(candles) < _BTC_EMA_FETCH_LIMIT:\n                last_err",
                          "if len(candles) < REGIME_FILTER_EMA_PERIOD:\n                last_err")
    assert any("fetch_btc_above_ema" in e and "가드 없음" in e for e in pdc.check_btc_ema_history_depth(s))


def test_ema_depth_catches_missing_pagination_loop():
    pdc = _load_pdc()
    s = _sources()
    s[SC] = s[SC].replace("    while len(out) < n:\n        params = {}", "    if True:\n        params = {}")
    assert any("while" in e for e in pdc.check_btc_ema_history_depth(s))
