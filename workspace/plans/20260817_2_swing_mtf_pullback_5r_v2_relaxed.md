# 20260817_2 MTF_Pullback_5R v2 (relaxed) 재백테스트

담당: bata-investment-expert
발의일: 2026-08-17
소요 예상: 3~5h (fetch 스킵, 백테스트 + 감도 + 리포트)
근거: [20260817_1 v1 FAIL 판정](20260817_1_swing_mtf_pullback_5r_backtest.md) — 11개 AND 필터로 신호 극소 (감도분석 27조합 전부 n=0~1)

## 목표

v1 대비 3개 필터 완화로 신호수 확보. R:R 2:1 (TP +5%, SL -2.5%) 원안 골격은 유지. 파라미터 감도 재검증.

## 완화 항목 (3개, 다른 조건 전부 v1과 동일)

| 조건 | v1 | v2 | 완화 사유 |
|---|---|---|---|
| D (RSI lookback) | 최근 5봉 내 RSI(14, 4h) ≤ 40 터치 | **최근 10봉** 내 RSI(14, 4h) ≤ 40 터치 | 5봉(20h)은 짧아 과매도 신호 놓침 |
| E (EMA20 근접) | 현재가 ≥ EMA(20, D) × 0.97 | 현재가 ≥ EMA(20, D) × **0.95** | ±3% 밴드 좁아 진입 봉 소실 |
| H (거래량) | Vol(현봉) ≥ MA(20, Vol) × 1.2 | Vol(현봉) ≥ MA(20, Vol) × **1.0** | 반전봉이 반드시 급증 거래량 동반은 아님 |

## 유지 항목 (변경 금지)

- REGIME 필터 A∧B∧C (lessons #26, ADR 20260516-2 준수)
- 반전캔들 트리거 G (Bullish Engulfing OR Hammer)
- ATR% 범위 F (3~8%)
- 자금관리 (위험/트레이드 0.5%, MAX_POSITIONS_SWING 3)
- 청산 (TP 5%, SL max(low, -2.5%), Time stop 48h+미실현<2%)
- 유니버스 12종목 (fetch 스킵)
- 데이터 기간 2024-01-01 ~ 2026-08-16, IS/OOS 동일

## 성공 기준 (v1과 동일)

- PASS: OOS Sharpe ≥ 0.8 ∧ MDD ≥ -15% ∧ PF ≥ 1.3 ∧ 거래수 ≥ 60 ∧ TP도달률 ≥ 40%
- CONDITIONAL: Sharpe 0.5~0.8, MDD -15~-20%, PF 1.1~1.3 + 감도 대안 파라미터 근접
- FAIL: 그 외

**추가 관찰 지표**: v1 대비 거래수 증가율 (신호 확보 성공 판정용)

## 감도 분석 (v1과 동일 27조합)

- RSI {35, 40, 45} × TP {4, 5, 6}% × SL {-2.0, -2.5, -3.0}%
- 완화된 필터 조합에서 재실행

## 산출물

1. 리포트: `workspace/reports/backtest_mtf_pullback_5r_v2_20260817.md`
2. ADR: `docs/decisions/20260817_2_swing_mtf_pullback_5r_v2_backtest.md`
3. 거래 로그: `workspace/runs/mtf_pullback_5r_v2/trades_{is,oos}_20260817.csv`
4. 자본곡선: `workspace/runs/mtf_pullback_5r_v2/equity_{is,oos}_20260817.csv` + PNG
5. 감도: `workspace/runs/mtf_pullback_5r_v2/sensitivity_20260817.csv`

## 자기평가 회피

- expert 스스로 PASS 판정 금지
- ADR 결론 라벨은 "PM 확정 대기" 표기
- v1 대비 비교 표 필수 (거래수·Sharpe·PF·TP도달률)
