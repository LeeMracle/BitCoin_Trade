#!/usr/bin/env python3
"""배포 전 검증 스크립트 — 시행착오 기반 자동 검증.

docs/lessons/ 의 검증규칙을 코드로 구현한다.
새 시행착오 추가 시 해당 검증규칙도 이 스크립트에 반영할 것.

사용법:
  python scripts/pre_deploy_check.py
  종료코드: 0=통과, 1=실패
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

errors: list[str] = []
warnings: list[str] = []


# ═══════════════════════════════════════════════════════════════════
# 검증 1: 전략 파라미터 일관성 (CLAUDE.md ↔ config.py ↔ .env)
# ref: docs/lessons/20260331_1_dc_strategy_mismatch.md
# ═══════════════════════════════════════════════════════════════════

def check_strategy_consistency() -> None:
    """CLAUDE.md, config.py의 전략 파라미터가 일치하는지 확인."""
    config_file = PROJECT_ROOT / "services" / "execution" / "config.py"
    claude_file = PROJECT_ROOT / "CLAUDE.md"

    if not config_file.exists():
        errors.append("[전략] services/execution/config.py 파일 없음")
        return

    config_content = config_file.read_text(encoding="utf-8")
    dc_match = re.search(r"DONCHIAN_PERIOD\s*=\s*(\d+)", config_content)
    if not dc_match:
        warnings.append("[전략] config.py에서 DONCHIAN_PERIOD 설정을 찾을 수 없음")
        return

    dc_period = dc_match.group(1)

    if claude_file.exists():
        claude_content = claude_file.read_text(encoding="utf-8")
        # DC(N) 패턴 찾기
        claude_dc = re.findall(r"DC\((\d+)\)", claude_content)
        if claude_dc:
            unique_dc = set(claude_dc)
            if dc_period not in unique_dc:
                errors.append(
                    f"[전략] config.py DC_PERIOD={dc_period} vs "
                    f"CLAUDE.md DC({', '.join(unique_dc)}) — 불일치"
                )


# ═══════════════════════════════════════════════════════════════════
# 검증 2: 필수 설정 파일 존재
# ═══════════════════════════════════════════════════════════════════

REQUIRED_CONFIG_FILES = [
    "config/btc-trader.service",
    "services/execution/config.py",
]


def check_config_files() -> None:
    """운영에 필요한 설정 파일 존재 여부 검증."""
    for rel_path in REQUIRED_CONFIG_FILES:
        fpath = PROJECT_ROOT / rel_path
        if not fpath.exists():
            warnings.append(f"[설정] 파일 없음: {rel_path}")


# ═══════════════════════════════════════════════════════════════════
# 검증 3: .env 필수 키 존재
# ═══════════════════════════════════════════════════════════════════

REQUIRED_ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

OPTIONAL_ENV_KEYS = [
    "UPBIT_ACCESS_KEY",
    "UPBIT_SECRET_KEY",
]


def check_env_keys() -> None:
    """.env 필수 키 존재 여부 검증."""
    env_file = PROJECT_ROOT / "services" / ".env"
    if not env_file.exists():
        errors.append("[ENV] services/.env 파일 없음")
        return

    content = env_file.read_text(encoding="utf-8")
    defined_keys: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            defined_keys.add(key)

    for key in REQUIRED_ENV_KEYS:
        if key not in defined_keys:
            errors.append(f"[ENV] .env에 필수 키 누락: {key}")

    for key in OPTIONAL_ENV_KEYS:
        if key not in defined_keys:
            warnings.append(f"[ENV] .env에 권장 키 누락: {key} (실전 거래 시 필수)")


# ═══════════════════════════════════════════════════════════════════
# 검증 4: 서버 경로 일관성
# ref: Stock_Trade lessons/20260331_2 참조
# ═══════════════════════════════════════════════════════════════════

CORRECT_SERVER_PATH = "/home/ubuntu/BitCoin_Trade"

SERVER_PATH_FILES = [
    "scripts/deploy_to_aws.sh",
]


def check_server_paths() -> None:
    """배포 파일의 서버 경로 일관성 검증."""
    pattern = re.compile(r"/home/ubuntu/[Bb]it[Cc]oin.?[Tt]rade")
    for rel_path in SERVER_PATH_FILES:
        fpath = PROJECT_ROOT / rel_path
        if not fpath.exists():
            continue
        for lineno, line in enumerate(
            fpath.read_text(encoding="utf-8").splitlines(), 1
        ):
            for match in pattern.finditer(line):
                if match.group() != CORRECT_SERVER_PATH:
                    errors.append(
                        f"[경로] {rel_path}:{lineno} — "
                        f"'{match.group()}' → '{CORRECT_SERVER_PATH}'로 수정 필요"
                    )


# ═══════════════════════════════════════════════════════════════════
# 검증 5: systemd 서비스 필수 설정
# ref: docs/lessons/20260331_2_server_memory_pressure.md
# ═══════════════════════════════════════════════════════════════════

def check_service_config() -> None:
    """btc-trader.service에 PYTHONUNBUFFERED 또는 -u 플래그 확인."""
    service_file = PROJECT_ROOT / "config" / "btc-trader.service"
    if not service_file.exists():
        return
    content = service_file.read_text(encoding="utf-8")
    if "PYTHONUNBUFFERED" not in content and " -u " not in content:
        warnings.append(
            "[서비스] btc-trader.service에 PYTHONUNBUFFERED=1 또는 python -u 누락"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 6: v2 필터가 모든 매수 경로에 적용되었는지
# ref: docs/lessons/20260404_1_v2_filter_missing_path.md
# ═══════════════════════════════════════════════════════════════════

def check_v2_filter_paths() -> None:
    """전략 필터(F&G, BTC SMA)가 모든 매수 경로에 적용되었는지 검증."""
    monitor_file = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not monitor_file.exists():
        warnings.append("[v2필터] realtime_monitor.py 파일 없음")
        return

    content = monitor_file.read_text(encoding="utf-8")

    # _execute_buy 함수 본문 추출
    buy_match = re.search(
        r"async def _execute_buy\(.*?\n(.*?)(?=\n    async def |\nclass |\Z)",
        content,
        re.DOTALL,
    )
    if not buy_match:
        warnings.append("[v2필터] _execute_buy 함수를 찾을 수 없음")
        return

    buy_body = buy_match.group(1)

    if "_fg_value" not in buy_body and "fg_value" not in buy_body:
        errors.append(
            "[v2필터] realtime_monitor._execute_buy에 F&G 게이트 미적용 — "
            "scanner.py에만 있고 실제 매수 경로에 없음"
        )

    if "_btc_above_ema" not in buy_body and "btc_above_ema" not in buy_body:
        errors.append(
            "[v2필터] realtime_monitor._execute_buy에 BTC EMA 필터 미적용 — "
            "scanner.py에만 있고 실제 매수 경로에 없음"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 7: VB 일일 회전에 날짜 체크가 있는지
# ref: docs/lessons/20260404_2_vb_rotation_duplicate.md
# ═══════════════════════════════════════════════════════════════════

def check_vb_rotation_guard() -> None:
    """VB 일일 회전이 1일 1회만 실행되도록 날짜 체크가 있는지 검증."""
    monitor_file = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not monitor_file.exists():
        return

    content = monitor_file.read_text(encoding="utf-8")

    # _vb_daily_rotation 함수 내에 날짜 체크 존재 여부
    rotation_match = re.search(
        r"def _vb_daily_rotation\(.*?\n(.*?)(?=\n    async def |\n    def |\nclass |\Z)",
        content,
        re.DOTALL,
    )
    if not rotation_match:
        return  # 함수 자체가 없으면 VB 미사용

    rotation_body = rotation_match.group(1)
    if "vb_last_rotation_date" not in rotation_body:
        errors.append(
            "[VB회전] _vb_daily_rotation에 날짜 체크(vb_last_rotation_date) 미적용 — "
            "서비스 재시작마다 중복 회전 발생 위험"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 8: 잔고 조회가 전체 자산을 포함하는지
# ref: docs/lessons/20260405_1_balance_missing_alts.md
# ═══════════════════════════════════════════════════════════════════

def check_balance_includes_alts() -> None:
    """get_balance()가 알트코인 평가액을 포함하는지 검증."""
    client_file = PROJECT_ROOT / "services" / "execution" / "upbit_client.py"
    if not client_file.exists():
        return

    content = client_file.read_text(encoding="utf-8")

    # alts_krw_value 합산 로직이 존재해야 함 (lessons/20260405_1)
    if "alts_krw_value" not in content:
        warnings.append(
            "[잔고] upbit_client.get_balance()에서 알트코인 평가액 합산이 "
            "누락되었을 수 있음 — 모니터링 보고 평가금액 과소 표시 위험"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 9: jarvis_executor 자동화 등록 여부
# ref: docs/lessons/20260408_1_jarvis_cron_missing.md
# ═══════════════════════════════════════════════════════════════════

def check_jarvis_automation() -> None:
    """jarvis_executor가 cron 또는 systemd timer에 등록되었는지 검증.

    로컬에서는 서버의 crontab을 직접 볼 수 없으므로, 최소한
    (1) scripts/jarvis_executor.py 존재 여부,
    (2) 활성 전략이 있는 경우 deploy_to_aws.sh가 cron 등록을 언급하는지
    를 확인한다.
    """
    exec_file = PROJECT_ROOT / "scripts" / "jarvis_executor.py"
    strat_file = PROJECT_ROOT / "workspace" / "jarvis_strategies.json"
    if not exec_file.exists():
        return

    # 활성 전략이 없으면 통과
    active = False
    if strat_file.exists():
        try:
            import json
            data = json.loads(strat_file.read_text(encoding="utf-8"))
            active = any(
                isinstance(v, dict) and v.get("active") for v in data.values()
            )
        except Exception:
            pass

    if not active:
        return

    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if deploy.exists():
        text = deploy.read_text(encoding="utf-8")
        if "jarvis_executor" not in text:
            warnings.append(
                "[자비스] 활성 분할매매 전략이 있으나 deploy_to_aws.sh에 "
                "jarvis_executor cron 등록 로직이 없음 — 서버 재배포 시 "
                "자동화가 누락될 위험"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증 10: vb_state.json ↔ 거래소 잔고 정합성 (선택적)
# ref: docs/lessons/20260408_2_state_balance_mismatch.md
# ═══════════════════════════════════════════════════════════════════

def check_state_balance_consistency() -> None:
    """로컬에서 실행 시 UPBIT 키가 있으면 vb_state ↔ balance 교차 검증."""
    import os
    state_file = PROJECT_ROOT / "workspace" / "vb_state.json"
    if not state_file.exists():
        return
    try:
        import json
        state = json.loads(state_file.read_text(encoding="utf-8"))
        positions = state.get("positions", {}) or {}
    except Exception:
        return

    if not positions:
        return  # 포지션 없음 → 검증 생략

    access = os.environ.get("UPBIT_ACCESS_KEY")
    secret = os.environ.get("UPBIT_SECRET_KEY")
    if not access or not secret:
        warnings.append(
            f"[상태] vb_state.json에 {len(positions)}개 포지션 기록됨 — "
            "UPBIT_ACCESS_KEY 미설정으로 거래소 잔고 교차 검증 생략"
        )
        return

    try:
        import ccxt  # type: ignore
        ex = ccxt.upbit({"apiKey": access, "secret": secret})
        bal = ex.fetch_balance()
        held = {c for c, v in bal["total"].items() if v and v > 0}
    except Exception as e:
        warnings.append(f"[상태] 거래소 잔고 조회 실패: {e}")
        return

    missing = []
    for sym in positions.keys():
        base = sym.split("/")[0]
        if base not in held:
            missing.append(sym)
    if missing:
        errors.append(
            f"[상태] vb_state.json 포지션이 거래소에 없음: {', '.join(missing)} — "
            "state ↔ balance 불일치 (lessons/20260408_2 참조)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 11: NoneType 포매팅 린트 (lint_none_format 위임)
# ref: docs/lessons/20260408_4_nonetype_format_lint.md
# ═══════════════════════════════════════════════════════════════════

def check_none_format_lint() -> None:
    """scripts/lint_none_format.py 를 호출하여 숫자 포매팅 안전성 검증."""
    import subprocess
    lint_script = PROJECT_ROOT / "scripts" / "lint_none_format.py"
    if not lint_script.exists():
        warnings.append("[린트] lint_none_format.py 스크립트 없음")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(lint_script), "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(PROJECT_ROOT),
            timeout=60,
        )
    except Exception as e:
        warnings.append(f"[린트] lint_none_format 실행 실패: {e}")
        return

    if result.returncode != 0:
        # ERROR 라인만 요약해서 포함
        err_lines = [
            line for line in result.stdout.splitlines()
            if "[ERROR]" in line
        ]
        summary = "\n".join(err_lines[:5]) or result.stdout[-400:]
        errors.append(
            "[린트] NoneType 포매팅 위반 탐지 — 재발방지 규칙 위반:\n"
            + summary
        )


def check_cb_l2_config() -> None:
    """CB L2 (ADR 20260408_1) 설정 검증.

    - config.py에 L2/L1 자동해제 상수가 존재하고 음수/비율 범위가 정상인가
    - circuit_breaker.py에 L2 관련 핵심 심볼이 노출되어 있는가
    - realtime_monitor.py가 L2 훅을 실제 호출하는가
    """
    import re
    cfg_path = PROJECT_ROOT / "services" / "execution" / "config.py"
    cb_path = PROJECT_ROOT / "services" / "execution" / "circuit_breaker.py"
    if not cfg_path.exists():
        errors.append("[CB-L2] config.py 파일 없음")
        return
    if not cb_path.exists():
        errors.append("[CB-L2] circuit_breaker.py 파일 없음")
        return

    cfg_txt = cfg_path.read_text(encoding="utf-8")

    def _num(pattern: str):
        m = re.search(pattern, cfg_txt)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    l1 = _num(r"CIRCUIT_BREAKER_THRESHOLD\s*=\s*(-?[\d.]+)")
    l2 = _num(r"CIRCUIT_BREAKER_L2_THRESHOLD\s*=\s*(-?[\d.]+)")
    resume = _num(r"CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT\s*=\s*([\d.]+)")

    if l2 is None:
        errors.append("[CB-L2] config.CIRCUIT_BREAKER_L2_THRESHOLD 미정의")
    elif not (-1 < l2 < 0):
        errors.append(f"[CB-L2] L2 임계값 비정상: {l2} (기대: -1 < x < 0)")
    elif l1 is not None and l2 >= l1:
        errors.append(f"[CB-L2] L2({l2}) >= L1({l1}) — L2는 L1보다 더 엄격해야 함")

    if resume is None:
        errors.append("[CB-L2] config.CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT 미정의")
    elif not (0 < resume <= 1):
        errors.append(f"[CB-L2] L1 auto-resume 비율 비정상: {resume}")

    cb_txt = cb_path.read_text(encoding="utf-8")
    for sym in ("check_and_trigger_l2", "is_l2_triggered", "check_l1_auto_resume"):
        if f"def {sym}" not in cb_txt:
            errors.append(f"[CB-L2] circuit_breaker.{sym} 미정의")

    rt_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if rt_path.exists():
        txt = rt_path.read_text(encoding="utf-8")
        if "check_and_trigger_l2" not in txt:
            errors.append("[CB-L2] realtime_monitor가 check_and_trigger_l2를 호출하지 않음")
        if "_liquidate_all_positions" not in txt:
            errors.append("[CB-L2] realtime_monitor에 _liquidate_all_positions 훅 누락")


def check_cb_log_throttle() -> None:
    """CB 로그 스팸 방지 throttle 존재 검증 (lessons/20260410_1).

    realtime_monitor.py에서 서킷브레이커 "발동 중" 로그가
    throttle 없이 매 이벤트마다 출력되면 일 수천 건 스팸 발생.
    _cb_log_ts 필드와 throttle 로직이 존재하는지 확인한다.
    """
    rt_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rt_path.exists():
        return
    txt = rt_path.read_text(encoding="utf-8")
    if "_cb_log_ts" not in txt:
        errors.append("[CB-LOG] realtime_monitor에 _cb_log_ts (CB 로그 throttle) 누락 — lessons/20260410_1")


# ═══════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 50)
    print("배포 전 검증 (pre-deploy check)")
    print("=" * 50)

    check_strategy_consistency()
    check_config_files()
    check_env_keys()
    check_server_paths()
    check_service_config()
    check_v2_filter_paths()
    check_vb_rotation_guard()
    check_balance_includes_alts()
    check_jarvis_automation()
    check_state_balance_consistency()
    check_none_format_lint()
    check_cb_l2_config()
    check_cb_log_throttle()

    if warnings:
        print(f"\n경고 {len(warnings)}건:")
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}")

    if errors:
        print(f"\n오류 {len(errors)}건 발견:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        print(f"\n배포를 중단합니다. 위 오류를 먼저 수정하세요.")
        sys.exit(1)
    else:
        print("\n모든 검증 통과. 배포를 진행합니다.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
