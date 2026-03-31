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
