"""후보 선택 규칙 연구 (plan 20261005_5) — 연구 코드의 정직성을 검증한다.

1) 특성에 look-ahead 가 없다: d 일 이후 데이터를 바꿔도 d 일의 특성은 불변
2) 순위/필터 함수의 의미 (결측은 맨 뒤, 동률은 무작위, 필터는 임계 미만 제외)
3) simulate 의 rank_fn 훅: 기본(None)은 기존과 같은 무작위, 지정하면 그 순서로 진입.
   기존 결과 불변(회귀)은 훅 추가 시점에 20건 비트 단위로 확인했다(plan §1).

재현: py -m pytest tests/scripts/test_study_candidate_ranking.py -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import study_candidate_ranking as S  # noqa: E402

FEATS = ("vol_ratio", "atrp", "ret30", "liq")


def _df(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    vol = rng.uniform(1_000, 5_000, n)
    return pd.DataFrame({"ts": np.arange(n) * 86_400_000, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


# ─────────────────────────── 1) look-ahead 없음 ───────────────────────────

@pytest.mark.parametrize("d", [40, 60, 100])
def test_features_at_day_d_ignore_day_d_and_later(d):
    base = _df()
    f0 = S.compute_features(base)
    mut = base.copy()
    for col in ("open", "high", "low", "close", "volume"):
        mut.loc[d:, col] = mut.loc[d:, col] * 7.3 + 1000          # d 일 이후 전부 왜곡
    f1 = S.compute_features(mut)
    for k in FEATS:
        assert f0.loc[d, k] == pytest.approx(f1.loc[d, k], nan_ok=True), f"{k}@{d}: d 일 데이터가 특성에 샜다"


@pytest.mark.parametrize("d", [40, 100])
def test_features_at_next_day_do_use_day_d(d):
    """대조: d+1 일 특성은 d 일 데이터를 쓴다 — 위 테스트가 '아무것도 안 바뀌어서' 통과한 게 아님을 증명."""
    base = _df()
    mut = base.copy()
    for col in ("open", "high", "low", "close", "volume"):
        mut.loc[d, col] = mut.loc[d, col] * 7.3 + 1000
    f0, f1 = S.compute_features(base), S.compute_features(mut)
    assert all(not np.isclose(f0.loc[d + 1, k], f1.loc[d + 1, k]) for k in FEATS)


def test_feature_definitions_match_plan():
    df = _df()
    f = S.compute_features(df)
    d = 80
    assert f.loc[d, "vol_ratio"] == pytest.approx(df.loc[d - 1, "volume"] / df.loc[d - 5:d - 1, "volume"].mean())
    assert f.loc[d, "ret30"] == pytest.approx(df.loc[d - 1, "close"] / df.loc[d - 31, "close"] - 1)
    assert f.loc[d, "liq"] == pytest.approx((df.loc[d - 5:d - 1, "volume"] * df.loc[d - 5:d - 1, "close"]).mean())


# ─────────────────────────── 2) 순위·필터 ───────────────────────────

def _c(**f):
    base = {k: 1.0 for k in FEATS}
    base.update(f)
    return {"f": base, "id": f.get("id", 0)}


def test_rank_orders_by_direction_and_puts_nan_last():
    cands = [_c(vol_ratio=2.0), _c(vol_ratio=5.0), _c(vol_ratio=float("nan")), _c(vol_ratio=3.0)]
    out = S.make_rank("vol_ratio", +1)(cands, np.random.default_rng(0))
    assert [c["f"]["vol_ratio"] for c in out[:3]] == [5.0, 3.0, 2.0]
    assert math.isnan(out[3]["f"]["vol_ratio"])
    low_first = S.make_rank("atrp", -1)([_c(atrp=0.05), _c(atrp=0.01), _c(atrp=0.03)], np.random.default_rng(0))
    assert [c["f"]["atrp"] for c in low_first] == [0.01, 0.03, 0.05]


def test_rank_ties_are_random_but_reproducible():
    cands = [_c(vol_ratio=1.0, id=i) for i in range(8)]
    a = [c["id"] for c in S.make_rank("vol_ratio", +1)(cands, np.random.default_rng(1))]
    b = [c["id"] for c in S.make_rank("vol_ratio", +1)(cands, np.random.default_rng(1))]
    c = [c["id"] for c in S.make_rank("vol_ratio", +1)(cands, np.random.default_rng(2))]
    assert a == b and a != c and sorted(a) == list(range(8))


def test_filter_drops_below_threshold_and_keeps_direction_sign():
    cands = [_c(atrp=0.01), _c(atrp=0.04), _c(atrp=0.02)]
    # R2(방향 -1): key = -atrp. 임계 -0.025 → atrp ≤ 0.025 만 통과
    out = S.make_filter("atrp", -1, -0.025)(cands, np.random.default_rng(0))
    assert sorted(c["f"]["atrp"] for c in out) == [0.01, 0.02]
    assert S.make_filter("atrp", -1, -0.025)([_c(atrp=float("nan"))], np.random.default_rng(0)) == []


# ─────────────────────────── 3) simulate 훅 ───────────────────────────

def _ctx():
    n, T = 3, 72
    O = np.full((n, T), 100.0)
    H = np.full((n, T), 101.0)
    L = np.full((n, T), 99.0)
    C = np.full((n, T), 100.0)
    L[:, 10:] = 50.0                                    # 10시에 폭락 → 손절로 거래 종료(atrp 가 기록됨)
    sigs = {0: [{"si": i, "t": 5, "entry_px": 100.0, "atr": a} for i, a in enumerate((1.0, 2.0, 3.0))]}
    return {"O": O, "H": H, "L": L, "C": C, "syms": ["A/KRW", "B/KRW", "C/KRW"], "sigs": sigs}


def _run(rank_fn, seed=0):
    import backtest_stop_validation as SV
    from backtest_param_sweep import P
    return SV.simulate(_ctx(), [0, 24, 48], P(slots=1), 0.06, False, seed, rank_fn=rank_fn)


def test_hook_hands_the_first_slot_to_the_top_ranked_candidate():
    hi_atr_first = _run(lambda c, rng: sorted(c, key=lambda s: -s["atr"]))
    lo_atr_first = _run(lambda c, rng: sorted(c, key=lambda s: s["atr"]))
    assert [round(t["atrp"], 2) for t in hi_atr_first["trades"]] == [0.03]
    assert [round(t["atrp"], 2) for t in lo_atr_first["trades"]] == [0.01]


def test_default_is_random_shuffle_and_deterministic_per_seed():
    a, b = _run(None, seed=3), _run(None, seed=3)
    assert a["trades"] == b["trades"] and a["ret"] == b["ret"]
    seen = {round(_run(None, seed=s)["trades"][0]["atrp"], 2) for s in range(30)}
    assert seen == {0.01, 0.02, 0.03}                    # 기본은 셋 모두 나올 수 있는 무작위


def test_candidate_and_skip_counters():
    r = _run(None)
    assert r["n_cand"] == 3 and r["n_skip"] == 2         # 슬롯 1개 → 1개 진입, 2개 탈락
