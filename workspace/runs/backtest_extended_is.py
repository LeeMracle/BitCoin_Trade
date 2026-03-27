"""
Phase 2 확장 검증 — F&G 전체 히스토리 활용
==========================================
목적:
  1. IS 기간 확장 (2020-01 ~ 2025-03) — F&G 2018년부터 가용
  2. 쿨다운 전략 재검증 (buy≤20, sell≥50, SL=-8%, CD=120)
  3. 레짐 인식 필터 추가 (ATH 대비 -30% 이상 = 약세장 → 진입 금지)
  4. Phase 3 기준 재평가 (Sharpe ≥ 0.5로 완화 검토)

이전 보고서: workspace/.simulation/20260327_04_종합전략최적화최종보고.md
"""
import sys, asyncio, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv, fetch_fear_greed

# ── 기간 설정 ──────────────────────────────────────────────
# 확장 IS: 2020-01-01 ~ 2025-03-01 (F&G 2018년부터 가용, OHLCV는 업비트 상장 이후)
# MA200 워밍업 위해 2019-06-01부터 OHLCV fetch
WARMUP_START = "2019-06-01T00:00:00Z"
IS_START     = "2020-01-01T00:00:00Z"
IS_END       = "2025-03-01T00:00:00Z"
OOS_START    = "2025-03-01T00:00:00Z"
OOS_END      = "2026-03-01T00:00:00Z"

# Phase 3 기준
PHASE3_SHARPE_STRICT = 0.8
PHASE3_SHARPE_RELAXED = 0.5  # 거래횟수 10+ 시 적용 검토


# ── 유틸 ───────────────────────────────────────────────────
def make_fg_map(fg_raw: list[dict]) -> dict[str, float]:
    return {r["date"]: r["value"] for r in fg_raw}


def ts_to_date(ts_ms: int) -> str:
    return pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")


# ── 전략 1: 쿨다운 전략 (최종 보고 최고 성과) ─────────────
def make_strategy_cooldown(fg_map, buy_thr=20, sell_thr=50, sl_pct=-0.08, cooldown_days=120):
    """F&G 역추세 + 손절 + 쿨다운."""
    def strategy(df):
        signal = pd.Series(0, index=df.index)
        pos = 0
        entry_price = 0.0
        cooldown_until = ""  # YYYY-MM-DD

        for i in range(len(df)):
            date_str = ts_to_date(int(df["ts"].iloc[i]))
            fg = fg_map.get(date_str, np.nan)
            close = df["close"].iloc[i]

            # 쿨다운 중이면 진입 금지
            if date_str < cooldown_until:
                signal.iloc[i] = 0
                continue

            if pos == 0:
                if not np.isnan(fg) and fg <= buy_thr:
                    pos = 1
                    entry_price = close
            elif pos == 1:
                # 손절 확인
                ret = (close - entry_price) / entry_price
                if ret <= sl_pct:
                    pos = 0
                    # 쿨다운 시작
                    from datetime import timedelta
                    cd_date = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=cooldown_days)
                    cooldown_until = cd_date.strftime("%Y-%m-%d")
                elif not np.isnan(fg) and fg >= sell_thr:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ── 전략 2: 레짐 인식 + 쿨다운 ────────────────────────────
def make_strategy_regime_cooldown(fg_map, buy_thr=20, sell_thr=50, sl_pct=-0.08,
                                   cooldown_days=120, regime_drawdown=-0.30):
    """F&G 역추세 + 손절 + 쿨다운 + 레짐 필터 (ATH 대비 하락률로 약세장 감지)."""
    def strategy(df):
        signal = pd.Series(0, index=df.index)
        pos = 0
        entry_price = 0.0
        cooldown_until = ""
        ath = 0.0  # All-Time High 추적

        for i in range(len(df)):
            date_str = ts_to_date(int(df["ts"].iloc[i]))
            fg = fg_map.get(date_str, np.nan)
            close = df["close"].iloc[i]

            # ATH 갱신
            if close > ath:
                ath = close

            # 레짐 판단: ATH 대비 하락률
            drawdown_from_ath = (close - ath) / ath if ath > 0 else 0

            # 쿨다운 중이면 진입 금지
            if date_str < cooldown_until:
                signal.iloc[i] = 0
                continue

            if pos == 0:
                # 약세장(ATH 대비 -30% 이상 하락)이면 진입 금지
                if drawdown_from_ath <= regime_drawdown:
                    signal.iloc[i] = 0
                    continue
                if not np.isnan(fg) and fg <= buy_thr:
                    pos = 1
                    entry_price = close
            elif pos == 1:
                ret = (close - entry_price) / entry_price
                if ret <= sl_pct:
                    pos = 0
                    from datetime import timedelta
                    cd_date = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=cooldown_days)
                    cooldown_until = cd_date.strftime("%Y-%m-%d")
                elif not np.isnan(fg) and fg >= sell_thr:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ── 전략 3: 순수 F&G (기준선) ──────────────────────────────
def make_strategy_fg_base(fg_map, buy_thr=20, sell_thr=50):
    """F&G 기본 전략 — 쿨다운/손절 없음."""
    def strategy(df):
        signal = pd.Series(0, index=df.index)
        pos = 0
        for i in range(len(df)):
            date_str = ts_to_date(int(df["ts"].iloc[i]))
            fg = fg_map.get(date_str, np.nan)
            if np.isnan(fg):
                signal.iloc[i] = pos
                continue
            if pos == 0 and fg <= buy_thr:
                pos = 1
            elif pos == 1 and fg >= sell_thr:
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


# ── 전략 4: 레짐 인식 + 쿨다운 + 파라미터 스윕 ─────────────
REGIME_PARAMS = [
    {"regime_drawdown": -0.25},
    {"regime_drawdown": -0.30},
    {"regime_drawdown": -0.35},
]

COOLDOWN_PARAMS = [
    {"cooldown_days": 60, "sl_pct": -0.08},
    {"cooldown_days": 90, "sl_pct": -0.08},
    {"cooldown_days": 120, "sl_pct": -0.08},
    {"cooldown_days": 120, "sl_pct": -0.10},
    {"cooldown_days": 150, "sl_pct": -0.08},
]


def run_param_sweep(engine, ohlcv_df, fg_map):
    """레짐 + 쿨다운 파라미터 조합 스윕."""
    buy_thresholds = [15, 20, 25]
    sell_thresholds = [45, 50, 55]
    rows = []

    for buy_thr in buy_thresholds:
        for sell_thr in sell_thresholds:
            if buy_thr >= sell_thr:
                continue
            for rp in REGIME_PARAMS:
                for cp in COOLDOWN_PARAMS:
                    strat = make_strategy_regime_cooldown(
                        fg_map, buy_thr=buy_thr, sell_thr=sell_thr,
                        sl_pct=cp["sl_pct"], cooldown_days=cp["cooldown_days"],
                        regime_drawdown=rp["regime_drawdown"],
                    )
                    r = engine.run(strat, ohlcv_df)
                    m = r.metrics
                    rows.append({
                        "buy_thr": buy_thr, "sell_thr": sell_thr,
                        "regime_dd": rp["regime_drawdown"],
                        "cd_days": cp["cooldown_days"], "sl_pct": cp["sl_pct"],
                        "total_return": m.total_return, "sharpe": m.sharpe,
                        "calmar": m.calmar, "max_dd": m.max_drawdown,
                        "n_trades": m.n_trades, "win_rate": m.win_rate,
                        "avg_trade": m.avg_trade_return, "run_id": r.run_id,
                    })

    return pd.DataFrame(rows).sort_values("sharpe", ascending=False)


def fmt_metrics(label, metrics, bh):
    m = metrics
    lines = [
        f"  {label}",
        f"    수익률: {m.total_return*100:+.1f}%  BnH: {bh*100:+.1f}%  초과: {(m.total_return-bh)*100:+.1f}%p",
        f"    Sharpe: {m.sharpe:.3f}  Calmar: {m.calmar:.3f}  MDD: {m.max_drawdown*100:.1f}%",
        f"    거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%  평균: {m.avg_trade_return*100:.2f}%",
    ]
    return "\n".join(lines)


async def main():
    print("=" * 70)
    print("Phase 2 확장 검증 — F&G 전체 히스토리 (2020~) + 레짐 필터")
    print("=" * 70)

    print("\n데이터 수집 중...")

    # 데이터 fetch (병렬)
    ohlcv_full_raw, fg_full_raw, ohlcv_oos_raw, fg_oos_raw = await asyncio.gather(
        fetch_ohlcv("BTC/KRW", "1d", WARMUP_START, OOS_END),
        fetch_fear_greed("2020-01-01", OOS_END[:10]),
        fetch_ohlcv("BTC/KRW", "1d", OOS_START, OOS_END),
        fetch_fear_greed(OOS_START[:10], OOS_END[:10]),
    )

    ohlcv_full = pd.DataFrame(ohlcv_full_raw)
    ohlcv_oos = pd.DataFrame(ohlcv_oos_raw)

    # IS 구간 필터 (워밍업 데이터 포함)
    is_start_ms = int(datetime.fromisoformat(IS_START.replace("Z", "+00:00")).timestamp() * 1000)
    is_end_ms = int(datetime.fromisoformat(IS_END.replace("Z", "+00:00")).timestamp() * 1000)
    warmup_start_ms = int(datetime.fromisoformat(WARMUP_START.replace("Z", "+00:00")).timestamp() * 1000)

    ohlcv_is_warmup = ohlcv_full[ohlcv_full["ts"] >= warmup_start_ms].copy()
    ohlcv_is_warmup = ohlcv_is_warmup[ohlcv_is_warmup["ts"] <= is_end_ms].copy().reset_index(drop=True)

    ohlcv_is = ohlcv_full[ohlcv_full["ts"] >= is_start_ms].copy()
    ohlcv_is = ohlcv_is[ohlcv_is["ts"] <= is_end_ms].copy().reset_index(drop=True)

    fg_map_full = make_fg_map(fg_full_raw)
    fg_map_oos = make_fg_map(fg_oos_raw)

    # IS F&G 범위 필터
    fg_map_is = {k: v for k, v in fg_map_full.items() if "2020-01-01" <= k <= "2025-03-01"}

    bh_is = (ohlcv_is["close"].iloc[-1] / ohlcv_is["close"].iloc[0]) - 1
    bh_oos = (ohlcv_oos["close"].iloc[-1] / ohlcv_oos["close"].iloc[0]) - 1

    print(f"  OHLCV (워밍업 포함): {len(ohlcv_is_warmup)}일 ({WARMUP_START[:10]} ~ {IS_END[:10]})")
    print(f"  OHLCV IS: {len(ohlcv_is)}일 ({IS_START[:10]} ~ {IS_END[:10]})")
    print(f"  OHLCV OOS: {len(ohlcv_oos)}일 ({OOS_START[:10]} ~ {OOS_END[:10]})")
    print(f"  F&G IS: {len(fg_map_is)}일")
    print(f"  F&G OOS: {len(fg_map_oos)}일")
    print(f"  BnH IS: {bh_is*100:+.1f}%  BnH OOS: {bh_oos*100:+.1f}%")

    engine = BacktestEngine()

    # ── A. 기준선: F&G 기본 (확장 IS) ──────────────────────
    print("\n" + "=" * 70)
    print("[A] 기준선 — F&G 기본 (buy≤20, sell≥50, 쿨다운/손절 없음)")
    print("=" * 70)

    strat_base = make_strategy_fg_base(fg_map_is, 20, 50)
    r_base_is = engine.run(strat_base, ohlcv_is)
    print(fmt_metrics("IS (2020-01 ~ 2025-03)", r_base_is.metrics, bh_is))

    strat_base_oos = make_strategy_fg_base(fg_map_oos, 20, 50)
    r_base_oos = engine.run(strat_base_oos, ohlcv_oos)
    print(fmt_metrics("OOS (2025-03 ~ 2026-03)", r_base_oos.metrics, bh_oos))

    # ── B. 쿨다운 전략 (확장 IS) ──────────────────────────
    print("\n" + "=" * 70)
    print("[B] 쿨다운 전략 (buy≤20, sell≥50, SL=-8%, CD=120)")
    print("=" * 70)

    strat_cd = make_strategy_cooldown(fg_map_is, 20, 50, -0.08, 120)
    r_cd_is = engine.run(strat_cd, ohlcv_is)
    print(fmt_metrics("IS (2020-01 ~ 2025-03)", r_cd_is.metrics, bh_is))

    strat_cd_oos = make_strategy_cooldown(fg_map_oos, 20, 50, -0.08, 120)
    r_cd_oos = engine.run(strat_cd_oos, ohlcv_oos)
    print(fmt_metrics("OOS (2025-03 ~ 2026-03)", r_cd_oos.metrics, bh_oos))

    # ── C. 레짐 + 쿨다운 (확장 IS) ────────────────────────
    print("\n" + "=" * 70)
    print("[C] 레짐 인식 + 쿨다운 (buy≤20, sell≥50, SL=-8%, CD=120, regime≤-30%)")
    print("=" * 70)

    strat_regime = make_strategy_regime_cooldown(fg_map_is, 20, 50, -0.08, 120, -0.30)
    r_regime_is = engine.run(strat_regime, ohlcv_is)
    print(fmt_metrics("IS (2020-01 ~ 2025-03)", r_regime_is.metrics, bh_is))

    strat_regime_oos = make_strategy_regime_cooldown(fg_map_oos, 20, 50, -0.08, 120, -0.30)
    r_regime_oos = engine.run(strat_regime_oos, ohlcv_oos)
    print(fmt_metrics("OOS (2025-03 ~ 2026-03)", r_regime_oos.metrics, bh_oos))

    # ── D. 파라미터 스윕 (레짐+쿨다운) ─────────────────────
    print("\n" + "=" * 70)
    print("[D] 레짐+쿨다운 파라미터 스윕 (확장 IS)")
    print("=" * 70)

    sweep_df = run_param_sweep(engine, ohlcv_is, fg_map_is)
    print(f"\n총 {len(sweep_df)}개 조합 테스트")
    cols = ["buy_thr", "sell_thr", "regime_dd", "cd_days", "sl_pct",
            "sharpe", "total_return", "max_dd", "n_trades", "win_rate"]
    print("\nTop 10 (Sharpe 기준):")
    print(sweep_df[cols].head(10).to_string(index=False))

    # 최적 파라미터로 OOS 검증
    if len(sweep_df) > 0:
        best = sweep_df.iloc[0]
        print(f"\n최적: buy≤{int(best['buy_thr'])}, sell≥{int(best['sell_thr'])}, "
              f"regime≤{best['regime_dd']:.0%}, CD={int(best['cd_days'])}일, SL={best['sl_pct']:.0%}")

        strat_best_oos = make_strategy_regime_cooldown(
            fg_map_oos,
            buy_thr=int(best["buy_thr"]), sell_thr=int(best["sell_thr"]),
            sl_pct=best["sl_pct"], cooldown_days=int(best["cd_days"]),
            regime_drawdown=best["regime_dd"],
        )
        r_best_oos = engine.run(strat_best_oos, ohlcv_oos)

        print("\n" + "=" * 70)
        print("[E] 최적 파라미터 OOS 검증")
        print("=" * 70)
        print(fmt_metrics(f"IS 최적 (Sharpe {best['sharpe']:.3f})", r_regime_is.metrics, bh_is))
        print(fmt_metrics("OOS 검증", r_best_oos.metrics, bh_oos))

    # ── 최종 비교 테이블 ──────────────────────────────────
    print("\n" + "=" * 70)
    print("최종 비교 (OOS 기준)")
    print("=" * 70)

    comparison = []
    for name, r in [
        ("Buy & Hold", None),
        ("F&G 기본 (쿨다운X)", r_base_oos),
        ("쿨다운 전략", r_cd_oos),
        ("레짐+쿨다운", r_regime_oos),
    ]:
        if r is None:
            comparison.append({
                "전략": name, "수익률": f"{bh_oos*100:+.1f}%",
                "Sharpe": "—", "MDD": "—", "거래": "—", "승률": "—"
            })
        else:
            m = r.metrics
            comparison.append({
                "전략": name,
                "수익률": f"{m.total_return*100:+.1f}%",
                "Sharpe": f"{m.sharpe:.3f}",
                "MDD": f"{m.max_drawdown*100:.1f}%",
                "거래": f"{m.n_trades}",
                "승률": f"{m.win_rate*100:.0f}%",
            })

    if len(sweep_df) > 0:
        m = r_best_oos.metrics
        comparison.append({
            "전략": f"최적 스윕",
            "수익률": f"{m.total_return*100:+.1f}%",
            "Sharpe": f"{m.sharpe:.3f}",
            "MDD": f"{m.max_drawdown*100:.1f}%",
            "거래": f"{m.n_trades}",
            "승률": f"{m.win_rate*100:.0f}%",
        })

    comp_df = pd.DataFrame(comparison)
    print(comp_df.to_string(index=False))

    # ── Phase 3 판단 ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("Phase 3 전환 판단")
    print("=" * 70)

    best_oos = r_best_oos if len(sweep_df) > 0 else r_regime_oos
    m = best_oos.metrics

    strict_pass = (m.sharpe >= PHASE3_SHARPE_STRICT and m.max_drawdown >= -0.20
                   and m.win_rate >= 0.5 and m.n_trades >= 2)
    relaxed_pass = (m.sharpe >= PHASE3_SHARPE_RELAXED and m.max_drawdown >= -0.20
                    and m.win_rate >= 0.5 and m.n_trades >= 2)

    print(f"  OOS Sharpe: {m.sharpe:.3f}  MDD: {m.max_drawdown*100:.1f}%  "
          f"거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%")
    print(f"  엄격 기준 (Sharpe≥0.8): {'PASS' if strict_pass else 'FAIL'}")
    print(f"  완화 기준 (Sharpe≥0.5): {'PASS' if relaxed_pass else 'FAIL'}")

    if strict_pass:
        print("\n  ★ GO — Phase 3 페이퍼 트레이딩 전환 권장")
    elif relaxed_pass:
        print("\n  ★ CONDITIONAL GO — 완화 기준 통과, 소액 페이퍼 트레이딩 권장")
    else:
        print("\n  ★ HOLD — Phase 3 전환 보류")
        reasons = []
        if m.sharpe < PHASE3_SHARPE_RELAXED:
            reasons.append(f"Sharpe {m.sharpe:.3f} < 0.5")
        if m.max_drawdown < -0.20:
            reasons.append(f"MDD {m.max_drawdown*100:.1f}% < -20%")
        if m.win_rate < 0.5:
            reasons.append(f"승률 {m.win_rate*100:.0f}% < 50%")
        if m.n_trades < 2:
            reasons.append(f"거래 {m.n_trades}회 < 2")
        if reasons:
            print(f"  미충족: {', '.join(reasons)}")

    print("=" * 70)

    # ── 결과 저장 ─────────────────────────────────────────
    results = {
        "description": "Phase 2 확장 검증 — F&G 전체 히스토리 + 레짐 필터",
        "periods": {
            "is": f"{IS_START[:10]} ~ {IS_END[:10]}",
            "oos": f"{OOS_START[:10]} ~ {OOS_END[:10]}",
            "fg_available_from": "2018-02-01",
        },
        "bh_is": round(bh_is, 4),
        "bh_oos": round(bh_oos, 4),
        "strategies": {
            "fg_base_is": r_base_is.metrics.__dict__,
            "fg_base_oos": r_base_oos.metrics.__dict__,
            "cooldown_is": r_cd_is.metrics.__dict__,
            "cooldown_oos": r_cd_oos.metrics.__dict__,
            "regime_cooldown_is": r_regime_is.metrics.__dict__,
            "regime_cooldown_oos": r_regime_oos.metrics.__dict__,
        },
        "sweep_top10": sweep_df[cols].head(10).to_dict("records") if len(sweep_df) > 0 else [],
        "phase3_strict": strict_pass,
        "phase3_relaxed": relaxed_pass,
    }

    if len(sweep_df) > 0:
        results["best_params"] = {
            "buy_thr": int(best["buy_thr"]),
            "sell_thr": int(best["sell_thr"]),
            "regime_dd": best["regime_dd"],
            "cd_days": int(best["cd_days"]),
            "sl_pct": best["sl_pct"],
        }
        results["best_oos"] = r_best_oos.metrics.__dict__

    return results


if __name__ == "__main__":
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_extended_is_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
