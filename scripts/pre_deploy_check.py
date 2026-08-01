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
# 검증 1b: MIN_VOLUME_KRW 범위 + CLAUDE.md 동기화 (ADR 20260607-1, lessons #4)
# ═══════════════════════════════════════════════════════════════════

# 허용 범위: 2억(완화 하한) ~ 20억(보수 상한). 범위 밖이면 오타/단위 사고로 간주.
MIN_VOLUME_KRW_FLOOR = 200_000_000
MIN_VOLUME_KRW_CEIL = 2_000_000_000


def check_min_volume_krw_range() -> None:
    """MIN_VOLUME_KRW 값이 합리 범위 내인지 + CLAUDE.md 종목풀 언급과 동기화되었는지 검증.

    ADR 20260607-1: 3억 → 5억 환원. 이후 config↔CLAUDE.md 종목풀 표기 drift 차단 (lessons #4).
    """
    config_file = PROJECT_ROOT / "services" / "execution" / "config.py"
    claude_file = PROJECT_ROOT / "CLAUDE.md"

    if not config_file.exists():
        errors.append("[종목필터] config.py 파일 없음")
        return

    config_content = config_file.read_text(encoding="utf-8")
    # 주석/할당의 우변 첫 숫자(언더스코어 포함) 추출
    mv_match = re.search(
        r"^MIN_VOLUME_KRW\s*=\s*([\d_]+)", config_content, re.MULTILINE
    )
    if not mv_match:
        errors.append("[종목필터] config.py에서 MIN_VOLUME_KRW 할당을 찾을 수 없음")
        return

    try:
        mv = int(mv_match.group(1).replace("_", ""))
    except ValueError:
        errors.append(f"[종목필터] MIN_VOLUME_KRW 값 파싱 실패: {mv_match.group(1)}")
        return

    if not (MIN_VOLUME_KRW_FLOOR <= mv <= MIN_VOLUME_KRW_CEIL):
        errors.append(
            f"[종목필터] MIN_VOLUME_KRW={mv:,} 가 허용 범위 밖 "
            f"({MIN_VOLUME_KRW_FLOOR:,} ~ {MIN_VOLUME_KRW_CEIL:,}) — "
            f"단위/오타 의심 (예: 0 누락/초과)"
        )

    # CLAUDE.md 종목풀 언급과 단위(억) 동기화 검증 (lessons #4)
    if claude_file.exists():
        claude_content = claude_file.read_text(encoding="utf-8")
        mv_eok = mv // 100_000_000  # 억 단위
        # MIN_VOLUME_KRW 가 언급된 줄(들)에서 "N억" 표기를 모두 수집.
        # (한 줄에 "3억 → 5억" 처럼 여러 표기가 있을 수 있어 줄 단위로 findall)
        eok_mentions = []
        for line in claude_content.splitlines():
            if "MIN_VOLUME_KRW" in line:
                eok_mentions.extend(re.findall(r"(\d+)\s*억", line))
        if eok_mentions:
            # CLAUDE.md에 현재 config 억수가 한 번도 등장하지 않으면 drift 경고
            if str(mv_eok) not in eok_mentions:
                warnings.append(
                    f"[종목필터] config MIN_VOLUME_KRW={mv_eok}억 vs "
                    f"CLAUDE.md 표기 {eok_mentions}억 — 종목풀 표기 drift 확인 필요 (lessons #4)"
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


def check_cron_var_echo_consistency() -> None:
    """deploy_to_aws.sh의 활성 CRON_xxx 변수 정의와 echo 등록의 1:1 일관성 검증.

    배경: lessons #31 — 5/14에 ml_weekly_review/ml_outcome_match cron 변수만 추가하고
    echo 블록 등록 누락 시, deploy 직접 실행해도 cron이 등록 안 됨 (silent fail).
    또한 scp+restart 우회 시에는 deploy_to_aws.sh 자체가 안 돌므로 G6 패치 효과 없음 —
    하지만 적어도 deploy 스크립트의 내적 정합성은 항상 보장해야 한다.

    검증 규칙:
      1. ^CRON_(\\w+)= 매칭 → 활성 변수 추출 (주석 라인 제외)
      2. echo "$CRON_\\w+" 매칭 → echo 등록 변수 추출
      3. 정의됐는데 echo 안 된 변수 = errors (lessons #31 시나리오)
      4. echo 됐는데 정의 안 된 변수 = errors (오타/리네임 누락)

    ref: docs/lessons/20260520_1_cron_registration_missing.md
    ref: docs/lessons/20260524_1_g6_deploy_guard.md
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")

    defined: set[str] = set()
    for raw_line in txt.splitlines():
        # 라인 앞 공백 제외 후 # 로 시작하면 주석 → 비활성 변수로 간주
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue
        m = re.match(r'^([A-Z_]*CRON_[A-Z0-9_]+)\s*=', stripped)
        if m:
            defined.add(m.group(1))

    # echo 등록 추출 — `echo "$CRON_XXX"` 패턴
    # 주석 라인의 echo도 제외 (예: "# CRON_DIGEST 비활성" 코멘트)
    echoed: set[str] = set()
    for raw_line in txt.splitlines():
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue
        for m in re.finditer(r'echo\s+"\$([A-Z_]*CRON_[A-Z0-9_]+)"', raw_line):
            echoed.add(m.group(1))

    missing_echo = defined - echoed
    missing_def = echoed - defined

    if missing_echo:
        errors.append(
            "[CRON정합] deploy_to_aws.sh에 정의됐으나 echo 블록에 등록 안 된 변수: "
            f"{sorted(missing_echo)} — lessons #31 시나리오 (cron silent fail). "
            "echo \"$VAR\" 라인을 crontab 등록 파이프에 추가하세요."
        )
    if missing_def:
        errors.append(
            "[CRON정합] deploy_to_aws.sh에 echo 됐으나 정의 안 된 변수: "
            f"{sorted(missing_def)} — 오타 또는 리네임 누락. "
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
# 검증: SSH 표준 키 경로 존재 + 표준 문서 존재 (lessons #35)
# ref: docs/lessons/20260603_1_ssh_key_path_subagent_drift.md
# expert/operator subagent에서 SSH 키 경로 추측 → Permission denied 차단
# ═══════════════════════════════════════════════════════════════════

def check_ssh_canonical_key() -> None:
    """canonical SSH 키 경로 존재 + 표준 문서 등재 + deploy 스크립트 일치 검증."""
    import os

    # 1. canonical 키 경로 (deploy_to_aws.sh와 동일 — $HOME/Downloads/upbit-trading-key-seoul.pem)
    home = os.path.expanduser("~")
    canonical_key = Path(home) / "Downloads" / "upbit-trading-key-seoul.pem"
    if not canonical_key.exists():
        # 로컬 PC 외(subagent/CI) 환경에서는 키 부재가 정상일 수 있음 — warning만
        warnings.append(
            f"[SSH] canonical 키 파일 없음: {canonical_key} "
            "— 로컬 배포 환경에서는 필수. subagent라면 docs/ssh_access.md 참조"
        )
    else:
        # 권한 체크 (Linux/Mac: 0400/0600, Windows: ACL은 OS가 관리)
        if os.name != "nt":
            mode = canonical_key.stat().st_mode & 0o777
            if mode not in (0o400, 0o600):
                warnings.append(
                    f"[SSH] 키 권한 {oct(mode)} — 권장 0400 또는 0600 "
                    "(Linux/Mac에서 0644 등 group/other 읽기 가능 시 ssh가 거부)"
                )

    # 2. 표준 문서 존재 확인 (subagent가 추측 없이 따를 수 있도록)
    ssh_doc = PROJECT_ROOT / "docs" / "ssh_access.md"
    if not ssh_doc.exists():
        errors.append(
            "[SSH] docs/ssh_access.md 없음 — subagent/operator가 키 경로/사용자명 추측 차단 불가 (lessons #35)"
        )

    # 3. deploy_to_aws.sh의 PEM_KEY 변수가 canonical 경로와 동일한지
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if d_path.exists():
        dtxt = d_path.read_text(encoding="utf-8")
        if "upbit-trading-key-seoul.pem" not in dtxt:
            errors.append(
                "[SSH] deploy_to_aws.sh가 canonical PEM 파일명을 참조하지 않음 — 표준 분기"
            )


# ═══════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# 검증: 헬스체크 모듈 import 가능성 + critical cron 등록
# ref: workspace/plans/20260502_reporting_system_overhaul.md
# ref: docs/lessons/20260502_1_upbit_keyset_ip_mapping.md (#20)
# ═══════════════════════════════════════════════════════════════════

def check_healthcheck_module() -> None:
    """services.healthcheck 모듈이 정상 import 가능한지 검증."""
    try:
        # sys.path 보장 (runner.py 자체에도 보강 있지만 이중 안전)
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from services.healthcheck.runner import (
            check_auth, check_jarvis_cron, run_all, build_health_section,
        )
        # callable 검증
        for fn in (check_auth, check_jarvis_cron, run_all, build_health_section):
            if not callable(fn):
                errors.append(f"[헬스체크] {fn} not callable")
    except Exception as e:
        errors.append(f"[헬스체크] services.healthcheck 모듈 import 실패: {e}")


def check_critical_healthcheck_cron() -> None:
    """deploy_to_aws.sh에 critical_healthcheck cron 등록 여부 + 09:10 daily_report 제거 여부."""
    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not deploy.exists():
        warnings.append("[critical] deploy_to_aws.sh 없음")
        return
    content = deploy.read_text(encoding="utf-8")
    if "critical_healthcheck.py" not in content:
        errors.append(
            "[critical] deploy_to_aws.sh에 critical_healthcheck.py cron 미등록 "
            "(plan 20260502 P0 #5)"
        )
    # 09:10 KST = "10 0 * * * ..." daily_report 패턴이 잔존하면 안 됨
    if re.search(r'CRON_REPORT="10\s+0\s+\*', content):
        errors.append(
            "[critical] deploy_to_aws.sh에 09:10 KST daily_report cron 잔존 "
            "(plan 20260502 P1: 18:00 단일화)"
        )
    # 스크립트 자체 존재 확인
    crit = PROJECT_ROOT / "scripts" / "critical_healthcheck.py"
    if not crit.exists():
        errors.append("[critical] scripts/critical_healthcheck.py 파일 없음")


def check_strategy_enhancement_config() -> None:
    """plan 20260504_2 AC16 — 신규 전략 보강 config 키 존재 검증."""
    cfg = PROJECT_ROOT / "services" / "execution" / "config.py"
    if not cfg.exists():
        return
    content = cfg.read_text(encoding="utf-8")
    required_keys = [
        "TP_LEVELS", "TP_ENABLED",
        "VOL_FILTER_ENABLED", "VOL_FILTER_MULTIPLIER",
        "DAILY_LOSS_LIMIT_ENABLED", "DAILY_LOSS_LIMIT_PCT", "DAILY_LOSS_BASE_KRW",
    ]
    for k in required_keys:
        if k not in content:
            errors.append(f"[전략보강] config.py에 {k} 미정의 (plan 20260504_2)")


def check_hourly_digest_cron() -> None:
    """plan 20260503_4 P4-2 (cto #2): hourly_digest cron 등록 + 스크립트 존재 검증.

    lessons #9 정면 위반 방지 — 자동화 전제 cron은 pre_deploy_check 검증 필수.
    """
    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not deploy.exists():
        return
    content = deploy.read_text(encoding="utf-8")
    if "hourly_digest.py" not in content:
        errors.append(
            "[digest] deploy_to_aws.sh에 hourly_digest.py cron 미등록 (plan 20260503_4 P4-2)"
        )
    digest = PROJECT_ROOT / "scripts" / "hourly_digest.py"
    if not digest.exists():
        errors.append("[digest] scripts/hourly_digest.py 파일 없음")


def check_deploy_log_files() -> None:
    """plan 20260503 P1 (AC19): deploy_to_aws.sh의 LOG_FILES 배열에 cron 라인의 모든 로그가 포함되어야 함.

    silent fail 방지 (lessons #18) — cron의 stderr→로그 리디렉션이 파일 미생성 시 실패.
    LOG_FILES = (...) 내용을 추출하고, 모든 CRON_* 라인의 ">> /var/log/X.log" 경로가 포함되었는지 검증.
    """
    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not deploy.exists():
        return
    content = deploy.read_text(encoding="utf-8")

    # LOG_FILES 배열 추출
    m = re.search(r"LOG_FILES=\(([^)]+)\)", content)
    if not m:
        warnings.append("[배포로그] deploy_to_aws.sh에서 LOG_FILES=(...) 추출 실패")
        return
    log_files = set(re.findall(r"/var/log/[\w.]+\.log", m.group(1)))

    # 모든 cron 라인의 redirect 경로 추출
    cron_logs = set(re.findall(r">>\s+(/var/log/[\w.]+\.log)", content))

    missing = cron_logs - log_files
    if missing:
        errors.append(
            f"[배포로그] LOG_FILES 배열에 누락된 로그 파일: {sorted(missing)} "
            f"(cron이 redirect 시 silent fail — lessons #18)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증: ML 신호 필터 무결성 (plans/20260504_3_ml_signal_filter)
# ═══════════════════════════════════════════════════════════════════

def check_ml_filter_integrity() -> None:
    """ML 신호 필터의 무결성:
       1) services/ml/config.py 단일 출처 존재
       2) current.pkl 있으면 current.meta.json 도 있어야 함
       3) meta의 feature_columns가 config.FEATURE_COLUMNS와 정확히 일치
       4) meta의 threshold가 0~1 범위
       5) ML_FILTER_ENABLED=1인데 모델 부재 → fail-open 경고
    """
    import json

    ml_cfg = PROJECT_ROOT / "services" / "ml" / "config.py"
    if not ml_cfg.exists():
        warnings.append("[ML] services/ml/config.py 없음 — ML 미도입 환경")
        return

    cfg_text = ml_cfg.read_text(encoding="utf-8")
    if "FEATURE_COLUMNS" not in cfg_text:
        errors.append("[ML] services/ml/config.py FEATURE_COLUMNS 정의 누락")
        return

    pkl = PROJECT_ROOT / "data" / "models" / "current.pkl"
    meta = PROJECT_ROOT / "data" / "models" / "current.meta.json"

    if pkl.exists() and not meta.exists():
        errors.append("[ML] current.pkl 존재하지만 current.meta.json 없음 — 학습/배포 누락")
        return

    if meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"[ML] current.meta.json 파싱 실패: {e}")
            return
        # threshold 범위
        thr = m.get("threshold")
        if thr is None or not (0.0 < float(thr) < 1.0):
            errors.append(f"[ML] meta threshold 비정상: {thr}")
        # feature 카탈로그 일치
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from services.ml.config import FEATURE_COLUMNS as _CFG_FEATS  # noqa: WPS433
            meta_feats = m.get("feature_columns", [])
            if meta_feats != list(_CFG_FEATS):
                errors.append(
                    f"[ML] feature 카탈로그 mismatch: meta {len(meta_feats)} vs config {len(_CFG_FEATS)} "
                    "→ MLFilter는 fail-open이지만 학습/추론 drift 직결, 즉시 재학습 필요"
                )
        except Exception as e:
            warnings.append(f"[ML] feature 카탈로그 검증 스킵 (import 실패): {e}")
        # CV 메트릭 sanity
        cv = m.get("cv_metrics", {})
        if cv:
            auc = cv.get("mean_auc", 0)
            if auc < 0.5:
                warnings.append(f"[ML] mean_auc={auc:.3f} < 0.5 — 모델이 random보다 못함, 배포 신중")

    # ML_FILTER_ENABLED=1 인데 pkl 없음 → critical (운영에서 차단 의도가 무효화됨은 아니지만 fail-open)
    import os
    if os.getenv("ML_FILTER_ENABLED") == "1" and not pkl.exists():
        warnings.append(
            "[ML] ML_FILTER_ENABLED=1 인데 current.pkl 없음 — fail-open 모드로 동작 (의도가 맞는지 확인)"
        )

    # 모든 매수 경로에 ML hook이 있는지 확인 (lessons #6 위배 방지)
    for path_rel in ("services/execution/multi_trader.py", "services/execution/realtime_monitor.py"):
        p = PROJECT_ROOT / path_rel
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        if "_get_ml_filter" not in src and "get_filter" not in src:
            errors.append(
                f"[ML] {path_rel}에 ML 필터 hook 누락 — lessons #6 (모든 매수 경로 필터 적용) 위배"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증: 시스템 메모리/swap 압박 (lessons #5, ML 활성 후 +210MB RSS)
# ═══════════════════════════════════════════════════════════════════

def check_system_memory() -> None:
    """t3.micro 1GB 환경에서 free RAM/swap 사용률 점검.
       AWS 환경에서만 의미 있음. 로컬 실행 시 자동 skip.
    """
    import shutil
    if not shutil.which("free"):
        return  # 윈도우 등 free 미설치 환경

    try:
        import subprocess
        out = subprocess.check_output(["free", "-m"], text=True, timeout=5)
    except Exception:
        return

    lines = out.strip().split("\n")
    mem_line = next((l for l in lines if l.startswith("Mem:")), None)
    swap_line = next((l for l in lines if l.startswith("Swap:")), None)
    if not mem_line:
        return

    mem_parts = mem_line.split()
    # Mem: total used free shared buff/cache available
    if len(mem_parts) >= 7:
        total = int(mem_parts[1])
        avail = int(mem_parts[6])
        if total < 2048:  # t3.micro 등 소형 인스턴스만 체크
            if avail < 100:
                errors.append(
                    f"[메모리] 가용 RAM={avail}MB / total={total}MB — OOM 위험 임박 (lessons #5)"
                )
            elif avail < 200:
                warnings.append(
                    f"[메모리] 가용 RAM={avail}MB / total={total}MB — ML+cron 동시 실행 시 압박"
                )

    if swap_line:
        sw_parts = swap_line.split()
        if len(sw_parts) >= 3:
            sw_total = int(sw_parts[1])
            sw_used = int(sw_parts[2])
            if sw_total > 0 and sw_used > sw_total * 0.8:
                warnings.append(
                    f"[메모리] swap 사용 {sw_used}/{sw_total}MB ({100*sw_used//sw_total}%) — t3.micro 압박"
                )


# ═══════════════════════════════════════════════════════════════════
# 검증: 좀비 봇 프로세스 (lessons #27)
# ═══════════════════════════════════════════════════════════════════

def check_zombie_bot_processes() -> None:
    """BitCoin_Trade cwd로 가동 중인 daily_live.py 인스턴스가 정상 1개뿐인지 검증.
       --realtime 1개(systemd) + non-realtime 0개(즉시 종료 가정)가 정상.
       좀비 누적 = lessons #27 회귀 (옛 코드 알림 발사 / crontab 갱신 누락).
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "daily_live.py"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # pgrep 미설치(Windows) 또는 매치 없음 = skip
    except Exception:
        return

    lines = [l for l in out.strip().split("\n") if "BitCoin_Trade" in l]
    if not lines:
        return  # 로컬 (BitCoin_Trade 프로세스 없음)
    realtime = [l for l in lines if "--realtime" in l]
    non_realtime = [l for l in lines if "--realtime" not in l]
    if len(realtime) > 1:
        errors.append(
            f"[좀비] daily_live.py --realtime {len(realtime)}개 (1개여야 함, lessons #27)"
        )
    # lessons #33 (2026-06-02): non_realtime 좀비 ≥ 3 시 ERROR 승격.
    # cron 재등록(5 0 * * *)으로 매일 1개씩 누적된 사고 (8개 발견).
    if len(non_realtime) >= 3:
        errors.append(
            f"[좀비] daily_live.py (no --realtime) {len(non_realtime)}개 — "
            "누적 좀비 ERROR 임계 초과 (lessons #33, cron 재등록 회귀 의심)"
        )
    elif non_realtime:
        warnings.append(
            f"[좀비] daily_live.py (no --realtime) {len(non_realtime)}개 — 누적 좀비 의심 (lessons #27/#33)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증: deploy_to_aws.sh에 daily_live.py (no --realtime) cron 등록 차단
# ref: docs/lessons/20260602_1_cron_zombie_relapse_no_realtime.md (lessons #33)
# ═══════════════════════════════════════════════════════════════════

def check_deploy_no_daily_live_cron() -> None:
    """deploy_to_aws.sh가 daily_live.py (no --realtime)를 cron으로 등록하면 ERROR.

    배경: 2026-06-02 lessons #33 — crontab에 `5 0 * * * .../daily_live.py >>`
    라인이 있어 매일 새 인스턴스 생성, 일부가 종료 안 되어 좀비 8개 누적.
    systemd btc-trader.service가 --realtime을 항상 가동하므로 cron 호출은 금지.
    """
    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not deploy.exists():
        return
    content = deploy.read_text(encoding="utf-8")
    # cron 표현식과 daily_live.py가 같은 라인에 있고 --realtime이 없으면 ERROR
    # 단, 주석(#) 또는 no-op 라인(:로 시작 — sh의 echo 비활성 패턴)은 제외
    cron_re = re.compile(r"\d+\s+\d+(?:\s+\S+){3}.*daily_live\.py")
    for line in content.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # `: "echo $CRON_LIVE ..."` 같은 no-op 무시
        if stripped.startswith(":"):
            continue
        if "daily_live.py" not in line:
            continue
        if "--realtime" in line:
            continue
        if cron_re.search(line):
            errors.append(
                "[lessons #33] deploy_to_aws.sh에 daily_live.py (no --realtime) cron 등록 라인 잔존 — "
                "좀비 누적 위험 (systemd 단독 가동만 허용)"
            )
            return


# ═══════════════════════════════════════════════════════════════════
# 검증 X1: entry_qty=0 저장 invariant (lessons #25 강화)
# ref: docs/lessons/20260524_2_state_qty_zero_and_cron_loss.md
# ═══════════════════════════════════════════════════════════════════

def check_entry_qty_invariant() -> None:
    """모든 매수 경로(realtime_monitor.py / multi_trader.py)의 entry_qty 결정 로직에
    fallback 체인과 invariant 가드가 있는지.

    배경: 2026-05-24 발견 — 업비트 시장가 매수 시 order["amount"]=None이면
    float(None) → TypeError → entry_qty=0 저장. 5/5 MTL 케이스: entry_qty=0이라
    부분 TP 수량 산정에서 cur_total fallback으로 매도는 됐지만 state는 영구히
    잔량 정합 불일치 (entry_qty=0, remaining_qty=93 vs 거래소 46).

    2026-05-24 P2-1 보강 (lessons #6 정신):
    P1 close 직후 발견 — scanner 경로(multi_trader.py:245)에도 동일 로직 필요.
    한 경로만 가드하면 다른 경로에서 동형 회귀 가능. 모든 매수 경로를 강제 검증.

    검증규칙 (lessons #25/#32 강화):
      각 매수 경로마다:
        - order.filled 또는 order.amount 외에도 fetch_balance fallback 존재
        - entry_qty <= 0 invariant 가드 (placeholder 또는 경고 로그)
        - positions[symbol] dict에 entry_qty / entry_amount_krw / tp_sold_levels 저장
    """
    # 검증 대상: (파일경로, 라벨)
    targets = [
        (PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py", "realtime_monitor"),
        (PROJECT_ROOT / "services" / "execution" / "multi_trader.py", "multi_trader(scanner)"),
    ]
    for path, label in targets:
        if not path.exists():
            continue
        txt = path.read_text(encoding="utf-8")
        # 옛 형태 (한 줄 표현식) → 회귀 의심
        if re.search(r'entry_qty\s*=\s*float\(\(order\s+or\s+\{\}\)\.get\("amount"\)', txt):
            errors.append(
                f"[entry_qty-invariant:{label}] 옛 entry_qty 결정 로직(float(None) 위험) 잔존 — "
                "fetch_balance fallback 누락 (lessons #25/#32 강화, 2026-05-24)"
            )
            continue
        # entry_qty 결정 블록 + positions[symbol] dict 본문 추출
        # (entry_qty = ... ~ "save_state" 또는 다음 함수 정의 직전까지)
        block_match = re.search(
            r"entry_qty\s*=\s*0(?:\.0)?.*?positions\[symbol\]\s*=\s*\{[^}]*\}",
            txt, re.DOTALL
        )
        if not block_match:
            errors.append(
                f"[entry_qty-invariant:{label}] entry_qty 결정 블록 미발견 — "
                "scanner 매수 경로에 fallback 체인 누락 (lessons #6/#32, 2026-05-24)"
            )
            continue
        block = block_match.group(0)
        # fallback 체인 검증
        if "filled" not in block:
            errors.append(
                f"[entry_qty-invariant:{label}] entry_qty 결정 블록에 order.filled fallback 누락 "
                "(order.amount는 시장가 매수에서 None일 수 있음, lessons #25 강화)"
            )
        if "fetch_balance" not in block and "_bal_raw" not in block:
            errors.append(
                f"[entry_qty-invariant:{label}] entry_qty 결정 블록에 fetch_balance fallback 누락 — "
                "order 정보가 모두 None일 때 거래소 실잔량 미러링 불가"
            )
        # invariant 가드: entry_qty <= 0 체크
        if not re.search(r"entry_qty\s*<=?\s*0", block):
            warnings.append(
                f"[entry_qty-invariant:{label}] entry_qty <=0 invariant 가드 미발견 — "
                "0 저장 시 부분 TP 수량 산정 어긋남 위험"
            )
        # state 저장 키 검증 (lessons #32 후속 — entry_amount_krw / tp_sold_levels 동시 저장)
        if "entry_amount_krw" not in block:
            errors.append(
                f"[entry_qty-invariant:{label}] positions[symbol] 저장에 entry_amount_krw 누락 — "
                "부분 TP 잔량 회계 기준 누락 (lessons #25/#32, 2026-05-24)"
            )
        if "tp_sold_levels" not in block:
            warnings.append(
                f"[entry_qty-invariant:{label}] positions[symbol] 저장에 tp_sold_levels 미초기화 — "
                "단계별 매도 추적 KeyError 위험 (lessons #25)"
            )


# ═══════════════════════════════════════════════════════════════════
# 검증 X2: fix_state_balance_mismatch.py 잔량 미러링 (lessons #10/#25)
# ref: docs/lessons/20260524_2_state_qty_zero_and_cron_loss.md
# ═══════════════════════════════════════════════════════════════════

def check_state_qty_mirror_in_fix() -> None:
    """scripts/fix_state_balance_mismatch.py가 종목 add/remove 외에 잔량(qty) 미러링도
    수행하는지 검증.

    배경: 기존 fix는 종목 존재 유무만 확인 → state.qty=0 + 거래소.qty=46 같은
    잔량 drift는 통과. lessons #10 "state는 거래소 미러" 원칙 미충족.
    """
    f_path = PROJECT_ROOT / "scripts" / "fix_state_balance_mismatch.py"
    if not f_path.exists():
        warnings.append(
            "[state-mirror] scripts/fix_state_balance_mismatch.py 없음"
        )
        return
    txt = f_path.read_text(encoding="utf-8")
    if "qty_fixes" not in txt and "qty 미러" not in txt and "잔량 미러" not in txt:
        errors.append(
            "[state-mirror] fix_state_balance_mismatch.py에 잔량 미러링 로직 없음 — "
            "종목 일치 시 잔량(qty) drift 통과 위험 (lessons #10/#25 강화, 2026-05-24)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증 X3: deploy_to_aws.sh BitCoin cron 최소 카운트 (lessons #31 강화)
# ref: docs/lessons/20260524_2_state_qty_zero_and_cron_loss.md
# ═══════════════════════════════════════════════════════════════════

def check_btc_cron_count_baseline() -> None:
    """deploy_to_aws.sh가 등록하는 BitCoin cron 변수가 최소 baseline 이상인지.

    배경: 2026-05-24 발견 — AWS crontab에 BitCoin cron이 ml_outcome/ml_weekly_review
    2개만 남고 watchdog/critical/regime/daily_report 등 6개 누락. 원인은 hotfix scp
    또는 다른 프로젝트 deploy가 crontab 통째 갱신 시 BitCoin 라인 미보존 가능성.
    deploy_to_aws.sh 자체는 8+ 개를 등록해야 하므로, 변수 활성 개수가 baseline 이상이어야 함.

    Baseline (2026-05-24 기준): CRON_LIVE/REPORT_18/WATCHDOG/LOGVOL/VB_RECHECK/REGIME/
    CRITICAL/ML_OUTCOME/ML_WEEKLY = 9개 (CRON_DIGEST/JARVIS는 비활성 주석).
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    # 활성(주석 아닌) CRON_xxx 변수 카운트
    active = set()
    for raw in txt.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        m = re.match(r'^(CRON_[A-Z0-9_]+)\s*=', stripped)
        if m:
            active.add(m.group(1))
    BASELINE = 9  # lessons #38 (2026-08-01): 아침 브리핑 신설로 8→9 상향, 서버 실측 게이트와 통일
    if len(active) < BASELINE:
        errors.append(
            f"[cron-baseline] deploy_to_aws.sh 활성 CRON_xxx 변수 {len(active)}개 "
            f"(baseline {BASELINE}+ 필요) — 핵심 BitCoin cron 누락 의심 "
            f"(lessons #24/#31 강화, 2026-05-24): {sorted(active)}"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증: 모든 매수 경로에 ML 게이트 라인 인접 hook (lessons #36)
# ═══════════════════════════════════════════════════════════════════

def check_all_buy_paths_ml_gate() -> None:
    """buy_market_coin( 호출 전 100 라인 이내에 ML 게이트 호출이 있어야 함.

    배경 (lessons #36, 2026-06-03):
        파일 단위 hook 검증(check_ml_filter_integrity)은 한 파일에 N개 매수 분기가
        있을 때 일부 누락을 놓침 — 실제로 EMA-TREND(realtime_monitor.py:1223)는
        같은 파일 내 DC 경로(1956)에 ML hook이 있다는 이유로 검증 통과했지만
        EMA-TREND 자체에는 hook 부재. 진입 함수 단위 라인 인접성 검증 필수.

    검증: services/execution/*.py 의 buy_market_coin( 호출 모두 찾아서
        호출 라인의 직전 100 라인 내에 _get_ml_filter() 또는 get_filter() 호출
        그리고 _ml_pass 분기가 있어야 함.

    ───────────────────────────────────────────────────────────────
    B9 분리 배포 임시 가드 (2026-06-03):
        본 룰은 B7/B8 묶음(realtime_monitor.py ML hook + fail-closed)을 강제하는데,
        cto P3 review에서 B7/B8 FAIL 판정으로 재작성 필요. B9(5연패 watchdog 회피)는
        독립 시급 사안(cooldown_until 만료 6/4 12:04 KST 전 배포)이라 단독 분리 배포.
        환경변수 B7B8_REVIEWED=1 시에만 ERROR. 미설정/0 시 WARNING으로 격하.
        B7/B8 재작성 + cto 재승인 후 B7B8_REVIEWED=1 영구 적용 + 본 가드 제거.
    ───────────────────────────────────────────────────────────────
    """
    import os as _os
    b7b8_reviewed = _os.getenv("B7B8_REVIEWED", "0") == "1"
    targets = [
        PROJECT_ROOT / "services" / "execution" / "multi_trader.py",
        PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py",
    ]
    LOOKBACK = 100
    for p in targets:
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            # buy_market_coin 호출 (정의 def buy_market_coin 제외)
            if "buy_market_coin(" not in line:
                continue
            stripped = line.lstrip()
            if stripped.startswith("def ") or stripped.startswith("from ") \
                    or stripped.startswith("import "):
                continue
            # 직전 LOOKBACK 라인 윈도우
            window = "\n".join(lines[max(0, i - LOOKBACK):i])
            has_filter_call = ("_get_ml_filter()" in window) or ("get_filter()" in window)
            has_pass_branch = ("_ml_pass" in window) or ("ml_pass" in window)
            if not (has_filter_call and has_pass_branch):
                rel = p.relative_to(PROJECT_ROOT).as_posix()
                msg = (
                    f"[ML-gate-path] {rel}:{i+1} buy_market_coin 호출 직전 {LOOKBACK}라인 내 "
                    f"ML 게이트 hook 누락 (filter_call={has_filter_call}, "
                    f"pass_branch={has_pass_branch}) — lessons #36 (B7/B8) 위배"
                )
                if b7b8_reviewed:
                    errors.append(msg)
                else:
                    warnings.append(
                        msg + " [B7B8_REVIEWED=0 임시 격하 — B9 분리 배포]"
                    )


# ═══════════════════════════════════════════════════════════════════
# 검증: ML 게이트 fail-closed 정책 (lessons #36 B8)
# ═══════════════════════════════════════════════════════════════════

def check_ml_failopen_policy() -> None:
    """ML 게이트가 fail-closed 분기를 갖춰야 함.

    배경 (lessons #36, 2026-06-03):
        inference.py의 passes()는 is_active=False일 때 무조건 True 반환(fail-open).
        매수 경로(multi_trader.py / realtime_monitor.py)에서는 LIVE 모드(ML_FILTER_ENABLED=1
        AND ML_SHADOW_MODE=0)일 때 모델 로드 실패 시 차단해야 함.

    검증:
        1) inference.py에 ML_SHADOW_MODE/ML_FILTER_ENABLED 분기 존재
        2) 매수 경로 파일에 fail-CLOSED 또는 fail_closed 또는 _ml_pass = False 분기 존재
           (is_active=False 경로에서)

    ───────────────────────────────────────────────────────────────
    B9 분리 배포 임시 가드 (2026-06-03):
        본 룰은 B8(realtime_monitor.py fail-closed)를 강제하는데 cto FAIL.
        B7B8_REVIEWED=1 시에만 ERROR. 미설정/0 시 WARNING으로 격하.
        B7/B8 재작성 + cto 재승인 후 B7B8_REVIEWED=1 영구 적용 + 본 가드 제거.
    ───────────────────────────────────────────────────────────────
    """
    import os as _os
    b7b8_reviewed = _os.getenv("B7B8_REVIEWED", "0") == "1"
    inf = PROJECT_ROOT / "services" / "ml" / "inference.py"
    if inf.exists():
        txt = inf.read_text(encoding="utf-8")
        if "ML_SHADOW_MODE" not in txt:
            msg = (
                "[ML-failclosed] services/ml/inference.py에 ML_SHADOW_MODE 분기 부재 "
                "— shadow/LIVE 구분 불가 (lessons #36 B8)"
            )
            if b7b8_reviewed:
                errors.append(msg)
            else:
                warnings.append(msg + " [B7B8_REVIEWED=0 임시 격하]")

    for rel in ("services/execution/multi_trader.py", "services/execution/realtime_monitor.py"):
        p = PROJECT_ROOT / rel
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        # fail-CLOSED 또는 _ml_pass = False 패턴이 있어야 함
        has_failclosed = (
            "fail-CLOSED" in txt or "fail-closed" in txt or "fail_closed" in txt
            or "_ml_pass = False" in txt
        )
        # LIVE 분기 (ML_FILTER_ENABLED 환경변수 직접 체크 — passes() 위임만으론 부족)
        has_live_gate = "ML_FILTER_ENABLED" in txt and "ML_SHADOW_MODE" in txt
        if not (has_failclosed and has_live_gate):
            msg = (
                f"[ML-failclosed] {rel} fail-CLOSED 분기 부재 "
                f"(failclosed_marker={has_failclosed}, live_gate={has_live_gate}) "
                f"— lessons #36 B8 위배 (모델 로드 실패 시 매수 차단 필요)"
            )
            if b7b8_reviewed:
                errors.append(msg)
            else:
                warnings.append(msg + " [B7B8_REVIEWED=0 임시 격하 — B9 분리 배포]")


def check_consec_loss_no_running_false() -> None:
    """5연패 분기에서 self.running=False 사용 금지 (lessons #37 B9).

    배경 (2026-06-02 사고):
        realtime_monitor._send_periodic_report()의 5연패 분기가 self.running=False를
        실행하는데, systemd Restart=always + WatchdogSec=5min 환경에서 sd_notify
        STOPPING=1 누락 → watchdog timeout → SIGABRT(6) → 자동 재시작으로 안전장치 무력화.

    조치:
        5연패 분기는 cooldown_until + consec_loss_alerted_until 강제 갱신으로 표현하고
        process는 유지해야 함. self.running=False는 connectivity_errors 분기 등 별도 경로만.
    """
    rm = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rm.exists():
        warnings.append("[5연패-B9] realtime_monitor.py 파일 없음")
        return
    lines = rm.read_text(encoding="utf-8").splitlines()
    # 5연패 분기 탐색: "if consec >= 5:" 를 시작으로 다음 빈 줄 또는 "return" 까지 블록
    start = None
    for i, ln in enumerate(lines):
        if re.search(r"if\s+consec\s*>=\s*5\s*:", ln):
            start = i
            break
    if start is None:
        warnings.append("[5연패-B9] 5연패 분기(`if consec >= 5:`)를 찾을 수 없음")
        return
    # 분기 끝: 다음 'return' 까지 또는 함수 종료(들여쓰기 감소)까지
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() == "":
            end += 1
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind <= base_indent and end > start + 1:
            break
        if "return" in ln and ind > base_indent:
            end += 1
            break
        end += 1
    # 주석/문자열 라인 제외 — 실제 코드 라인만 검사
    violations: list[int] = []
    for j in range(start, end):
        ln = lines[j]
        # 주석 라인 (들여쓰기 + #로 시작) 제외
        if ln.lstrip().startswith("#"):
            continue
        # 인라인 주석 분리
        code_part = ln.split("#", 1)[0]
        if re.search(r"self\.running\s*=\s*False", code_part):
            violations.append(j + 1)
    if violations:
        errors.append(
            f"[5연패-B9] realtime_monitor.py 5연패 분기에 self.running=False 발견 "
            f"(lines {violations}) — lessons #37 위배. "
            f"systemd watchdog SIGABRT 회귀 위험. cooldown_until + alerted_until 갱신으로 대체 필요."
        )


def check_consec_loss_cooldown_invariant() -> None:
    """5연패 silence(alerted_until) ↔ 매수 차단(cooldown_until) invariant 강제.

    배경 (lessons #30, #37):
        consec_loss_alerted_until만 설정하고 cooldown_until은 안 만지면, 봇 재시작 후 부활 시
        silence는 살아있고 매수 차단은 풀린 위험 윈도우 발생. 두 플래그는 항상 함께 갱신되어야 함.

    검증:
        realtime_monitor.py 내 consec_loss_alerted_until 설정 라인 인근(±30줄)에 cooldown_until
        설정(max(...,cooldown_target) 또는 self.state["cooldown_until"] = ...) 동시 존재 필수.
    """
    rm = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    if not rm.exists():
        return
    lines = rm.read_text(encoding="utf-8").splitlines()
    set_pat = re.compile(r"consec_loss_alerted_until.*=")
    cd_pat = re.compile(r"cooldown_until.*=|cooldown_target")
    violations: list[int] = []
    for i, ln in enumerate(lines):
        if not set_pat.search(ln):
            continue
        # 단순 읽기(get/dict access)는 제외 — 좌측 값 할당만
        if re.search(r"self\.state\[\"consec_loss_alerted_until\"\]\s*=", ln) or re.search(
            r"\bconsec_loss_alerted_until\s*=", ln
        ):
            # 인근 ±30줄에 cooldown_until 갱신 있는지
            lo = max(0, i - 30)
            hi = min(len(lines), i + 31)
            window = "\n".join(lines[lo:hi])
            if not cd_pat.search(window):
                violations.append(i + 1)
    if violations:
        errors.append(
            f"[5연패-invariant] realtime_monitor.py에서 consec_loss_alerted_until 갱신 시 "
            f"인근 30줄에 cooldown_until 동시 갱신 없음 (lines {violations}) — lessons #30/#37 위배"
        )


def check_consec_loss_floor_consistency() -> None:
    """연패 산정 함수 2곳의 consec_loss_floor_date 필터 일관성 강제 (lessons #38).

    배경 (lessons #38, 2026-06-07):
        연패(consec)는 별도 카운터가 아니라 closed_trades를 매 cycle 재계산. 따라서
        cooldown_until만 리셋해도 다음 cycle에 consec>=5로 72h 재설정되는 함정이 있음.
        근본 해제는 consec_loss_floor_date 필드로 옛 거래를 연패 산정에서 제외하는 방식.

    검증:
        연패를 산정하는 두 함수 — periodic_analysis.check_consec_loss /
        realtime_monitor._get_consec_loss — 가 **둘 다** consec_loss_floor_date를 참조해야
        함. 한쪽만 floor를 적용하면 산정 불일치로 cooldown이 사일런트 부활(경로 B) 가능.
    """
    pa = PROJECT_ROOT / "services" / "reporting" / "periodic_analysis.py"
    rm = PROJECT_ROOT / "services" / "execution" / "realtime_monitor.py"
    missing: list[str] = []
    if pa.exists():
        txt = pa.read_text(encoding="utf-8")
        # check_consec_loss 함수 본문에 floor 참조 존재
        m = re.search(r"def check_consec_loss\(.*?\n(.*?)(?=\ndef |\Z)", txt, re.S)
        if not (m and "consec_loss_floor_date" in m.group(1)):
            missing.append("periodic_analysis.check_consec_loss")
    if rm.exists():
        txt = rm.read_text(encoding="utf-8")
        m = re.search(r"def _get_consec_loss\(.*?\n(.*?)(?=\n    def |\Z)", txt, re.S)
        if not (m and "consec_loss_floor_date" in m.group(1)):
            missing.append("realtime_monitor._get_consec_loss")
    if missing:
        errors.append(
            f"[연패-floor] consec_loss_floor_date 필터 누락: {missing} — "
            f"두 연패 산정 함수 모두 floor를 적용해야 cooldown 근본 해제가 사일런트 부활 안 함 (lessons #38)"
        )


# ═══════════════════════════════════════════════════════════════════
# 검증: deploy_to_aws.sh 사후 SSH 실측 검증 라인 존재 (lessons #36)
# ═══════════════════════════════════════════════════════════════════

def check_deploy_post_check_remote_cron() -> None:
    """deploy_to_aws.sh에 사후 SSH 실측 검증(BitCoin_Trade cron ≥ 8) 라인이 있는지.

    배경 (lessons #36, 2026-08-01):
        Stock_Trade deploy_aws.sh가 crontab을 파일 원자 갱신 방식으로 통째 덮어써서
        BitCoin_Trade cron 8개 전면 소실. 로컬 정적 검사(check_btc_cron_count_baseline)는
        PASS였음 — 스크립트 소스는 정상. 배포 성공 = 서버 반영 확인까지.
        deploy_to_aws.sh 마지막에 ssh "crontab -l | grep -c BitCoin_Trade" 실측 게이트 필수.
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    # 사후 실측 라인: 한 줄 안에 'crontab -l' + 'grep -c BitCoin_Trade' 동시 포함되어야 함
    # (전체 파일 존재만으로는 주석·PROJECT_DIR 등에 문자열이 흩어져 있어 방어 불가)
    line_matched = False
    for raw_line in txt.splitlines():
        # 주석 라인은 실측 라인이 아님
        if raw_line.lstrip().startswith("#"):
            continue
        if "crontab -l" in raw_line and "grep -c BitCoin_Trade" in raw_line:
            line_matched = True
            break
    # 사후 게이트 exit 1 분기도 함께 존재하는지 확인 (검증만 하고 exit 안 하면 무의미)
    # lessons #38 (2026-08-01): 아침 브리핑 신설로 baseline 8→9 상향 — 정규식은 8 이상 정수 허용
    has_exit_guard = re.search(
        r'if\s+\[\s+"\$[A-Z_]+"\s+-lt\s+\d+\s+\];\s+then[\s\S]{0,300}?exit\s+1',
        txt,
    ) is not None
    if not (line_matched and has_exit_guard):
        errors.append(
            "[lessons #36] deploy_to_aws.sh에 사후 SSH 실측 검증 게이트 부재 — "
            f"실측라인={line_matched}, exit1가드={has_exit_guard}. "
            "'ssh ... crontab -l | grep -c BitCoin_Trade' + baseline 미만 시 exit 1 게이트 필수 "
            "(로컬 정적 검사만으로는 다중 프로젝트 crontab 덮어쓰기 방어 불가, 2026-08-01 소실 재발)"
        )


def check_regime_notify_flag() -> None:
    """CRON_REGIME(scripts/regime_check.py 등록 라인)에 --notify 플래그가 있는지.

    배경 (lessons #37, 2026-08-01):
        scripts/regime_check.py는 --notify가 없으면 should_notify=True여도
        텔레그램 발송을 안 함(regime_check.py:81 `if notify and should_notify(...)`).
        deploy_to_aws.sh의 CRON_REGIME에 --notify 미부여 상태로 방치되어
        BULL 전환(관망 종료 시점)이 발생해도 사용자가 실시간 인지 불가.
        cron 명령어 인자 누락은 대표적인 silent fail (lessons #31 계열).

    검증:
        deploy_to_aws.sh의 CRON_REGIME= 라인 (주석 제외)에서
        'regime_check.py'와 '--notify'가 같은 라인에 모두 존재해야 한다.
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    line_matched = False
    for raw_line in txt.splitlines():
        stripped = raw_line.lstrip()
        # 주석 라인 제외 (변수 정의 라인만 검사)
        if stripped.startswith("#"):
            continue
        if "CRON_REGIME=" not in raw_line:
            continue
        if "regime_check.py" in raw_line and "--notify" in raw_line:
            line_matched = True
            break
    if not line_matched:
        errors.append(
            "[lessons #37] deploy_to_aws.sh의 CRON_REGIME 라인에 --notify 플래그 부재 — "
            "regime_check.py:81은 notify=False 시 텔레그램 발송 안 함(should_notify 무의미). "
            "CRON_REGIME=\"... scripts/regime_check.py --notify >> ...\" 형태로 부여 필수 "
            "(BULL 전환 알림 누락 silent fail 방지)"
        )


def check_morning_briefing_registered() -> None:
    """CRON_DAILY_BRIEFING(scripts/daily_check.py --notify)이 deploy_to_aws.sh에 등록되어 있는지.

    배경 (lessons #38, 2026-08-01):
        lessons #33/#34로 CRON_LIVE(09:05 KST 아침 트리거)를 제거하면서 대체 아침
        브리핑 채널을 마련하지 않음 → 사용자 접점은 18:00 daily_report만 존재.
        CLAUDE.md "09:05 KST 실행 권장" 문서만 있고 crontab에는 실제 미등록 상태 2개월 방치.
        regime_check --notify는 전환 시에만 발송 → BEAR 지속 상태에서 아침 침묵.

    검증:
        1) deploy_to_aws.sh 안에 CRON_DAILY_BRIEFING= 변수 정의
        2) 해당 변수 값에 daily_check.py + --notify 동시 포함
        3) 실행 시각 "32 0 * * *" (KST 09:32)
        4) echo "$CRON_DAILY_BRIEFING" 등록 라인 존재
        5) grep -v "daily_check.py" 기존 정리 라인 존재
        6) 사후 실측 게이트 baseline ≥ 9
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    lines = txt.splitlines()

    # 1~3) 변수 정의 라인 검사
    var_ok = False
    for raw_line in lines:
        stripped = raw_line.lstrip()
        if stripped.startswith("#"):
            continue
        if "CRON_DAILY_BRIEFING=" not in raw_line:
            continue
        if (
            "daily_check.py" in raw_line
            and "--notify" in raw_line
            and "32 0 * * *" in raw_line
        ):
            var_ok = True
            break
    if not var_ok:
        errors.append(
            "[lessons #38] deploy_to_aws.sh의 CRON_DAILY_BRIEFING 정의 부재 또는 형식 오류 — "
            "'32 0 * * *' 시각 + daily_check.py + --notify 3요소 동시 필요 "
            "(아침 브리핑 침묵 회귀 방지)"
        )

    # 4) echo 등록 라인
    if 'echo "$CRON_DAILY_BRIEFING"' not in txt:
        errors.append(
            "[lessons #38] deploy_to_aws.sh에 `echo \"$CRON_DAILY_BRIEFING\"` 등록 라인 부재 — "
            "변수만 정의하고 crontab 등록 파이프에 추가하지 않으면 서버 미반영 (lessons #32 동일 패턴)"
        )

    # 5) grep -v "daily_check.py" 정리 라인
    if 'grep -v "daily_check.py"' not in txt:
        errors.append(
            "[lessons #38] deploy_to_aws.sh crontab 재작성 파이프에 "
            "`| grep -v \"daily_check.py\"` 부재 — 기존 등록 중복 리스크"
        )

    # 6) 사후 실측 게이트 baseline ≥ 9
    baseline_ok = False
    for raw_line in lines:
        if 'BTC_REMOTE_LINES' not in raw_line:
            continue
        if '-lt 9' in raw_line or '-lt "9"' in raw_line:
            baseline_ok = True
            break
    if not baseline_ok:
        errors.append(
            "[lessons #38] deploy_to_aws.sh 사후 실측 게이트 baseline이 9 미만 — "
            "아침 브리핑 추가로 baseline 8→9 상향 필수 "
            "(< 9 시 exit 1 조건이 daily_check 미등록을 잡지 못함)"
        )


def main() -> None:
    print("=" * 50)
    print("배포 전 검증 (pre-deploy check)")
    print("=" * 50)

    check_strategy_consistency()
    check_min_volume_krw_range()
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
    check_cron_var_echo_consistency()
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
    check_ssh_canonical_key()
    check_healthcheck_module()
    check_critical_healthcheck_cron()
    check_hourly_digest_cron()
    check_strategy_enhancement_config()
    check_deploy_log_files()
    check_ml_filter_integrity()
    check_system_memory()
    check_zombie_bot_processes()
    check_deploy_no_daily_live_cron()
    check_entry_qty_invariant()
    check_state_qty_mirror_in_fix()
    check_btc_cron_count_baseline()
    check_all_buy_paths_ml_gate()
    check_ml_failopen_policy()
    check_consec_loss_no_running_false()
    check_consec_loss_cooldown_invariant()
    check_consec_loss_floor_consistency()
    check_deploy_post_check_remote_cron()
    check_regime_notify_flag()
    check_morning_briefing_registered()

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
