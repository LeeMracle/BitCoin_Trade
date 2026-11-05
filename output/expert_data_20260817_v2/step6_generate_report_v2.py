"""Step 6 (v2) — 백테스트 리포트 .md 자동 생성 (relaxed 필터).

v1 산출물 (workspace/runs/mtf_pullback_5r/) 도 로드하여 비교 표 첨부.
metrics_is.json + metrics_oos.json + sensitivity_oos.csv + analysis_20260817.json 취합.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

import pandas as pd

OUT_DIR = Path(__file__).resolve().parent
RUN_DIR = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r_v2"
V1_RUN_DIR = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r"
REPORT_DIR = Path(__file__).resolve().parents[2] / "workspace" / "reports"
DECISION_DIR = Path(__file__).resolve().parents[2] / "docs" / "decisions"


def _fmt_metrics(m: dict) -> str:
    lines = [
        f"- 거래수: **{m.get('n_trades', 0)}**",
        f"- 총수익률: **{m.get('total_return_pct', 0):.2f}%**",
        f"- CAGR: {m.get('cagr_pct', 0):.2f}%",
        f"- Sharpe: **{m.get('sharpe', 0):.3f}**",
        f"- Sortino: {m.get('sortino', 0):.3f}",
        f"- MDD: **{m.get('mdd_pct', 0):.2f}%**",
        f"- Profit Factor: **{m.get('profit_factor', 0):.2f}**",
        f"- 승률: **{m.get('win_rate_pct', 0):.2f}%**",
        f"- 평균 승/평균 패 (%기준): {m.get('avg_win_pct', 0):.3f}% / {m.get('avg_loss_pct', 0):.3f}%",
        f"- 손익비 (승/패 절댓값): {m.get('win_loss_ratio', 0):.2f}",
        f"- 평균 보유 시간: {m.get('avg_hold_hours', 0):.1f}h",
        f"- 최대 연속 손실: {m.get('max_consec_losses', 0)}회",
        f"- 청산 사유: {m.get('exit_reasons', {})}",
        f"- 최종 자본: {m.get('final_equity_krw', 0):,.0f} KRW",
    ]
    return "\n".join(lines)


def _load_safe(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _tp_reach(m: dict) -> float:
    n = m.get("n_trades", 0)
    if n <= 0:
        return 0.0
    return m.get("exit_reasons", {}).get("TP", 0) / n


def _compare_row(label: str, v1: dict, v2: dict, key: str, fmt: str = "{:.3f}") -> str:
    v1v = v1.get(key, 0) if v1 else 0
    v2v = v2.get(key, 0) if v2 else 0
    return f"| {label} | {fmt.format(v1v)} | {fmt.format(v2v)} |"


def main():
    # v2 metrics
    is_m = _load_safe(RUN_DIR / "metrics_is_20260817.json")
    oos_m = _load_safe(RUN_DIR / "metrics_oos_20260817.json")
    sens = pd.read_csv(RUN_DIR / "sensitivity_20260817.csv") if (RUN_DIR / "sensitivity_20260817.csv").exists() else pd.DataFrame()
    analysis = _load_safe(RUN_DIR / "analysis_20260817.json")

    # v1 metrics (비교용)
    is_v1 = _load_safe(V1_RUN_DIR / "metrics_is_20260817.json")
    oos_v1 = _load_safe(V1_RUN_DIR / "metrics_oos_20260817.json")

    # PASS/CONDITIONAL/FAIL 판정 (v2 OOS)
    sharpe = oos_m.get("sharpe", 0)
    mdd = oos_m.get("mdd_pct", 0)
    pf = oos_m.get("profit_factor", 0)
    n = oos_m.get("n_trades", 0)
    tp_reach = _tp_reach(oos_m)

    pass_criteria = {
        "sharpe_>=_0.8": sharpe >= 0.8,
        "mdd_>=_-15%": mdd >= -15,
        "pf_>=_1.3": pf >= 1.3,
        "n_>=_60": n >= 60,
        "tp_reach_>=_40%": tp_reach >= 0.40,
    }
    conditional_criteria = {
        "sharpe_0.5~0.8": 0.5 <= sharpe < 0.8,
        "mdd_-15~-20%": -20 <= mdd < -15,
        "pf_1.1~1.3": 1.1 <= pf < 1.3,
    }

    # 자기평가 금지 — 라벨 비워두고 "PM 검토 후 확정" 표기
    if all(pass_criteria.values()):
        verdict_candidate = "PASS 후보"
    elif any(conditional_criteria.values()) and n >= 30:
        verdict_candidate = "CONDITIONAL 후보"
    else:
        verdict_candidate = "FAIL 후보"
    verdict = f"{verdict_candidate} (PM 검토 후 확정 — expert 자기평가 금지)"

    # 신호 확보 성공 판정 (v2 거래수 >= v1 x 5배)
    v1_oos_n = oos_v1.get("n_trades", 0) if oos_v1 else 0
    v1_is_n = is_v1.get("n_trades", 0) if is_v1 else 0
    v2_oos_n = n
    v2_is_n = is_m.get("n_trades", 0)
    threshold_n = max(v1_oos_n * 5, 5)  # v1 x 5배 또는 최소 5건
    signal_success = v2_oos_n >= threshold_n
    signal_success_txt = (
        f"v2 OOS 거래수 {v2_oos_n} vs 임계 {threshold_n} (v1 OOS {v1_oos_n}건 × 5배) → "
        f"{'완화 목적 달성' if signal_success else '완화에도 신호 부족'}"
    )

    # 감도 분석 TOP 10 (거래 발생 조합 우선, Sharpe 기준)
    sens_top = ""
    if not sens.empty:
        sens_ranked = sens.copy()
        sens_ranked["has_trade"] = (sens_ranked["n_trades"] >= 1).astype(int)
        sens_sorted = sens_ranked.sort_values(["has_trade", "sharpe"], ascending=[False, False]).head(10)
        sens_top = sens_sorted[["rsi_trigger", "tp", "sl", "n_trades", "sharpe",
                                 "mdd_pct", "profit_factor", "win_rate_pct",
                                 "n_tp", "n_sl", "n_time"]].to_markdown(index=False)

    bear = analysis.get("bear_week", {})
    sym_top = analysis.get("sym_pnl_top", [])[:10]
    sym_bot = analysis.get("sym_pnl_bottom", [])[:10]
    tw = analysis.get("top_wins", [])[:10]
    tl = analysis.get("top_losses", [])[:10]

    # v1 vs v2 비교 표
    def _cmp(label, key, fmt="{:.3f}"):
        v1_is_v = is_v1.get(key, 0) if is_v1 else 0
        v1_oos_v = oos_v1.get(key, 0) if oos_v1 else 0
        v2_is_v = is_m.get(key, 0) if is_m else 0
        v2_oos_v = oos_m.get(key, 0) if oos_m else 0
        return f"| {label} | {fmt.format(v1_is_v)} | {fmt.format(v1_oos_v)} | {fmt.format(v2_is_v)} | {fmt.format(v2_oos_v)} |"

    def _cmp_tp_reach():
        v1_is_r = _tp_reach(is_v1) * 100 if is_v1 else 0
        v1_oos_r = _tp_reach(oos_v1) * 100 if oos_v1 else 0
        v2_is_r = _tp_reach(is_m) * 100 if is_m else 0
        v2_oos_r = _tp_reach(oos_m) * 100 if oos_m else 0
        return f"| TP 도달률 (%) | {v1_is_r:.1f}% | {v1_oos_r:.1f}% | {v2_is_r:.1f}% | {v2_oos_r:.1f}% |"

    compare_rows = [
        _cmp("거래수 (n)", "n_trades", "{:d}"),
        _cmp("Sharpe", "sharpe"),
        _cmp("MDD (%)", "mdd_pct", "{:.2f}%"),
        _cmp("Profit Factor", "profit_factor", "{:.3f}"),
        _cmp("승률 (%)", "win_rate_pct", "{:.1f}%"),
        _cmp("총수익률 (%)", "total_return_pct", "{:.2f}%"),
        _cmp_tp_reach(),
    ]
    compare_table = "\n".join(compare_rows)

    md = f"""# MTF_Pullback_5R v2 (relaxed) 백테스트 리포트 (2026-08-17)

## 개요

- 발의: bata-investment-expert (사용자 2026-08-17 옵션 B 승인)
- v1 대비 완화 3항목: RSI lookback 5→10봉, EMA20 근접 0.97→0.95, Vol multiplier 1.2→1.0
- 나머지 조건 v1과 동일 (plan `workspace/plans/20260817_2_swing_mtf_pullback_5r_v2_relaxed.md`)
- 전략: R:R 2:1 (TP +5%, SL -2.5%) 다중 타임프레임 눌림목
- 데이터: 업비트 KRW blue-chip 12종목 (BTC/ETH/XRP/ADA/SOL/DOGE/LINK/XLM/SHIB/ONG/SNT/ONT), 2024-01-01 ~ 2026-08-16
- 초기 자본: 10,000,000 KRW / 위험/트레이드 0.5% / MAX_POSITIONS 3
- 왕복 실비용: 0.20% (수수료 0.10% + 슬리피지 0.10%)

## 최종 판정: **{verdict}**

### PASS 조건 대비 (v2 OOS)
| 조건 | 목표 | 실측 | 판정 |
| --- | --- | --- | --- |
| Sharpe | ≥ 0.8 | {sharpe:.3f} | {"OK" if pass_criteria['sharpe_>=_0.8'] else "X"} |
| MDD | ≥ -15% | {mdd:.2f}% | {"OK" if pass_criteria['mdd_>=_-15%'] else "X"} |
| Profit Factor | ≥ 1.3 | {pf:.2f} | {"OK" if pass_criteria['pf_>=_1.3'] else "X"} |
| 거래수 | ≥ 60 | {n} | {"OK" if pass_criteria['n_>=_60'] else "X"} |
| TP 도달률 | ≥ 40% | {tp_reach*100:.1f}% | {"OK" if pass_criteria['tp_reach_>=_40%'] else "X"} |

### 신호 확보 성공 판정
{signal_success_txt}

## v1 vs v2 비교 표

| 지표 | v1 IS | v1 OOS | v2 IS | v2 OOS |
| --- | ---: | ---: | ---: | ---: |
{compare_table}

## IS (2024-01-01 ~ 2025-06-30, 18개월)

{_fmt_metrics(is_m)}

## OOS (2025-07-01 ~ 2026-08-16, 13.5개월)

{_fmt_metrics(oos_m)}

## 리스크 이벤트 분석 (OOS)

- BTC 주간 -10% 이상 급락 주 수: {bear.get('bear_weeks', 0)}
- 급락 주 중 스윙 거래 수: {bear.get('trades_in_bear', 0)}
- 급락 주 PnL 합계: {bear.get('pnl_in_bear_krw', 0):,.0f} KRW
- 최대 연속 손실 (OOS): {oos_m.get('max_consec_losses', 0)}회

## 종목별 PnL (OOS 상위/하위)

### Top 10 (합계 이익)
| symbol | n | total_pnl (KRW) | win_rate (%) |
| --- | ---: | ---: | ---: |
""" + "\n".join([f"| {r['symbol']} | {r['n']} | {r['total_pnl']:,.0f} | {r['win_rate']:.1f} |"
                 for r in sym_top]) + """

### Bottom 10 (합계 손실 또는 낮은 수익)
| symbol | n | total_pnl (KRW) | win_rate (%) |
| --- | ---: | ---: | ---: |
""" + "\n".join([f"| {r['symbol']} | {r['n']} | {r['total_pnl']:,.0f} | {r['win_rate']:.1f} |"
                 for r in sym_bot]) + f"""

## Top 10 승리 거래 (OOS)

| symbol | entry_dt | exit_dt | exit_reason | ret (%) | pnl (KRW) | hold (h) |
| --- | --- | --- | --- | ---: | ---: | ---: |
""" + "\n".join([f"| {r['symbol']} | {str(r['entry_dt_utc'])[:16]} | {str(r['exit_dt_utc'])[:16]} | {r['exit_reason']} | {r['ret_pct']} | {r['pnl_krw']:,.0f} | {r['hold_hours']:.1f} |"
                 for r in tw]) + """

## Top 10 패배 거래 (OOS)

| symbol | entry_dt | exit_dt | exit_reason | ret (%) | pnl (KRW) | hold (h) |
| --- | --- | --- | --- | ---: | ---: | ---: |
""" + "\n".join([f"| {r['symbol']} | {str(r['entry_dt_utc'])[:16]} | {str(r['exit_dt_utc'])[:16]} | {r['exit_reason']} | {r['ret_pct']} | {r['pnl_krw']:,.0f} | {r['hold_hours']:.1f} |"
                 for r in tl]) + f"""

## 감도 분석 (v2 OOS)

RSI trigger × TP × SL = 27 조합. Sharpe 상위 10:

{sens_top if sens_top else '(감도 분석 미실행)'}

## 차트

- 자본곡선: `workspace/runs/mtf_pullback_5r_v2/equity_curve_20260817.png`
- 월별 히트맵: `workspace/runs/mtf_pullback_5r_v2/monthly_heatmap_20260817.png`

## 산출물 경로

- 백테스트 리포트: `workspace/reports/backtest_mtf_pullback_5r_v2_20260817.md` (본 파일)
- IS 거래 로그: `workspace/runs/mtf_pullback_5r_v2/trades_is_20260817.csv`
- OOS 거래 로그: `workspace/runs/mtf_pullback_5r_v2/trades_oos_20260817.csv`
- 감도 분석 CSV: `workspace/runs/mtf_pullback_5r_v2/sensitivity_20260817.csv`
- 자본곡선 CSV: `workspace/runs/mtf_pullback_5r_v2/equity_{{is,oos}}_20260817.csv`
- 분석 JSON: `workspace/runs/mtf_pullback_5r_v2/analysis_20260817.json`
- 스크립트: `output/expert_data_20260817_v2/step{{3..6}}_*_v2.py`

## 유니버스 제약 (v1과 동일)

- **실 유니버스: 12종목** — v1과 동일 (fetch 재실행 안 함)
- 알트 편중 없음. 결과 일반화 시 "top-tier 12종목" 제약 명시 필수

## 완화 효과 진단

- v2 신호수 = v1 대비 배수: {(v2_oos_n / max(v1_oos_n, 1)):.1f}x (OOS 기준)
- v2 신호수 = v1 IS 대비 배수: {(v2_is_n / max(v1_is_n, 1)):.1f}x (IS 기준)
- 3개 완화 항목이 신호 확보에 기여했는지 감도표에서 재확인 가능

## 주의사항

- 시점별 유동성 재계산은 백테스트 루프에서 수행 (미래정보 누출 zero)
- 지표 계산은 t 시점까지의 데이터만 사용
- 다음 4h 봉 시가 체결 (lessons #1 준수)
- SL/TP 동시 = SL 우선 (lessons #25 준수)
- REGIME C assert 위반 없음 확인 (step3 v2 실행 로그 참조)
"""

    report_path = REPORT_DIR / "backtest_mtf_pullback_5r_v2_20260817.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"saved: {report_path}")

    # ADR 초안
    def _sens_top_line(sens_df):
        if sens_df.empty:
            return "  (감도 분석 미실행 — 리포트 참조)"
        sub = sens_df[sens_df['n_trades'] >= 1].sort_values('sharpe', ascending=False).head(3)
        if sub.empty:
            return "  (감도분석 27조합 전부 n=0 — 완화에도 신호 부족)"
        return "\n".join([
            f"  - RSI={r.rsi_trigger} TP={r.tp} SL={r.sl}: Sharpe={r.sharpe:.3f}, MDD={r.mdd_pct:.2f}%, PF={r.profit_factor:.2f}, n={r.n_trades}, 청산=TP{r.n_tp}/SL{r.n_sl}/TIME{r.n_time}"
            for r in sub.itertuples()
        ])

    sens_top3_line = _sens_top_line(sens)

    adr_md = f"""# ADR 20260817-2: MTF_Pullback_5R v2 (relaxed) 스윙 전략 백테스트 검증

- 작성일: 2026-08-17
- 담당: bata-investment-expert
- 상태: DRAFT (PM 승인 대기)
- 관련: 사용자 발의(2026-08-17 옵션 B), plan `workspace/plans/20260817_2_swing_mtf_pullback_5r_v2_relaxed.md`, 선행 ADR `20260817_1_swing_mtf_pullback_5r_backtest.md`

## 배경

v1 FAIL 판정(2026-08-17) — 감도분석 27조합 전부 OOS 거래수 0~1건. 필터 조합 자체가 신호 자체를 억제하는 문제.
PM이 옵션 B "스펙 완화 후 재백테스트" 승인. 3개 필터 완화하여 재실행.

## v1 → v2 완화 스펙 (3개, 나머지 동일)

| 조건 | v1 | v2 | 완화 사유 |
|---|---|---|---|
| D (RSI lookback) | 최근 5봉 RSI(14, 4h) ≤ 40 | 최근 **10봉** RSI(14, 4h) ≤ 40 | 5봉(20h)은 짧아 과매도 신호 놓침 |
| E (EMA20 근접) | 현재가 ≥ EMA(20, D) × **0.97** | 현재가 ≥ EMA(20, D) × **0.95** | ±3% 밴드 좁아 진입 봉 소실 |
| H (거래량) | Vol ≥ MA(20, Vol) × **1.2** | Vol ≥ MA(20, Vol) × **1.0** | 반전봉이 반드시 급증 거래량 동반은 아님 |

## 유지 항목

- REGIME 필터 A∧B∧C (lessons #26, ADR 20260516-2 준수)
- 반전캔들 트리거 G (Bullish Engulfing OR Hammer)
- ATR% 범위 F (3~8%)
- 자금관리 (위험/트레이드 0.5%, MAX_POSITIONS_SWING 3)
- 청산 (TP 5%, SL max(low, -2.5%), Time stop 48h+미실현<2%)
- 유니버스 12종목 (fetch 재실행 안 함)
- 데이터 기간 2024-01-01 ~ 2026-08-16, IS/OOS 동일

## 데이터

- IS: 2024-01-01 ~ 2025-06-30
- OOS: 2025-07-01 ~ 2026-08-16
- 유니버스: 12종목 (v1과 동일)

## 결과 요약 (실측)

### v1 vs v2 비교

| 지표 | v1 IS | v1 OOS | v2 IS | v2 OOS |
| --- | ---: | ---: | ---: | ---: |
{compare_table}

### v2 IS
- Sharpe {is_m.get('sharpe', 0):.3f} | MDD {is_m.get('mdd_pct', 0):.2f}% | PF {is_m.get('profit_factor', 0):.2f} | n={is_m.get('n_trades', 0)} | 승률 {is_m.get('win_rate_pct', 0):.1f}%

### v2 OOS (판정 기준)
- Sharpe {sharpe:.3f} | MDD {mdd:.2f}% | PF {pf:.2f} | n={n} | 승률 {oos_m.get('win_rate_pct', 0):.1f}% | TP 도달률 {tp_reach*100:.1f}%

### 신호 확보 판정
- {signal_success_txt}

### 감도 분석 (v2 OOS)
- 27조합 (RSI {{35,40,45}} × TP {{4,5,6}} × SL {{-2.0,-2.5,-3.0}}):
  - 거래 발생 조합 (n≥1): {"없음" if sens.empty else f"{(sens['n_trades']>=1).sum()} / 27"}
  - 거래 미발생 (n=0) 조합: {"없음" if sens.empty else f"{(sens['n_trades']==0).sum()} / 27"}
  - Sharpe 상위 3 (거래 발생 조합 우선):
{sens_top3_line}

## 판정

**{verdict}**

### 판정 근거 (사전 정의 기준, v1과 동일)

| 조건 | 목표 | 실측 | 판정 |
| --- | --- | --- | --- |
| Sharpe | ≥ 0.8 | {sharpe:.3f} | {"PASS" if pass_criteria['sharpe_>=_0.8'] else "FAIL"} |
| MDD | ≥ -15% | {mdd:.2f}% | {"PASS" if pass_criteria['mdd_>=_-15%'] else "FAIL"} |
| PF | ≥ 1.3 | {pf:.2f} | {"PASS" if pass_criteria['pf_>=_1.3'] else "FAIL"} |
| 거래수 | ≥ 60 | {n} | {"PASS" if pass_criteria['n_>=_60'] else "FAIL"} |
| TP 도달률 | ≥ 40% | {tp_reach*100:.1f}% | {"PASS" if pass_criteria['tp_reach_>=_40%'] else "FAIL"} |

## 다음 단계 (PM 승인 시)

- PASS: 페이퍼 트레이딩 슬롯 신설 (기존 DC+ATR 5개와 별도로 3개 슬롯). 1주 관찰 후 실전 검토.
- CONDITIONAL: 감도 분석 상위 파라미터로 재백테스트. 여전히 미달 시 폐기.
- FAIL: 폐기. 사유 문서화 후 대체 전략 발의 (예: 필터 추가 완화 v3, 유니버스 확대 후 재시도, 또는 스윙 전략 자체 폐기).

## 리스크 및 완화

- 유니버스 축소로 인한 거래수 부족 여전 가능 (12종목 blue-chip only)
- 실전 슬리피지 > 백테스트: 페이퍼에서 실측 검증 필수
- 상관성: DC+ATR과 진입 시점 중복 시 위험 노출 3x 초과 가능
- REGIME 게이트 의존: BTC BEAR 지속 시 거래 0

## 자기평가 회피

본 ADR은 정량 지표만 서술. PASS/CONDITIONAL/FAIL 라벨링은 PM 검토 후 확정.
"""
    adr_path = DECISION_DIR / "20260817_2_swing_mtf_pullback_5r_v2_backtest.md"
    adr_path.write_text(adr_md, encoding="utf-8")
    print(f"saved: {adr_path}")

    return {"report": str(report_path), "adr": str(adr_path), "verdict": verdict}


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
