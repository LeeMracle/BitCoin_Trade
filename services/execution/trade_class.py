"""청산 거래 분류 — "전략이 낸 성적"과 "사람이 개입한 결과"를 가른다.

## 왜 필요한가 (2026-08-25 실제 발생)

ADR 20260823-1의 검증 창(표본 30건)은 **전략이 설계대로 도는가**를 보는 장치다.
그런데 같은 `closed_trades` 에 사람이 개입한 청산이 섞여 들어온다:

    JUP/KRW  +6.44%  manual_tp_user   ← 사용자 지시로 판 것
    STX/KRW  +7.71%  tp2_full_exit    ← 전략이 판 것

둘을 합쳐 세면 "검증 2/30건, 승률 100%"가 되는데, 이 승률에는 전략의 기여가 절반뿐이다.
수동 매도는 사람이 유리한 타이밍에 하기 마련이므로 **통계를 낙관 쪽으로 오염**시킨다.
반대로 손실 구간에서 개입하면 비관 쪽으로 오염된다. 어느 쪽이든 판정 근거가 흐려진다.

## 왜 allowlist(전략 화이트리스트)인가

"manual_ 로 시작하면 제외" 같은 blocklist 는 **새로운 사유가 생기면 조용히 전략 성적에
섞인다**. 반대로 전략 사유를 열거해 두면, 분류되지 않은 사유는 `unknown` 으로 떨어져
보고서에 눈에 띄게 표시된다 — 실수의 방향이 "몰래 섞임"이 아니라 "드러남"이 된다.

`pre_deploy_check.check_exit_reasons_classified()` 가 코드의 `_close_position(...)`
리터럴을 훑어 미분류 사유가 있으면 배포를 막는다.
"""
from __future__ import annotations

# 전략이 스스로 낸 청산 — 검증 표본에 포함된다
STRATEGY_EXIT_REASONS = frozenset({
    "tp_complete",        # 모든 TP 단계 완료
    "tp1_full_exit",      # 마지막 TP 단계에서 잔량 전량
    "tp2_full_exit",
    "tp3_full_exit",
    "trail_stop",         # 트레일링/하드 손절
    "daily_rotation",     # multi_trader 일일 회전
})

# 사람이 개입한 청산 — 전략 성적이 아니다
MANUAL_EXIT_REASONS = frozenset({
    "manual_sell",        # 거래소에서 직접 매도 → 봇이 사후 인식
    "manual_tp_user",     # 사용자 지시 익절 (scripts/manual_close_position.py)
    "manual_slot_cleanup",
    "manual_close",        # manual_close_position.py 기본 사유
    "weight_trim",        # 비중 상한 소급 적용 (ADR 20260825-1)
})

# 상태 보정 — 매매 결과가 아니라 장부 정리
REPAIR_EXIT_REASONS = frozenset({
    "auto_cleanup_zero_balance",  # 잔고 0 자동 정리 (대개 수동 매도의 후속)
    "reconcile_removed",          # fix_state_balance_mismatch
    "tp_complete_backfill",       # backfill_realized_pl
})

STRATEGY, MANUAL, REPAIR, UNKNOWN = "strategy", "manual", "repair", "unknown"


def classify(trade: dict) -> str:
    """청산 1건을 strategy / manual / repair / unknown 으로 분류."""
    reason = str(trade.get("exit_reason") or "")
    if reason in STRATEGY_EXIT_REASONS:
        return STRATEGY
    if reason in MANUAL_EXIT_REASONS:
        return MANUAL
    if reason in REPAIR_EXIT_REASONS:
        return REPAIR
    return UNKNOWN


def in_window(closed: list[dict], strategy_start: str) -> list[dict]:
    """검증 창(strategy_start 이후) 안의 청산만 (ADR 20260823-1).

    exit_date 는 문자열 비교다 — 양쪽 다 UTC "YYYY-MM-DD HH:MM" 형식이어야 한다
    (교훈: 로컬시간 혼용 시 경계에서 어긋난다).
    """
    return [t for t in closed if str(t.get("exit_date", "")) >= str(strategy_start)]


def split(closed: list[dict], strategy_start: str) -> dict[str, list[dict]]:
    """검증 창 안의 청산을 분류별로 나눈다.

    Returns:
        {"strategy": [...], "manual": [...], "repair": [...], "unknown": [...]}
    """
    out: dict[str, list[dict]] = {STRATEGY: [], MANUAL: [], REPAIR: [], UNKNOWN: []}
    for t in in_window(closed, strategy_start):
        out[classify(t)].append(t)
    return out


def summarize(trades: list[dict]) -> dict:
    """건수 / 승률 / 평균수익률 — 표본이 없으면 0으로 채운다."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "avg_ret": 0.0, "sum_ret": 0.0}
    wins = sum(1 for t in trades if (t.get("return_pct") or 0) > 0)
    total = sum((t.get("return_pct") or 0) for t in trades)
    return {"n": n, "wins": wins, "win_rate": wins / n * 100,
            "avg_ret": total / n, "sum_ret": total}


def non_strategy_note(parts: dict[str, list[dict]]) -> str:
    """전략 외 청산이 있으면 한 줄 주석, 없으면 빈 문자열.

    보고서에서 "왜 거래는 있었는데 표본이 안 늘었나"를 설명하는 자리다.
    """
    bits = []
    for kind, label in ((MANUAL, "수동"), (REPAIR, "보정"), (UNKNOWN, "미분류")):
        if parts.get(kind):
            s = summarize(parts[kind])
            bits.append(f"{label} {s['n']}건({s['sum_ret']:+.1f}%)")
    return ("ⓘ 표본 제외: " + " / ".join(bits)) if bits else ""


def counts_for_consec_loss(trade: dict) -> bool:
    """연패(5연패 자동 중단) 산정에 포함할 청산인가.

    ## 통계 경로와 일부러 다르게 판정한다

    | | 통계(검증 표본) | 연패 카운터(안전장치) |
    |---|---|---|
    | strategy | 포함 | 포함 |
    | manual / repair | **제외** | **제외** |
    | **unknown** | **제외** | **포함** |

    `unknown` 의 처리가 갈리는 것은 실수가 아니라 **각 경로가 안전한 쪽으로 틀리도록**
    맞춘 것이다:

    - 통계에서 미분류를 포함하면 전략 성적이 **부풀 수** 있다 → 제외해서 과소평가 쪽으로.
    - 안전장치에서 미분류를 제외하면 브레이커가 **안 걸릴 수** 있다 → 포함해서 민감한 쪽으로.

    즉 새 매도 경로를 만들고 `trade_class` 등록을 잊었을 때,
    "성적이 좋아 보이는데 브레이커가 안 걸린다"는 최악 조합이 나오지 않는다.

    manual 을 빼는 이유(2026-08-25 사용자 결정): 연패 브레이커는 **전략이 망가졌을 때**
    매수를 멈추는 장치다. 사람이 비중을 줄이거나(weight_trim) 지시로 정리한 손실은
    전략의 실패가 아니므로, 이걸로 72시간 매수 중단이 걸리면 오작동이다.
    """
    return classify(trade) in (STRATEGY, UNKNOWN)
