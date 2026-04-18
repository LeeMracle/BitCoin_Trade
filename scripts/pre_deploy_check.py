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
            errors.append(
                "[자비스] 활성 분할매매 전략이 있으나 deploy_to_aws.sh에 "
                "jarvis_executor cron 등록 로직이 없음 — 서버 재배포 시 "
                "자동화가 누락되어 체결 기회를 놓칠 위험. "
                "ref: docs/lessons/20260408_1_jarvis_cron_missing.md (재발 이력 있음, P4-14c)"
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
# 검증 13: Heartbeat / WS-stale / hourly_sync (P7-03/06/07)
# ref: workspace/plans/20260410_monitoring_framework.md
# ═══════════════════════════════════════════════════════════════════

def check_monitoring_hooks() -> None:
    """realtime_monitor에 heartbeat / ws-stale / hourly_sync 훅이 있는지."""
    rt_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rt_path.exists():
        return
    txt = rt_path.read_text(encoding="utf-8")

    # P7-03: /tmp/bata_heartbeat touch
    if "/tmp/bata_heartbeat" not in txt:
        errors.append(
            "[P7-03] realtime_monitor에 /tmp/bata_heartbeat touch 누락 — Watchdog이 감지 불가"
        )

    # P7-06: _hourly_sync 존재
    if "_hourly_sync" not in txt or "async def _hourly_sync" not in txt:
        errors.append(
            "[P7-06] realtime_monitor에 _hourly_sync (state ↔ exchange 교차검증) 누락"
        )

    # P7-07: 웹소켓 stale 감지 (timeout=300 + TimeoutError 처리)
    if "timeout=300" not in txt or "TimeoutError" not in txt:
        errors.append(
            "[P7-07] realtime_monitor 웹소켓 루프에 5분 timeout + TimeoutError 처리 누락"
        )


def check_watchdog_script() -> None:
    """watchdog_check.sh 존재 + 핵심 동작 요소 확인."""
    w_path = PROJECT_ROOT / "scripts" / "watchdog_check.sh"
    if not w_path.exists():
        errors.append("[P7-04] scripts/watchdog_check.sh 없음")
        return
    txt = w_path.read_text(encoding="utf-8")
    if "bata_heartbeat" not in txt:
        errors.append("[P7-04] watchdog_check.sh가 /tmp/bata_heartbeat를 참조하지 않음")
    if "systemctl restart" not in txt:
        errors.append("[P7-04] watchdog_check.sh에 systemctl restart 로직 누락")


def check_service_watchdog_sec() -> None:
    """config/btc-trader.service에 WatchdogSec 존재 + Type=notify + TimeoutStartSec 충분."""
    s_path = PROJECT_ROOT / "config" / "btc-trader.service"
    if not s_path.exists():
        # 상위 check_config_files에서 WARN 처리됨 — 여기서는 스킵
        return
    txt = s_path.read_text(encoding="utf-8")
    if "WatchdogSec" not in txt:
        errors.append("[P7-05] config/btc-trader.service에 WatchdogSec 누락")
    if "Type=notify" not in txt:
        warnings.append(
            "[P7-05] config/btc-trader.service의 Type이 notify가 아님 — "
            "WatchdogSec는 Type=notify에서 가장 안정적"
        )
        return
    # Type=notify일 때 TimeoutStartSec이 충분히 큰지 확인.
    # ref: docs/lessons/20260417_2_systemd_notify_timeout_start.md
    m = re.search(r"TimeoutStartSec\s*=\s*(\d+)", txt)
    if not m:
        errors.append(
            "[P7-05] Type=notify 사용 중이나 TimeoutStartSec 미설정 — "
            "기본 90초로 초기화 지연 시 kill/restart 루프 발생 위험 "
            "(ref: lessons/20260417_2)"
        )
    elif int(m.group(1)) < 300:
        errors.append(
            f"[P7-05] TimeoutStartSec={m.group(1)}초 — Type=notify + 긴 초기화 경로에 "
            "부족. 최소 300초 권장 (ref: lessons/20260417_2)"
        )


def check_deploy_cron_registered() -> None:
    """deploy_to_aws.sh가 watchdog/log_volume cron + 로그 파일 초기화를 등록하는지.

    ref: docs/lessons/20260418_2_missing_log_files_silent_cron_failure.md
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    if "watchdog_check.sh" not in txt:
        warnings.append(
            "[P7-04] deploy_to_aws.sh에 watchdog_check.sh cron 등록 로직 없음"
        )
    if "log_volume_check.sh" not in txt:
        warnings.append(
            "[P7-08] deploy_to_aws.sh에 log_volume_check.sh cron 등록 로직 없음"
        )
    # R-log-1: /var/log/*.log 초기화 스니펫 존재 (cron의 silent fail 방지)
    # 배열/직접 두 형태 모두 허용: LOG_FILES=(/var/log/...) 또는 sudo touch /var/log/*.log
    has_log_paths = bool(re.search(r"/var/log/\w+\.log", txt))
    has_sudo_touch = bool(re.search(r"sudo\s+touch\b", txt))
    has_sudo_chown = bool(re.search(r"sudo\s+chown\s+ubuntu:ubuntu", txt))
    if not (has_log_paths and has_sudo_touch):
        errors.append(
            "[로그파일] deploy_to_aws.sh에 'sudo touch'와 '/var/log/*.log' 경로 스니펫 없음 — "
            "cron redirect silent fail 위험 (lessons/20260418_2 R-log-1)"
        )
    if not has_sudo_chown:
        errors.append(
            "[로그파일] deploy_to_aws.sh에 'sudo chown ubuntu:ubuntu' 없음 — "
            "ubuntu 소유 보장 안 됨 (lessons/20260418_2 R-log-2)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 18: 잔고 로그 throttle 적용 (2026-04-18 관찰 과제)
# ref: 로그 스팸 방지 — log_volume_check.sh 임계(5000줄) 보호
# ═══════════════════════════════════════════════════════════════════

def check_balance_log_throttle() -> None:
    """upbit_client.py의 '[잔고] ... 마켓 없음/시세 일괄조회 실패' 로그가
    throttled_print로 감싸져 있는지 검증."""
    up_path = PROJECT_ROOT / "services" / "execution" / "upbit_client.py"
    helper_path = PROJECT_ROOT / "services" / "common" / "log_throttle.py"

    if not up_path.exists():
        return
    if not helper_path.exists():
        errors.append(
            "[잔고-throttle] services/common/log_throttle.py 없음"
        )
        return

    txt = up_path.read_text(encoding="utf-8")
    if "from services.common.log_throttle import throttled_print" not in txt:
        errors.append(
            "[잔고-throttle] upbit_client.py에 throttled_print import 누락"
        )
        return

    # raw print로 남아있는 잔고 로그가 있으면 경고
    raw_patterns = [
        r'print\(f"  \[잔고\] \{c\}/KRW 마켓 없음',
        r'print\(f"  \[잔고\] 알트 시세 일괄조회 실패',
    ]
    for pat in raw_patterns:
        if re.search(pat, txt):
            errors.append(
                f"[잔고-throttle] upbit_client.py에 raw print 잔류 — {pat}"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증 19: 필터 통계 카운터 통합 (P7-09)
# ref: workspace/plans/20260418_team_full_sweep.md AC-3/AC-4
# ═══════════════════════════════════════════════════════════════════

def check_filter_stats_integration() -> None:
    """filter_stats.py 존재 + realtime_monitor에 record_block 훅 ≥ 5회."""
    fs_path = PROJECT_ROOT / "services" / "execution" / "filter_stats.py"
    rm_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"

    if not fs_path.exists():
        errors.append("[P7-09] services/execution/filter_stats.py 없음")
        return
    if not rm_path.exists():
        return

    rm_txt = rm_path.read_text(encoding="utf-8")
    if "from services.execution.filter_stats import" not in rm_txt:
        errors.append(
            "[P7-09] realtime_monitor.py에 filter_stats import 누락"
        )
        return

    hook_count = len(re.findall(r"record_block\(", rm_txt))
    if hook_count < 5:
        errors.append(
            f"[P7-09] record_block 훅 {hook_count}회 — 최소 5회 필요 "
            f"(fg/ema200/atr/cb_l1/cb_l2/vb_gate_a)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 20: 필터 통계 일일보고 통합 (P7-10)
# ═══════════════════════════════════════════════════════════════════

def check_daily_report_filter_section() -> None:
    """daily_report.py가 필터 통계 섹션을 포함하는지."""
    dr_path = PROJECT_ROOT / "scripts" / "daily_report.py"
    if not dr_path.exists():
        return
    txt = dr_path.read_text(encoding="utf-8")
    if "filter_stats" not in txt and "필터 차단" not in txt:
        warnings.append(
            "[P7-10] daily_report.py에 필터 통계 섹션(filter_stats) 미발견"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 21: 메타 린트 스크립트 존재 (P6-12)
# ═══════════════════════════════════════════════════════════════════

def check_meta_lint_script() -> None:
    """scripts/lint_meta.py 존재 여부."""
    ml_path = PROJECT_ROOT / "scripts" / "lint_meta.py"
    if not ml_path.exists():
        warnings.append(
            "[P6-12] scripts/lint_meta.py 없음 — 메타 린트 미구현"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 22: 봉 마감 기반 진입 (실시간 틱 진입 금지)
# ref: docs/lessons/20260329_1_tick_vs_bar_entry.md
# ═══════════════════════════════════════════════════════════════════

def check_bar_based_entry() -> None:
    """realtime_monitor에 봉 마감(일봉 확정) 대기 로직 또는 스캔 인터벌 모드가 있는지 확인.

    검증규칙 (lessons/20260329_1):
    - 일봉 전략 → realtime_monitor.py에 캔들 확정 대기 로직 존재 확인
    - 4시간봉 전략 → 스캔 모드(--scan-interval) 사용 확인
    """
    rm_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rm_path.exists():
        warnings.append("[봉마감진입] realtime_monitor.py 없음 — 봉 마감 진입 가드 확인 불가")
        return

    txt = rm_path.read_text(encoding="utf-8")

    # 봉 마감 대기 패턴: KST 09:00 확정 대기, scan_interval, bar_close 등
    patterns = [
        r"scan.?interval",
        r"bar.?close",
        r"09[:_]00",
        r"candle.?confirm",
        r"rotation",          # _vb_daily_rotation 류 일봉 기준 동작
    ]
    found = any(re.search(p, txt, re.IGNORECASE) for p in patterns)
    if not found:
        warnings.append(
            "[봉마감진입] realtime_monitor.py에 봉 마감 확정 대기 또는 스캔 인터벌 패턴 미발견 — "
            "실시간 틱 즉시 진입 시 가짜 돌파 피해 위험 (lessons/20260329_1)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 23: 백테스트 레짐별 성과 분리 검증
# ref: docs/lessons/20260329_2_backtest_period_bias.md
# ═══════════════════════════════════════════════════════════════════

def check_backtest_regime_split() -> None:
    """workspace/reports/ 최근 전략 리포트에 레짐별(하락장 구간) 성과 분리 표가 있는지 확인.

    검증규칙 (lessons/20260329_2):
    - 전체 OOS Sharpe >= 0.8
    - 최근 6개월 하락장 구간 별도 검증
    - 하락장 MDD >= -15%
    - 승률 >= 35% (하락장 구간)
    """
    reports_dir = PROJECT_ROOT / "workspace" / "reports"
    if not reports_dir.exists():
        warnings.append("[백테스트레짐] workspace/reports/ 디렉토리 없음 — 레짐별 성과 검증 불가")
        return

    # 가장 최근 .md 리포트 탐색
    md_files = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not md_files:
        warnings.append("[백테스트레짐] workspace/reports/에 .md 리포트 파일 없음")
        return

    latest = md_files[0]
    txt = latest.read_text(encoding="utf-8")

    # 레짐별 분리 표 패턴: BULL/BEAR/SIDEWAYS 또는 '하락장' '레짐별'
    regime_patterns = [
        r"BULL",
        r"BEAR",
        r"SIDEWAYS",
        r"레짐",
        r"하락장",
        r"상승장",
    ]
    found = any(re.search(p, txt) for p in regime_patterns)
    if not found:
        warnings.append(
            f"[백테스트레짐] 최근 리포트({latest.name})에 레짐별(BULL/BEAR/하락장) 성과 분리 표 미발견 — "
            "상승장 편향 과대평가 위험 (lessons/20260329_2)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 24: 체결 즉시 안전장치 체크 (연패 중단)
# ref: docs/lessons/20260329_3_auto_stop_delay.md
# ref: docs/lessons/20260418_1_stale_lint_regex_false_warn.md  (정규식 stale 방지 원칙 적용)
# ═══════════════════════════════════════════════════════════════════

def check_post_fill_safety_check() -> None:
    """체결 직후 경로(trader.py + realtime_monitor.py)에 연패 체크 즉시 호출이 있는지 확인.

    검증규칙 (lessons/20260329_3):
    - 체결 콜백 내 연패 체크 로직 존재 확인 (어느 경로든 OK — trader 또는 realtime_monitor)
    - 연패 한도/쿨다운 설정값이 config에 존재하는지 확인

    검증규칙 (lessons/20260418_1 — R-meta 적용):
    - loss_patterns는 실제 프로덕션 식별자(recent_consecutive_losses, is_in_loss_cooldown,
      set_loss_cooldown, "연패 자동 중단", "연패 쿨다운")와 교차검증된 목록이어야 함
    - config 상수 탐색도 실제 이름(MAX_CONSECUTIVE_ERRORS, VB_LOSS_COOLDOWN_HOURS)을 포함
    """
    trader_path = PROJECT_ROOT / "services" / "execution" / "trader.py"
    monitor_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    config_path = PROJECT_ROOT / "services" / "execution" / "config.py"

    # 연패 관련 실존 식별자 (실제 코드 기준):
    #   services/execution/realtime_monitor.py:51  recent_consecutive_losses
    #   services/execution/realtime_monitor.py:52  is_in_loss_cooldown
    #   services/execution/realtime_monitor.py:527 "5연패 자동 중단"
    loss_patterns = [
        r"consecutive_losses",
        r"recent_consecutive_losses",
        r"_check_loss_streak",
        r"emergency_stop",
        r"max_consecutive",
        r"is_in_loss_cooldown",
        r"set_loss_cooldown",
        r"연패\s*자동\s*중단",
        r"연패\s*쿨다운",
    ]

    found_loss = False
    for p in (trader_path, monitor_path):
        if p.exists():
            txt = p.read_text(encoding="utf-8")
            if any(re.search(pat, txt) for pat in loss_patterns):
                found_loss = True
                break

    if not found_loss:
        warnings.append(
            "[체결안전장치] trader.py/realtime_monitor.py에 연패 체크 로직 미발견 — "
            "체결 후 주기 체크가 아닌 즉시 체크 필요 (lessons/20260329_3)"
        )

    # config.py에 연패 관련 상수 존재 여부 — 이름은 프로젝트마다 다름
    if config_path.exists():
        cfg_txt = config_path.read_text(encoding="utf-8")
        cfg_patterns = [
            r"MAX_CONSECUTIVE_LOSSES",
            r"MAX_CONSECUTIVE_ERRORS",
            r"VB_LOSS_COOLDOWN",
            r"LOSS_COOLDOWN_HOURS",
        ]
        if not any(re.search(p, cfg_txt, re.IGNORECASE) for p in cfg_patterns):
            warnings.append(
                "[체결안전장치] config.py에 연패 한도/쿨다운 상수 미발견 (MAX_CONSECUTIVE_* / VB_LOSS_COOLDOWN) "
                "— 연패 한도 설정 누락 위험 (lessons/20260329_3)"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증 25: CB 기존 포지션 처리 정책 명시
# ref: docs/lessons/20260408_3_cb_existing_positions_policy.md
# ═══════════════════════════════════════════════════════════════════

def check_cb_existing_positions_policy() -> None:
    """config.py에 CB 기존 포지션 정책 상수가 있거나, ADR 문서가 존재하는지 확인.

    검증규칙 (lessons/20260408_3):
    - CB 발동 시 기존 포지션 처리 정책(Option A/B/C)이 코드 또는 문서에 명시
    - config.py에 CB_EXISTING_POSITIONS_POLICY 또는 관련 상수 존재
    - docs/decisions/에 CB 포지션 정책 ADR 존재
    """
    config_path = PROJECT_ROOT / "services" / "execution" / "config.py"
    decisions_dir = PROJECT_ROOT / "docs" / "decisions"

    policy_found = False

    # config.py에서 정책 상수 탐색
    if config_path.exists():
        cfg_txt = config_path.read_text(encoding="utf-8")
        if re.search(r"CB_EXISTING_POSITIONS_POLICY|CB_LIQUIDATE|cb_existing", cfg_txt, re.IGNORECASE):
            policy_found = True

    # docs/decisions/ 에서 CB 포지션 정책 ADR 탐색
    if not policy_found and decisions_dir.exists():
        for f in decisions_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if re.search(r"cb.*existing|existing.*positions.*policy|Option [ABC]", content, re.IGNORECASE):
                policy_found = True
                break

    if not policy_found:
        warnings.append(
            "[CB포지션정책] CB 발동 시 기존 포지션 처리 정책(Option A/B/C)이 "
            "config.py 또는 docs/decisions/에 명시되지 않음 — "
            "'신규 차단'만으로는 CB 손실 보장이 불완전 (lessons/20260408_3)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 26: ONG형 고변동 하드 손절 캡 상수 존재
# ref: docs/lessons/20260408_5_ong_wide_stop.md
# ═══════════════════════════════════════════════════════════════════

def check_hard_stop_caps() -> None:
    """config.py에 HARD_STOP_LOSS_PCT (≤ 0.12) 및 MAX_ATR_PCT (≤ 0.10) 상수가 존재하고 범위가 정상인지 확인.

    검증규칙 (lessons/20260408_5):
    - config.py에 HARD_STOP_LOSS_PCT, MAX_ATR_PCT 상수 존재
    - realtime_monitor.py의 trail_stop 계산이 max(..., hard_floor) 경유
    - 진입 시 ATR 필터 통과 로그 출력 (ATR 필터 문자열)
    """
    config_path = PROJECT_ROOT / "services" / "execution" / "config.py"
    if not config_path.exists():
        warnings.append("[하드캡] services/execution/config.py 없음 — HARD_STOP_LOSS_PCT 확인 불가")
        return

    cfg_txt = config_path.read_text(encoding="utf-8")

    # HARD_STOP_LOSS_PCT 존재 + 값 범위 확인
    hard_match = re.search(r"HARD_STOP_LOSS_PCT\s*=\s*([\d.]+)", cfg_txt)
    if not hard_match:
        warnings.append(
            "[하드캡] config.py에 HARD_STOP_LOSS_PCT 상수 미정의 — "
            "ATR*3 스탑이 고변동 종목에서 통제 불능 위험 (lessons/20260408_5)"
        )
    else:
        val = float(hard_match.group(1))
        if val > 0.12:
            warnings.append(
                f"[하드캡] HARD_STOP_LOSS_PCT={val} > 0.12 — "
                "하드 손절 캡이 너무 넓음, 0.12 이하 권장 (lessons/20260408_5)"
            )

    # MAX_ATR_PCT 존재 + 값 범위 확인
    atr_match = re.search(r"MAX_ATR_PCT\s*=\s*([\d.]+)", cfg_txt)
    if not atr_match:
        warnings.append(
            "[하드캡] config.py에 MAX_ATR_PCT 상수 미정의 — "
            "고변동 종목 진입 차단 필터 누락 위험 (lessons/20260408_5)"
        )
    else:
        val = float(atr_match.group(1))
        if val > 0.10:
            warnings.append(
                f"[하드캡] MAX_ATR_PCT={val} > 0.10 — "
                "ATR 변동성 필터가 너무 관대함, 0.10 이하 권장 (lessons/20260408_5)"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증 27: 외부 API 초기화 재시도+백오프 패턴
# ref: docs/lessons/20260413_1_startup_refresh_crash.md
# ═══════════════════════════════════════════════════════════════════

def check_startup_retry_backoff() -> None:
    """realtime_monitor.py의 start() 또는 _refresh_levels 경로에 재시도+백오프 패턴이 있는지 확인.

    검증규칙 (lessons/20260413_1):
    - start() 내 _refresh_levels() 호출은 반드시 try-except로 감싸야 함
    - API 장애 시 프로세스가 크래시하지 않고 재시도해야 함
    - pre_deploy_check: start() 안에 _refresh_levels 호출 시 재시도 루프 또는 try-except 존재 확인
    """
    rm_path = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rm_path.exists():
        warnings.append("[시작재시도] realtime_monitor.py 없음 — 초기화 재시도 패턴 확인 불가")
        return

    txt = rm_path.read_text(encoding="utf-8")

    # _refresh_levels 호출이 있는지 먼저 확인
    if "_refresh_levels" not in txt:
        return  # _refresh_levels 자체가 없으면 해당 없음

    # 재시도+백오프 패턴: retry, backoff, sleep, asyncio.sleep 이 _refresh_levels와 근접 존재
    retry_patterns = [
        r"retry",
        r"backoff",
        r"asyncio\.sleep",
        r"await asyncio\.sleep",
    ]
    has_retry = any(re.search(p, txt, re.IGNORECASE) for p in retry_patterns)

    # try-except로 _refresh_levels 호출을 감싸는 패턴 확인
    # start() 함수 내에 try 블록이 _refresh_levels 전후로 존재하는지
    start_match = re.search(
        r"async def start\(.*?\n(.*?)(?=\n    async def |\nclass |\Z)",
        txt,
        re.DOTALL,
    )
    has_try_in_start = False
    if start_match:
        start_body = start_match.group(1)
        has_try_in_start = "_refresh_levels" in start_body and "try" in start_body

    if not has_retry and not has_try_in_start:
        warnings.append(
            "[시작재시도] realtime_monitor.py의 start()/_refresh_levels 경로에 "
            "재시도 루프(retry/backoff/asyncio.sleep) 또는 try-except 미발견 — "
            "API 점검 중 크래시 루프 위험 (lessons/20260413_1)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 28: 레짐 자동 전환 시스템 (P5-04)
# ═══════════════════════════════════════════════════════════════════

def check_regime_switcher_integration() -> None:
    """regime_switcher 모듈 + config 상수 + regime_check 스크립트 존재 검증.

    lessons/20260408_1 (사일런트 cron 실패) 정책에 맞춰 ERROR로 승격.
    모듈·cron 스크립트가 누락된 채 배포되면 import 실패 / cron 사일런트 실패 유발.
    """
    rs_path = PROJECT_ROOT / "services" / "execution" / "regime_switcher.py"
    rc_path = PROJECT_ROOT / "scripts" / "regime_check.py"
    cfg_path = PROJECT_ROOT / "services" / "execution" / "config.py"

    if not rs_path.exists():
        errors.append("[P5-04] services/execution/regime_switcher.py 없음 — regime_switch import 실패")
    if not rc_path.exists():
        errors.append("[P5-04] scripts/regime_check.py 없음 — 레짐 cron 사일런트 실패")
    if cfg_path.exists():
        cfg = cfg_path.read_text(encoding="utf-8")
        if "REGIME_SWITCH_ENABLED" not in cfg:
            errors.append("[P5-04] config.py에 REGIME_SWITCH_ENABLED 상수 없음")


# ═══════════════════════════════════════════════════════════════════
# 검증 29: lint_history 누적 스크립트 (P6-13)
# ═══════════════════════════════════════════════════════════════════

def check_lint_history_script() -> None:
    lh_path = PROJECT_ROOT / "scripts" / "lint_history.py"
    if not lh_path.exists():
        warnings.append("[P6-13] scripts/lint_history.py 없음")


# ═══════════════════════════════════════════════════════════════════
# 검증 30: VB 재집계 자동 트리거 (후속과제)
# ═══════════════════════════════════════════════════════════════════

def check_vb_recheck_trigger() -> None:
    """VB 재집계 cron 스크립트 + deploy 등록 검증.

    lessons/20260408_1 (사일런트 cron 실패) 정책에 따라 ERROR로 승격.
    """
    vb_path = PROJECT_ROOT / "scripts" / "vb_recheck_trigger.py"
    if not vb_path.exists():
        errors.append("[VB-재집계] scripts/vb_recheck_trigger.py 없음 — cron 사일런트 실패")
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if d_path.exists():
        dtxt = d_path.read_text(encoding="utf-8")
        if "vb_recheck_trigger.py" not in dtxt:
            errors.append("[VB-재집계] deploy_to_aws.sh에 vb_recheck_trigger cron 등록 없음")


# ═══════════════════════════════════════════════════════════════════
# 검증 31: 배포 도구 가용성 (lessons/20260419_1)
# ═══════════════════════════════════════════════════════════════════

def check_deploy_tooling() -> None:
    """로컬 환경에 배포에 필요한 CLI 도구가 있는지 + 폴백 분기 유지 여부.

    ssh 는 필수. rsync 또는 tar 중 하나는 반드시 있어야 한다.
    deploy_to_aws.sh 에 rsync→tar 폴백 분기가 보존되어 있는지 검사.
    ref: lessons/20260419_1_rsync_missing_deploy_stall.md
    """
    import shutil

    if shutil.which("ssh") is None:
        errors.append("[배포툴] ssh 바이너리 없음 — 배포 불가")

    has_rsync = shutil.which("rsync") is not None
    has_tar = shutil.which("tar") is not None
    if not has_rsync and not has_tar:
        errors.append("[배포툴] rsync/tar 모두 없음 — 원격 전송 수단 부재")
    elif not has_rsync and has_tar:
        warnings.append("[배포툴] rsync 없음 — deploy_to_aws.sh 의 tar 폴백 경로로 동작")

    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if d_path.exists():
        dtxt = d_path.read_text(encoding="utf-8")
        if "command -v rsync" not in dtxt or "tar czf" not in dtxt:
            errors.append("[배포툴] deploy_to_aws.sh 에 rsync→tar 폴백 분기 누락 — lessons/20260419_1")


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
    check_monitoring_hooks()
    check_watchdog_script()
    check_service_watchdog_sec()
    check_deploy_cron_registered()
    check_balance_log_throttle()
    check_filter_stats_integration()
    check_daily_report_filter_section()
    check_meta_lint_script()
    check_bar_based_entry()
    check_backtest_regime_split()
    check_post_fill_safety_check()
    check_cb_existing_positions_policy()
    check_hard_stop_caps()
    check_startup_retry_backoff()
    check_regime_switcher_integration()
    check_lint_history_script()
    check_vb_recheck_trigger()
    check_deploy_tooling()

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
