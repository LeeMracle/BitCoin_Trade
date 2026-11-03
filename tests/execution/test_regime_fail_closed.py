"""F1 회귀 — 레짐 필터는 fail-closed (research/20261030_1 §4, lessons #21).

BTC 일봉 조회가 실패하거나 데이터가 모자라면 BULL(True)로 넘어가지 않고 False 를 반환해야 한다.
과거 구현은 예외를 삼키고 True 를 반환해, 조회 실패한 날 BEAR 에서도 매수가 열렸다.
"""
from __future__ import annotations

import pytest

from services.execution import scanner


class _Ex:
    def __init__(self, candles=None, exc=None):
        self._candles, self._exc = candles, exc
        self.calls = 0

    def fetch_ohlcv(self, *a, **k):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._candles


def _patch(monkeypatch, ex):
    monkeypatch.setattr(scanner, "REGIME_FILTER_ENABLED", True)
    monkeypatch.setattr(scanner.ccxt, "upbit", lambda *a, **k: ex)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)


def _candles(closes):
    return [[i, c, c, c, c, 1.0] for i, c in enumerate(closes)]


def test_exception_is_fail_closed_after_retries(monkeypatch):
    ex = _Ex(exc=RuntimeError("network down"))
    _patch(monkeypatch, ex)
    assert scanner.fetch_btc_above_ema(retries=3) is False
    assert ex.calls == 3


def test_insufficient_data_is_fail_closed(monkeypatch):
    ex = _Ex(candles=_candles([100.0] * 10))
    _patch(monkeypatch, ex)
    assert scanner.fetch_btc_above_ema(retries=2) is False


@pytest.mark.parametrize("last, expected", [(200.0, True), (50.0, False)])
def test_normal_path_unchanged(monkeypatch, last, expected):
    n = scanner.REGIME_FILTER_EMA_PERIOD + 50
    ex = _Ex(candles=_candles([100.0] * (n - 1) + [last]))
    _patch(monkeypatch, ex)
    assert scanner.fetch_btc_above_ema() is expected
    assert ex.calls == 1


def test_recovers_on_retry(monkeypatch):
    n = scanner.REGIME_FILTER_EMA_PERIOD + 50
    good = _candles([100.0] * (n - 1) + [200.0])

    class _Flaky(_Ex):
        def fetch_ohlcv(self, *a, **k):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("blip")
            return good

    ex = _Flaky()
    _patch(monkeypatch, ex)
    assert scanner.fetch_btc_above_ema(retries=3) is True
    assert ex.calls == 2


def test_disabled_filter_still_true(monkeypatch):
    monkeypatch.setattr(scanner, "REGIME_FILTER_ENABLED", False)
    assert scanner.fetch_btc_above_ema() is True


def test_monitor_default_flag_is_closed():
    """첫 레벨 갱신 전 기본값도 매수 차단이어야 한다."""
    import ast
    from pathlib import Path
    # import 하면 ML 의존성(lightgbm 등)을 끌어온다 — 소스만 파싱한다
    src = Path(scanner.__file__).with_name("realtime_monitor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    vals = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute)
            and n.target.attr == "_btc_above_ema"]
    assert vals and all(isinstance(v, ast.Constant) and v.value is False for v in vals)
