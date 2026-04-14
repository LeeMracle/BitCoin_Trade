#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Harness Step 6 Gate
====================
하네스 개선안 ③(운영 메트릭 + 훅 자동화) 진입 여부를 자동 판정.

근거 문서:
- docs/decisions/20260409_1_harness_step6_auto_gate.md
- docs/harness_step6_gate_guide.md
- output/improvement_risk_benefit.md

사용법:
    python scripts/harness_step6_gate.py           # 대화형 (수동 3개 Y/N)
    python scripts/harness_step6_gate.py --yes     # 수동 항목 전부 y (CI용)
    python scripts/harness_step6_gate.py --auto    # 자동모드: 수동 skip, 텔레그램 전송

판정:
    대화/yes 모드: 9/11 이상 → exit 0 (GO)
    auto 모드    : 자동 8개 중 7/8 이상 → READY (사람 최종 확인 요청)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANS_DIR = ROOT / "workspace" / "plans"
LESSONS_DIR = ROOT / "docs" / "lessons"
REPORTS_DIR = ROOT / "workspace" / "gate_reports"
STEP4_DATE = "2026-04-09"  # Step 4 도입일 (기준점)


@dataclass
class Check:
    code: str
    desc: str
    result: bool | None
    detail: str
    manual: bool = False


# ---------- 자동 판정 헬퍼 ----------

def list_plan_files() -> list[Path]:
    if not PLANS_DIR.exists():
        return []
    return sorted(
        p for p in PLANS_DIR.glob("2026*.md")
        if not p.name.startswith("_") and p.name != "README.md"
    )


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------- 개별 체크 ----------

def check_A1(plans: list[Path]) -> Check:
    n = len(plans)
    return Check("A1", "plans 파일 ≥3건", n >= 3, f"{n}건")


def check_A2(plans: list[Path]) -> Check:
    if not plans:
        return Check("A2", "성공기준 사전 작성 ≥80%", False, "plans 0건")
    ok = 0
    for p in plans:
        txt = read(p)
        # §2 성공기준 섹션에서 체크박스 라인 ≥1
        m = re.search(r"##\s*2\..*?\n(.*?)(?=\n##\s|\Z)", txt, re.S)
        section = m.group(1) if m else txt
        if re.search(r"-\s*\[\s*[ xX]\s*\]", section):
            ok += 1
    ratio = ok / len(plans)
    return Check(
        "A2", "성공기준 사전 작성 ≥80%",
        ratio >= 0.8, f"{ok}/{len(plans)} ({ratio:.0%})"
    )


def check_A3(plans: list[Path]) -> Check:
    ok = 0
    for p in plans:
        txt = read(p)
        m = re.search(r"##\s*6\..*?\n(.*?)(?=\n##\s|\Z)", txt, re.S)
        if not m:
            continue
        sec = m.group(1)
        has_result = re.search(r"\*\*결과\*\*\s*:\s*\S", sec) or re.search(r"결과\s*:\s*(PASS|FAIL|부분)", sec)
        has_cause = re.search(r"원인\s*귀속\s*:\s*\S", sec)
        if has_result and has_cause:
            ok += 1
    return Check("A3", "회고 ≥1건 + 원인 귀속", ok >= 1, f"{ok}건")


def check_B1(plans: list[Path]) -> Check:
    ok = 0
    for p in plans:
        txt = read(p)
        if re.search(r"검증\s*주체\s*:\s*[A-Da-d]", txt):
            ok += 1
    return Check("B1", "검증 주체 기록 ≥2건", ok >= 2, f"{ok}건")


def check_B2(plans: list[Path]) -> tuple[Check, int, int]:
    """발견 이슈 ≥1건 + B3 계산용 통계 동시 반환."""
    ge1 = 0
    total_with_record = 0
    for p in plans:
        txt = read(p)
        m = re.search(r"발견\s*이슈\s*:\s*(\d+)", txt)
        if m:
            total_with_record += 1
            if int(m.group(1)) >= 1:
                ge1 += 1
    return Check("B2", "이슈 발견 ≥1건", ge1 >= 1, f"{ge1}건"), ge1, total_with_record


def check_B3(ge1: int, total: int) -> Check:
    if total == 0:
        return Check("B3", "이슈 0건 PASS 100% 아님", False, "검증 기록 0건")
    zero_pass_ratio = (total - ge1) / total
    passed = zero_pass_ratio < 1.0
    return Check("B3", "이슈 0건 PASS 100% 아님", passed, f"0건 PASS 비율 {zero_pass_ratio:.0%}")


def check_B4() -> Check:
    # git log에서 pre_deploy_check 언급 또는 최근 실행 흔적 탐색
    try:
        out = subprocess.run(
            ["git", "log", f"--since={STEP4_DATE}", "--grep=pre_deploy_check", "--oneline"],
            capture_output=True, text=True, cwd=ROOT, timeout=10,
        )
        cnt = len([l for l in out.stdout.splitlines() if l.strip()])
    except Exception:
        cnt = 0
    # pyc 캐시 존재도 실행 증거로 인정
    pyc = ROOT / "scripts" / "__pycache__" / "pre_deploy_check.cpython-313.pyc"
    if pyc.exists():
        cnt += 1
    return Check("B4", "pre_deploy_check 실행 ≥1회", cnt >= 1, f"흔적 {cnt}건")


def check_C1() -> Check:
    if not LESSONS_DIR.exists():
        return Check("C1", "신규 사고 0건", True, "lessons 폴더 없음")
    # Step 4 이후 신규 lessons 중 'harness' 언급
    bad = 0
    step4 = datetime.strptime(STEP4_DATE, "%Y-%m-%d")
    for p in LESSONS_DIR.glob("2026*.md"):
        m = re.match(r"(\d{8})_", p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d")
        except Exception:
            continue
        if d < step4:
            continue
        txt = read(p).lower()
        if "harness" in txt or "하네스" in txt or "plans/" in txt:
            bad += 1
    return Check("C1", "신규 사고 0건 (하네스 기인)", bad == 0, f"{bad}건")


# ---------- 수동 판정 ----------

def ask(code: str, question: str, auto_yes: bool) -> Check:
    if auto_yes:
        return Check(code, question, True, "auto --yes", manual=True)
    try:
        ans = input(f"  {code} {question} [y/N]: ").strip().lower()
    except EOFError:
        ans = ""
    result = ans == "y"
    return Check(code, question, result, f"입력: {ans or '(빈값)'}", manual=True)


# ---------- 리포트 ----------

def render_report(checks: list[Check], passed: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    lines = [
        f"# Harness Step 6 Gate Report",
        "",
        f"- 실행: {now}",
        f"- Step 4 도입: {STEP4_DATE}",
        f"- 스크립트: `scripts/harness_step6_gate.py`",
        "",
        "## 판정 결과",
        "",
        "| # | 항목 | 결과 | 상세 | 유형 |",
        "|---|------|:---:|------|:---:|",
    ]
    for c in checks:
        mark = "PASS" if c.result else "FAIL"
        typ = "수동" if c.manual else "자동"
        lines.append(f"| {c.code} | {c.desc} | {mark} | {c.detail} | {typ} |")
    total = len(checks)
    verdict = "GO (Step 6 진입 승인)" if passed >= 9 else "NO-GO (추가 관찰 필요)"
    lines += [
        "",
        f"**점수**: {passed}/{total}",
        f"**판정**: {verdict}",
        "",
        "---",
        "*본 리포트는 자동 생성되었으며 수동 편집 금지. 재판정은 스크립트를 재실행하여 신규 리포트를 생성한다.*",
    ]
    return "\n".join(lines) + "\n"


def send_telegram(text: str) -> None:
    """services/.env에서 토큰/챗ID 로드 후 전송. 실패는 경고만."""
    try:
        env = ROOT / "services" / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            print("[warn] TELEGRAM 토큰/챗ID 없음 — 전송 skip")
            return
        import urllib.request
        import json
        data = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[telegram] {r.status}")
    except Exception as e:
        print(f"[warn] telegram 전송 실패: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="수동 항목을 전부 y로 간주")
    ap.add_argument("--auto", action="store_true", help="자동 모드: 수동 skip, 텔레그램 전송")
    args = ap.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    plans = list_plan_files()

    print("=== Harness Step 6 Gate ===")
    print(f"plans 파일: {len(plans)}건")
    print()

    checks: list[Check] = []
    checks.append(check_A1(plans))
    checks.append(check_A2(plans))
    checks.append(check_A3(plans))
    checks.append(check_B1(plans))
    b2, ge1, total_rec = check_B2(plans)
    checks.append(b2)
    checks.append(check_B3(ge1, total_rec))
    checks.append(check_B4())
    checks.append(check_C1())

    print("자동 판정:")
    for c in checks:
        mark = "PASS" if c.result else "FAIL"
        print(f"  [{mark}] {c.code} {c.desc} — {c.detail}")
    print()

    if args.auto:
        # 자동 모드: 수동 항목 skip, 자동 8개만 채점
        auto_passed = sum(1 for c in checks if c.result)
        ready = auto_passed >= 7
        date_tag = datetime.now().strftime("%Y%m%d_%H%M")
        report_path = REPORTS_DIR / f"{date_tag}_gate_auto.md"
        report_path.write_text(render_report(checks, auto_passed), encoding="utf-8")
        print(f"[auto] 자동 점수: {auto_passed}/8")
        print(f"[auto] 판정: {'READY — 수동 확인 요청' if ready else 'NOT READY'}")
        print(f"[auto] 리포트: {report_path.relative_to(ROOT)}")

        if ready:
            msg = (
                f"[Harness Gate] READY — 수동 확인 요청\n"
                f"자동 점수: {auto_passed}/8\n"
                f"리포트: {report_path.relative_to(ROOT)}\n\n"
                f"다음: python scripts/harness_step6_gate.py (대화형)으로\n"
                f"A4/C2/C3 최종 확인 후 9/11 이상이면 Step 6 진입"
            )
        else:
            fails = [f"{c.code} {c.desc}" for c in checks if not c.result]
            msg = (
                f"[Harness Gate] NOT READY ({auto_passed}/8)\n"
                f"FAIL 항목:\n- " + "\n- ".join(fails) + "\n\n"
                f"1주 후 재실행 (scheduled)"
            )
        send_telegram(msg)
        return 0 if ready else 1

    # 대화형 / --yes 모드
    print("수동 입력:")
    checks.append(ask("A4", "비자명 작업 기준 판정 가능?", args.yes))
    checks.append(ask("C2", "사문화 없음 (규칙 skip 없음)?", args.yes))
    checks.append(ask("C3", "CLAUDE.md 규칙 로드 체감?", args.yes))
    print()

    passed = sum(1 for c in checks if c.result)
    total = len(checks)
    verdict_go = passed >= 9

    print(f"점수: {passed}/{total}")
    print(f"판정: {'GO — Step 6 진입 승인' if verdict_go else 'NO-GO — 추가 관찰 필요'}")

    date_tag = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = REPORTS_DIR / f"{date_tag}_gate.md"
    report_path.write_text(render_report(checks, passed), encoding="utf-8")
    print(f"리포트: {report_path.relative_to(ROOT)}")

    return 0 if verdict_go else 1


if __name__ == "__main__":
    sys.exit(main())
