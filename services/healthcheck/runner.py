"""헬스체크 러너 — BATA 운영 상태 8개 항목 점검.

각 체크는 dict 반환: {"name", "status", "detail"} (status: OK/WARN/FAIL).
모든 체크는 try/except로 감싸 개별 실패가 보고 전체를 막지 않도록 한다.

사용:
    from services.healthcheck.runner import run_all, build_health_section
    results = run_all()
    text = build_health_section(results)

CLI:
    python -m services.healthcheck.runner          # 전체 출력
    python -m services.healthcheck.runner --json   # JSON 출력
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 프로젝트 루트
ROOT = Path(__file__).resolve().parents[2]
# 외부 호출(pre_deploy_check 등)에서 sys.path 미설정 시 ModuleNotFoundError 방지
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
KST = timezone(timedelta(hours=9))

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


def _result(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


# ════════════════════════════════════════════════════════════
# 1. 인증 (Upbit private API)
# ════════════════════════════════════════════════════════════

def check_auth() -> dict:
    """업비트 private API 호출 가능 여부 + 응답시간."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / "services" / ".env")
        import ccxt
        access = os.environ.get("UPBIT_ACCESS_KEY", "")
        secret = os.environ.get("UPBIT_SECRET_KEY", "")
        if not access or not secret:
            return _result("인증", FAIL, "UPBIT_ACCESS_KEY/SECRET_KEY 미설정")
        ex = ccxt.upbit({"apiKey": access, "secret": secret, "enableRateLimit": True})
        t0 = time.time()
        ex.fetch_balance()
        elapsed_ms = int((time.time() - t0) * 1000)
        prefix = access[:6]
        if elapsed_ms > 2000:
            return _result("인증", WARN, f"OK {elapsed_ms}ms (느림, 키 {prefix}...)")
        return _result("인증", OK, f"{elapsed_ms}ms (키 {prefix}...)")
    except Exception as e:
        msg = str(e)[:100]
        return _result("인증", FAIL, f"{type(e).__name__}: {msg}")


# ════════════════════════════════════════════════════════════
# 2. 키-IP 매핑 가시화
# ════════════════════════════════════════════════════════════

def check_key_ip_mapping() -> dict:
    """현재 .env 키 prefix + 서버 외부 IP를 표시 (정보성)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / "services" / ".env")
        access = os.environ.get("UPBIT_ACCESS_KEY", "")
        prefix = access[:6] if access else "(없음)"
        ext_ip = "(조회 실패)"
        try:
            with urllib.request.urlopen("https://ifconfig.me", timeout=5) as r:
                ext_ip = r.read().decode().strip()
        except Exception:
            try:
                with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
                    ext_ip = r.read().decode().strip()
            except Exception:
                pass
        return _result("키-IP", OK, f"{prefix}... → {ext_ip}")
    except Exception as e:
        return _result("키-IP", WARN, f"{type(e).__name__}")


# ════════════════════════════════════════════════════════════
# 3. jarvis cron 동작 흔적
# ════════════════════════════════════════════════════════════

def check_jarvis_cron(window_hours: int = 2,
                      cron_log: str = "/var/log/jarvis_executor.log") -> dict:
    """jarvis cron 실행 흔적 점검.

    판정 기준 (lessons #21 — false FAIL 방지):
      1순위: /var/log/jarvis_executor.log mtime — cron 매시 정각 실제 실행 여부
      2순위: workspace/jarvis_log.jsonl error 비율 — 매매·오류 발생 시만 기록되므로
              "최근 무로그"로 FAIL 판정 금지. error 누적만 WARN으로 보강.

    STOP 모드 (2026-05-05): jarvis_strategies.json에 활성 전략 0건이면 skip → OK 반환.
    사유: 5-4 BTC 분할매도 사용자 수동 매도 완료, cron STOP 상태에서 mtime stale로 false FAIL 방지.
    """
    # STOP 모드 자동 인식 — 활성 전략 0건이면 OK
    try:
        import json as _json
        strat_p = ROOT / "workspace" / "jarvis_strategies.json"
        if strat_p.exists():
            strategies = _json.loads(strat_p.read_text(encoding="utf-8"))
            active = [k for k, v in strategies.items() if v.get("active")]
            if not active:
                return _result("jarvis cron", OK, "STOP 모드 (활성 전략 0건)")
    except Exception:
        pass  # 파일 읽기 실패 시 기존 로직으로 fallback

    log_p = Path(cron_log)
    if not log_p.exists():
        return _result("jarvis cron", FAIL, f"{cron_log} 없음")
    try:
        # 1. cron 실제 실행 여부 (mtime 기반)
        mtime = datetime.fromtimestamp(log_p.stat().st_mtime, tz=KST)
        age = datetime.now(tz=KST) - mtime
        age_min = int(age.total_seconds() / 60)
        if age > timedelta(hours=window_hours):
            return _result("jarvis cron", FAIL,
                           f"{age_min}분 전 마지막 실행 (>{window_hours}h, mtime {mtime:%H:%M})")
        status = OK
        detail = f"{age_min}분 전 ({mtime:%H:%M})"

        # 2. jarvis_log.jsonl로 최근 1h 오류 누적 점검 (보조)
        jl = ROOT / "workspace" / "jarvis_log.jsonl"
        if jl.exists():
            cutoff = datetime.now(tz=KST) - timedelta(hours=1)
            errors = 0
            with jl.open("r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-200:]:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts_str = entry.get("logged_at") or entry.get("ts")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                except Exception:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=KST)
                if ts >= cutoff and entry.get("error"):
                    errors += 1
            if errors >= 1:
                status = WARN
                detail += f" / 1h error {errors}건"
        return _result("jarvis cron", status, detail)
    except Exception as e:
        return _result("jarvis cron", FAIL, f"{type(e).__name__}: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════
# 4. daily_live 오늘 실행
# ════════════════════════════════════════════════════════════

def check_daily_live(log_path: str = "/var/log/btc_trader.log") -> dict:
    """오늘 날짜 라인이 로그에 있는지 확인.

    plan 20260503 (AC14): 09:05 KST 이전엔 cron이 아직 안 돌았으므로 어제 날짜로 체크.
    """
    from datetime import time as _dtime
    p = Path(log_path)
    if not p.exists():
        return _result("daily_live", WARN, f"로그 파일 없음 ({log_path})")
    try:
        now = datetime.now(tz=KST)
        # 09:05 이전이면 어제 날짜 사용 (daily_live cron이 매일 09:05 KST 실행)
        if now.time() < _dtime(9, 5):
            target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            label = f"어제 {target_date} (09:05 cron 실행 전)"
        else:
            target_date = now.strftime("%Y-%m-%d")
            label = f"오늘 {target_date}"
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-5000:]
        for line in lines:
            if target_date in line:
                return _result("daily_live", OK, f"{label} 라인 존재")
        return _result("daily_live", WARN, f"{label} 라인 없음 (systemd 가동 가능성)")
    except Exception as e:
        return _result("daily_live", WARN, f"{type(e).__name__}")


def check_balance_fetch() -> dict:
    """잔고 조회 가능 여부 — CB가 실제로 평가에 쓰는 경로와 동일.

    plan 20260503 P0 (AC9): 인증 OK인데 Rate Limit으로 잔고만 실패하는 케이스 별도 탐지.
    영구 매수 차단 트랩 조기 발견용.
    """
    try:
        from services.execution.upbit_client import get_balance, RateLimitExhausted
        t0 = time.time()
        bal = get_balance()
        elapsed_ms = int((time.time() - t0) * 1000)
        krw = bal.get("krw", 0)
        total = bal.get("total_krw", 0)
        if elapsed_ms > 5000:
            return _result("잔고조회", WARN,
                           f"{elapsed_ms}ms (느림) total {total:,.0f}")
        return _result("잔고조회", OK,
                       f"{elapsed_ms}ms KRW {krw:,.0f} / total {total:,.0f}")
    except Exception as e:
        # RateLimitExhausted 포함 모든 예외 FAIL — CB가 평가 못 하는 상태
        return _result("잔고조회", FAIL, f"{type(e).__name__}: {str(e)[:80]}")


# ════════════════════════════════════════════════════════════
# 5. regime_check 최근 실행
# ════════════════════════════════════════════════════════════

def check_regime_check(log_path: str = "/var/log/regime_check.log",
                       window_hours: int = 26) -> dict:
    """regime_check.log mtime 또는 마지막 라인 시각 확인.
    2026-05-05: cron 매시 → 일 1회 KST 09:30 축소 → 임계 2h → 26h (24h + 2h 여유)
    """
    p = Path(log_path)
    if not p.exists():
        return _result("regime_check", WARN, f"로그 파일 없음 ({log_path})")
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=KST)
        age = datetime.now(tz=KST) - mtime
        age_min = int(age.total_seconds() / 60)
        if age > timedelta(hours=window_hours):
            return _result("regime_check", WARN,
                           f"{age_min}분 전 (>{window_hours}h, mtime {mtime:%H:%M})")
        return _result("regime_check", OK, f"{age_min}분 전 ({mtime:%H:%M})")
    except Exception as e:
        return _result("regime_check", WARN, f"{type(e).__name__}")


# ════════════════════════════════════════════════════════════
# 6. state 신선도
# ════════════════════════════════════════════════════════════

def check_state_freshness() -> dict:
    """multi_trading_state.json + vb_state.json mtime."""
    paths = {
        "composite": ROOT / "workspace" / "multi_trading_state.json",
        "vb": ROOT / "workspace" / "vb_state.json",
    }
    parts = []
    worst = OK
    for name, p in paths.items():
        if not p.exists():
            parts.append(f"{name} 없음")
            worst = WARN
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=KST)
        age = datetime.now(tz=KST) - mtime
        if age > timedelta(hours=24):
            h = int(age.total_seconds() / 3600)
            parts.append(f"{name} {h}h")
            worst = WARN
        else:
            h = int(age.total_seconds() / 3600)
            parts.append(f"{name} {h}h")
    return _result("state 신선도", worst, " / ".join(parts))


# ════════════════════════════════════════════════════════════
# 7. 시스템 (디스크/메모리)
# ════════════════════════════════════════════════════════════

def check_system() -> dict:
    """디스크/메모리/swap 사용률."""
    try:
        # 디스크
        total, used, free = shutil.disk_usage("/")
        disk_pct = int(used * 100 / total)
        # 메모리 (리눅스만)
        mem_pct = -1
        swap_pct = -1
        meminfo_path = Path("/proc/meminfo")
        if meminfo_path.exists():
            mem = {}
            with meminfo_path.open() as f:
                for line in f:
                    k, _, v = line.partition(":")
                    mem[k.strip()] = int(v.strip().split()[0])
            mem_total = mem.get("MemTotal", 1)
            mem_avail = mem.get("MemAvailable", 0)
            mem_pct = int((mem_total - mem_avail) * 100 / mem_total)
            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            if swap_total > 0:
                swap_pct = int((swap_total - swap_free) * 100 / swap_total)
        # 판정 (lessons #5: t3.micro 메모리 압박 — swap 50%+ WARN, mem 90%+ WARN)
        worst = OK
        if disk_pct >= 80 or mem_pct >= 90 or (swap_pct >= 0 and swap_pct >= 50):
            worst = WARN
        parts = [f"disk {disk_pct}%"]
        if mem_pct >= 0:
            parts.append(f"mem {mem_pct}%")
        if swap_pct >= 0:
            parts.append(f"swap {swap_pct}%")
        return _result("시스템", worst, " | ".join(parts))
    except Exception as e:
        return _result("시스템", WARN, f"{type(e).__name__}")


# ════════════════════════════════════════════════════════════
# 8. state ↔ balance 일관성
# ════════════════════════════════════════════════════════════

def check_state_balance_consistency() -> dict:
    """composite/vb state의 보유 종목 vs 거래소 잔고 비교.

    오탐 방지: 첫 1주는 WARN까지만, FAIL 승격은 별도 plan.
    """
    try:
        from services.execution.upbit_client import get_balance
        bal = get_balance()
        # state 파일
        composite_state = ROOT / "workspace" / "multi_trading_state.json"
        vb_state = ROOT / "workspace" / "vb_state.json"
        state_symbols = set()
        if composite_state.exists():
            d = json.loads(composite_state.read_text(encoding="utf-8"))
            for sym in d.get("positions", {}).keys():
                # "BTC/KRW" → "BTC"
                state_symbols.add(sym.split("/")[0])
        if vb_state.exists():
            d = json.loads(vb_state.read_text(encoding="utf-8"))
            for sym in d.get("positions", {}).keys():
                state_symbols.add(sym.split("/")[0])

        # 거래소 잔고에서 의미 있는 종목 (BTC + 알트, KRW 제외)
        from services.execution.upbit_client import _create_exchange  # noqa
        ex = _create_exchange()
        balance = ex.fetch_balance()
        SKIP = {"KRW", "info", "free", "used", "total", "timestamp", "datetime"}
        bal_symbols = set()
        for coin, amounts in balance.items():
            if coin in SKIP or not isinstance(amounts, dict):
                continue
            total_amt = float(amounts.get("total", 0) or 0)
            if total_amt > 0:
                bal_symbols.add(coin)

        # 비교
        only_state = state_symbols - bal_symbols
        only_bal = bal_symbols - state_symbols
        # BTC는 dust로 잔고에 남을 수 있어 제외하지 않고 그대로 비교
        if not only_state and not only_bal:
            return _result("state↔balance", OK,
                           f"일치 ({len(state_symbols)}종목)")
        parts = []
        if only_state:
            parts.append(f"state-only {','.join(sorted(only_state))}")
        if only_bal:
            parts.append(f"balance-only {','.join(sorted(only_bal))}")
        return _result("state↔balance", WARN, " / ".join(parts))
    except Exception as e:
        return _result("state↔balance", WARN, f"{type(e).__name__}: {str(e)[:60]}")


# ════════════════════════════════════════════════════════════
# 신규: 일일 손실 한도 (plan 20260504_2 AC15)
# ════════════════════════════════════════════════════════════

def check_daily_loss_state(state_path: str = None) -> dict:
    """daily_pl_state.json 점검 — reset 누락(>24h mtime) 또는 한도 발동 감지."""
    if state_path is None:
        state_path = str(ROOT / "workspace" / "daily_pl_state.json")
    p = Path(state_path)
    if not p.exists():
        return _result("일일손익", OK, "state 없음 (첫 가동 또는 reset 직후)")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=KST)
        age = datetime.now(tz=KST) - mtime
        pl_krw = d.get("realized_pl_krw", 0)
        blocked = d.get("blocked", False)
        date = d.get("date", "?")
        if age > timedelta(hours=24):
            return _result("일일손익", WARN,
                           f"state 24h+ 미갱신 (date={date}, mtime {mtime:%m-%d %H:%M})")
        if blocked:
            return _result("일일손익", WARN,
                           f"한도 발동 (date={date}, pl={pl_krw:,.0f} KRW, 매수 차단)")
        return _result("일일손익", OK,
                       f"date={date} pl={pl_krw:+,.0f} KRW")
    except Exception as e:
        return _result("일일손익", WARN, f"{type(e).__name__}: {str(e)[:60]}")


# ════════════════════════════════════════════════════════════
# 신규: hourly_digest heartbeat (plan 20260503_4 cto #1)
# ════════════════════════════════════════════════════════════

def check_digest_heartbeat(heartbeat_path: str = "/tmp/bata_hourly_digest_heartbeat") -> dict:
    """hourly_digest cron 실행 흔적 — heartbeat 파일 mtime.

    매시 30분 cron이 죽었거나 침묵 로직 무한루프로 안 도는 경우 감지.
    """
    p = Path(heartbeat_path)
    if not p.exists():
        return _result("digest heartbeat", WARN, "heartbeat 없음 (cron 미가동 또는 첫 실행 전)")
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=KST)
        age = datetime.now(tz=KST) - mtime
        age_min = int(age.total_seconds() / 60)
        if age > timedelta(hours=2):
            return _result("digest heartbeat", FAIL,
                           f"{age_min}분 전 (>2h, cron 죽음 의심, mtime {mtime:%H:%M})")
        if age > timedelta(minutes=90):
            return _result("digest heartbeat", WARN,
                           f"{age_min}분 전 (>90분, 다음 cron 지연 의심)")
        return _result("digest heartbeat", OK, f"{age_min}분 전 ({mtime:%H:%M})")
    except Exception as e:
        return _result("digest heartbeat", WARN, f"{type(e).__name__}")


# ════════════════════════════════════════════════════════════
# 9. 로그 볼륨 (전일 요약 — log_volume_check.sh 결과 흡수)
# ════════════════════════════════════════════════════════════

def check_log_volume(log_path: str = "/home/ubuntu/BitCoin_Trade/logs/log_volume.log") -> dict:
    """log_volume_check.sh가 매일 09:10 KST 작성한 마지막 라인을 표시.

    형식: "YYYY-MM-DD total=N errors=N spam=N"
    """
    p = Path(log_path)
    if not p.exists():
        return _result("로그 볼륨", WARN, "log_volume.log 없음")
    try:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return _result("로그 볼륨", WARN, "log_volume.log 비어있음")
        last = lines[-1]
        # "2026-05-01 total=4231 errors=3 spam=0"
        parts = last.split()
        date_str = parts[0] if parts else "?"
        kv = {p.split("=")[0]: p.split("=")[1] for p in parts[1:] if "=" in p}
        total = int(kv.get("total", -1))
        errors = int(kv.get("errors", -1))
        spam = int(kv.get("spam", -1))
        # 판정
        status = OK
        notes = []
        if total == 0:
            status = FAIL
            notes.append("로그 0줄")
        elif total > 5000:
            status = WARN
            notes.append("과다(>5000)")
        if errors > 100:
            status = WARN if status != FAIL else FAIL
            notes.append("오류 과다(>100)")
        suffix = f" — {', '.join(notes)}" if notes else ""
        return _result("로그 볼륨",
                       status,
                       f"{date_str} total={total} err={errors} spam={spam}{suffix}")
    except Exception as e:
        return _result("로그 볼륨", WARN, f"{type(e).__name__}: {str(e)[:60]}")


# ════════════════════════════════════════════════════════════
# 종합
# ════════════════════════════════════════════════════════════

ALL_CHECKS = [
    check_auth,
    check_balance_fetch,        # plan 20260503 P0 — Rate Limit으로 잔고만 실패 케이스
    check_key_ip_mapping,
    check_jarvis_cron,
    check_daily_live,
    check_regime_check,
    check_state_freshness,
    check_system,
    check_state_balance_consistency,
    check_log_volume,
    check_digest_heartbeat,     # plan 20260503_4 cto #1 — hourly_digest 죽음 감지
    check_daily_loss_state,     # plan 20260504_2 AC15 — 일일 손실 한도 + reset 누락 감지
]


def run_all() -> list[dict]:
    """8개 체크 순차 실행. 개별 실패는 다른 체크에 영향 없음."""
    results = []
    for fn in ALL_CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            results.append(_result(fn.__name__, FAIL, f"체크 자체 오류: {e}"))
    return results


def overall_status(results: list[dict]) -> str:
    """전체 종합 status (FAIL > WARN > OK)."""
    statuses = {r["status"] for r in results}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK


def build_health_section(results: list[dict] | None = None) -> str:
    """텔레그램용 헬스체크 섹션 텍스트.

    Returns:
        멀티라인 텍스트 (헤더 + 8개 항목 + 종합)
    """
    if results is None:
        results = run_all()
    now_kst = datetime.now(tz=KST).strftime("%H:%M KST")
    lines = [f"🩺 헬스체크 ({now_kst})"]
    for r in results:
        icon = _ICON.get(r["status"], "•")
        lines.append(f"  {icon} {r['name']}: {r['detail']}")
    final = overall_status(results)
    summary_icon = _ICON[final]
    summary_text = {OK: "정상", WARN: "주의", FAIL: "비정상 — 즉시 조치 필요"}[final]
    lines.append(f"  ─ 종합: {summary_text} {summary_icon}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--dry", action="store_true", help="(호환용, 동작 동일)")
    args = ap.parse_args()
    results = run_all()
    if args.json:
        print(json.dumps({
            "results": results,
            "overall": overall_status(results),
        }, ensure_ascii=False, indent=2))
    else:
        print(build_health_section(results))
    # exit code: FAIL=2, WARN=1, OK=0
    final = overall_status(results)
    sys.exit({OK: 0, WARN: 1, FAIL: 2}[final])


if __name__ == "__main__":
    main()
