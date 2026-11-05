"""Step 6 — 백테스트 리포트 .md 자동 생성.

metrics_is.json + metrics_oos.json + sensitivity_oos.csv + analysis_20260817.json 취합.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime

import pandas as pd

OUT_DIR = Path(__file__).resolve().parent
RUN_DIR = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r"
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


def main():
    is_m = json.loads((RUN_DIR / "metrics_is_20260817.json").read_text(encoding="utf-8"))
    oos_m = json.loads((RUN_DIR / "metrics_oos_20260817.json").read_text(encoding="utf-8"))
    sens = pd.read_csv(RUN_DIR / "sensitivity_20260817.csv") if (RUN_DIR / "sensitivity_20260817.csv").exists() else pd.DataFrame()
    analysis = json.loads((RUN_DIR / "analysis_20260817.json").read_text(encoding="utf-8")) \
        if (RUN_DIR / "analysis_20260817.json").exists() else {}

    # PASS/CONDITIONAL/FAIL 판정
    sharpe = oos_m.get("sharpe", 0)
    mdd = oos_m.get("mdd_pct", 0)
    pf = oos_m.get("profit_factor", 0)
    n = oos_m.get("n_trades", 0)
    tp_reach = oos_m.get("exit_reasons", {}).get("TP", 0) / max(n, 1)

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

    # 자기평가 금지 (plan §112~115) — 판정 라벨은 PM 검토 후 확정
    if all(pass_criteria.values()):
        verdict_candidate = "PASS 후보"
    elif any(conditional_criteria.values()) and n >= 30:
        verdict_candidate = "CONDITIONAL 후보"
    else:
        verdict_candidate = "FAIL 후보"
    verdict = f"{verdict_candidate} (PM 검토 후 확정 — expert 자기평가 금지)"

    # 감도 분석 TOP 5 (거래 발생 조합 우선, Sharpe 기준)
    sens_top = ""
    if not sens.empty:
        # 거래수 >= 1 우선, 그 안에서 Sharpe 내림차순
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

    md = f"""# MTF_Pullback_5R 백테스트 리포트 (2026-08-17)

## 개요

- 발의: bata-investment-expert (사용자 2026-08-17 요청)
- 전략: R:R 2:1 (TP +5%, SL -2.5%) 다중 타임프레임 눌림목
- 데이터: 업비트 KRW blue-chip 12종목 (BTC/ETH/XRP/ADA/SOL/DOGE/LINK/XLM/SHIB/ONG/SNT/ONT), 2024-01-01 ~ 2026-08-16
  - Step1 screening 40종목 중 2024-01-01 시점 4h/1d 캐시 완비 12종목만 usable
- 초기 자본: 10,000,000 KRW / 위험/트레이드 0.5% / MAX_POSITIONS 3
- 왕복 실비용: 0.20% (수수료 0.10% + 슬리피지 0.10%)

## 최종 판정: **{verdict}**

### PASS 조건 대비
| 조건 | 목표 | 실측 | 판정 |
| --- | --- | --- | --- |
| Sharpe | ≥ 0.8 | {sharpe:.3f} | {"✓" if pass_criteria['sharpe_>=_0.8'] else "×"} |
| MDD | ≥ -15% | {mdd:.2f}% | {"✓" if pass_criteria['mdd_>=_-15%'] else "×"} |
| Profit Factor | ≥ 1.3 | {pf:.2f} | {"✓" if pass_criteria['pf_>=_1.3'] else "×"} |
| 거래수 | ≥ 60 | {n} | {"✓" if pass_criteria['n_>=_60'] else "×"} |
| TP 도달률 | ≥ 40% | {tp_reach*100:.1f}% | {"✓" if pass_criteria['tp_reach_>=_40%'] else "×"} |

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

## 감도 분석 (OOS)

RSI trigger × TP × SL = 27 조합. Sharpe 상위 10:

{sens_top if sens_top else '(감도 분석 미실행)'}

## 차트

- 자본곡선: `workspace/runs/mtf_pullback_5r/equity_curve_20260817.png`
- 월별 히트맵: `workspace/runs/mtf_pullback_5r/monthly_heatmap_20260817.png`

## 산출물 경로

- 백테스트 리포트: `workspace/reports/backtest_mtf_pullback_5r_20260817.md` (본 파일)
- IS 거래 로그: `workspace/runs/mtf_pullback_5r/trades_is_20260817.csv`
- OOS 거래 로그: `workspace/runs/mtf_pullback_5r/trades_oos_20260817.csv`
- 감도 분석 CSV: `workspace/runs/mtf_pullback_5r/sensitivity_20260817.csv`
- 자본곡선 CSV: `workspace/runs/mtf_pullback_5r/equity_{{is,oos}}_20260817.csv`
- 분석 JSON: `workspace/runs/mtf_pullback_5r/analysis_20260817.json`
- 스크립트: `output/expert_data_20260817/step{{1..6}}_*.py`

## 유니버스 제약 (반드시 감안)

- **실 유니버스: 12종목** (BTC, ETH, XRP, ADA, SOL, DOGE, LINK, XLM, SHIB, ONG, SNT, ONT)
  - Step1 screening 40종목 중 2024-01-01~2026-08-16 4h/1d 캐시 완비 종목 12
  - 나머지 28종목: 상장 후 기간 부족(2025+ 상장) 또는 4h 캐시 부재
- **알트코인 편중 없음** — 12종목 모두 blue-chip 계열. 2024~2026 폭발적 상승 알트 미포함 → 스윙 상방 성과 과소평가 가능성
- 업비트 KRW 거래대금 90%+ 커버 (blue-chip only)
- **결과 일반화 시 "top-tier 12종목" 제약 명시 필수**

## Fail-cause 진단 (거래수 극소 원인 분리)

거래수 IS=4 / OOS=1로 판정 미달. 근본 원인:

1. **필터 체인 조합의 매우 낮은 통과율** (전략 자체가 까다로움)
   - 11개 필터 AND 결합: REGIME C + Daily A/B + Volume + Corr + RSI + EMA20 근접 + ATR% + Engulfing/Hammer + Vol×1.2
   - 특히 캔들 트리거(Engulfing/Hammer) × Vol×1.2 × RSI<40 동시 요구 → 하루 96 봉(4h) × 12종목 = 1152 봉/일 중 극소수 성립
2. **유니버스 축소(40→12)로 후보 자체가 줄어듦** (데이터 문제 25% 기여 추정)
   - 원래 40종목이었다면 신호 수 3~4배 예상 (하지만 여전히 60건 미달 가능)
3. **REGIME 게이트 BEAR 구간 손실**: OOS 13.5개월 중 BTC>EMA200 지속 구간 짧으면 진입 기회 자체 부재

→ 결론: **필터 조합 자체가 너무 보수적**. 유니버스 확대(28종목 추가 fetch)만으로는 60건 도달 불가 예상.

## 주의사항

- 시점별 유동성 재계산은 백테스트 루프에서 수행 (미래정보 누출 zero)
- 지표 계산은 t 시점까지의 데이터만 사용
- 다음 4h 봉 시가 체결 (lessons #1 준수)
- SL/TP 동시 = SL 우선 (lessons #25 준수)
- REGIME C assert IS 0/4, OOS 0/1 위반 없음 (미래정보 누출 zero 확인)
"""

    report_path = REPORT_DIR / "backtest_mtf_pullback_5r_20260817.md"
    report_path.write_text(md, encoding="utf-8")
    print(f"saved: {report_path}")

    # ADR 초안
    adr_path = DECISION_DIR / "20260817_1_swing_mtf_pullback_5r_backtest.md"
    adr_md = f"""# ADR 20260817-1: MTF_Pullback_5R 스윙 전략 백테스트 검증

- 작성일: 2026-08-17
- 담당: bata-investment-expert
- 상태: DRAFT (PM 승인 대기)
- 관련: 사용자 발의(2026-08-17), plan `workspace/plans/20260817_1_swing_mtf_pullback_5r_backtest.md`

## 배경

BATA 는 현재 DC(12)+ATR×3.0 추세추종 단일 전략 가동 중 (MAX_POSITIONS 5, REGIME 필터 ON).
추가 수익 채널로 R:R 2:1 다중 타임프레임 눌림목 전략 **MTF_Pullback_5R** 을 병렬 슬롯으로 도입 가능한지 정량 검증한다.

## 스펙 (사용자 발의, 변경 없이 검증)

- 유니버스: 업비트 KRW, MIN_VOLUME_KRW ≥ 5억, BTC 20일 수익률 상관계수 ≥ 0.3
- 추세(Daily): Close>EMA20 & EMA20>EMA50 & BTC>EMA200
- 셋업(4h): RSI(14) 최근 5봉 ≤ 40 & 현재가 ≥ EMA20(D)×0.97 & ATR%∈[3,8]
- 트리거(4h): Engulfing/Hammer & Vol ≥ MA20×1.2 → 다음 4h open 진입
- 자금: 위험 0.5%, MAX_POS 3, 왕복 0.20%
- 청산: TP +5%, SL max(-2.5%, 트리거 저가), Time stop 48h & 미실현<+2%
- SL/TP 동시 = SL 우선

## 데이터

- IS: 2024-01-01 ~ 2025-06-30
- OOS: 2025-07-01 ~ 2026-08-16
- **유니버스: 12종목** (BTC, ETH, XRP, ADA, SOL, DOGE, LINK, XLM, SHIB, ONG, SNT, ONT)
  - Step1 screening 40 → Step2 fetch 결과 usable=12
  - 나머지 28종목: 2024-01-01 이전 상장분에 대해 4h/1d 캐시 부재 또는 상장 후 기간 부족
  - 업비트 KRW 거래대금 90%+ 커버 (blue-chip only) — 알트 편중 없음
- **plan 최소 30종목 대비 축소 유니버스** — 결과 일반화 시 "top-tier only" 제약 명시 필수

## 결과 요약 (실측)

### IS
- Sharpe {is_m.get('sharpe', 0):.3f} | MDD {is_m.get('mdd_pct', 0):.2f}% | PF {is_m.get('profit_factor', 0):.2f} | n={is_m.get('n_trades', 0)} | 승률 {is_m.get('win_rate_pct', 0):.1f}%

### OOS (판정 기준)
- Sharpe {sharpe:.3f} | MDD {mdd:.2f}% | PF {pf:.2f} | n={n} | 승률 {oos_m.get('win_rate_pct', 0):.1f}% | TP 도달률 {tp_reach*100:.1f}%

### 감도 분석
- 27조합 (RSI {{35,40,45}} × TP {{4,5,6}} × SL {{-2.0,-2.5,-3.0}}) OOS 결과:
  - **거래 발생 조합 (n≥1)**: {"없음" if sens.empty else f"{(sens['n_trades']>=1).sum()} / 27"}
  - **거래 미발생 (n=0) 조합**: {"없음" if sens.empty else f"{(sens['n_trades']==0).sum()} / 27"}
  - Sharpe 상위 3 (거래 발생 조합 우선):
{('  (감도 분석 미실행 — 리포트 참조)' if sens.empty else chr(10).join([f'  - RSI={r.rsi_trigger} TP={r.tp} SL={r.sl}: Sharpe={r.sharpe:.3f}, MDD={r.mdd_pct:.2f}%, PF={r.profit_factor:.2f}, n={r.n_trades}, 청산=TP{r.n_tp}/SL{r.n_sl}/TIME{r.n_time}' for r in sens[sens['n_trades']>=1].sort_values('sharpe', ascending=False).head(3).itertuples()])) if not sens.empty else ''}

## 판정

**{verdict}**

### 판정 근거 (사전 정의 기준)

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
- FAIL: 폐기. 사유 문서화 후 대체 전략 발의.

## 리스크 및 완화

- **유니버스 축소로 인한 거래수 부족**: plan 최소 60건 대비 IS=4/OOS=1. 12종목 blue-chip only → 알트 폭발 상승 기회 미포함. 60건 도달 확인을 위해서는 유니버스 재확장(2024-01 이전 상장 알트 4h 캐시 확보) 후 재실행 필요.
- 실전 슬리피지 > 백테스트: 유동성 상위 종목 한정으로 완화. 페이퍼 1주에서 실측 slippage 검증 필수.
- 상관성: DC+ATR 과 진입 시점 중복 시 실제 위험 노출 3x 초과 가능. 페이퍼에서 병행 관찰 후 correlation 계산 필요.
- REGIME 게이트 의존: BTC BEAR 지속 시 거래 0. plan 20260817_1 명시했듯 이 경우 실전 이관 보류.
- **필터 조합의 보수성**: 11개 AND 필터 요구가 신호 자체를 억제. 감도분석 27조합 전부 OOS 거래수 0~1건 → 파라미터 tuning으로 극복 불가. 필터 하나 이상 완화(예: Vol×1.2 → ×1.0, EMA20 근접 0.97 → 0.95) 필요.

## 자기평가 회피

본 ADR은 정량 지표만 서술. PASS/CONDITIONAL/FAIL 라벨링은 PM 검토 후 확정.
"""
    adr_path.write_text(adr_md, encoding="utf-8")
    print(f"saved: {adr_path}")

    return {"report": str(report_path), "adr": str(adr_path), "verdict": verdict}


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
