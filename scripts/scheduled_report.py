"""지정 시각에 텔레그램 모니터링 보고를 발송하는 스케줄러.

사용법:
  python scripts/scheduled_report.py 12:00,18:00
  python scripts/scheduled_report.py 12:00,18:00 --daily-end

--daily-end: 마지막 보고 발송 후 "일일 종료 안내" 메시지를 추가 발송
"""
import sys, io, os, asyncio, time, signal
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ["PYTHONUTF8"] = "1"
sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID_FILE = ROOT / "scripts" / "scheduled_report.pid"
KST = timezone(timedelta(hours=9))


def write_pid():
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def handle_signal(signum, frame):
    print(f"\n[스케줄러] 종료 시그널 수신 (sig={signum})")
    remove_pid()
    sys.exit(0)


async def run_report():
    """hourly_monitor.py의 build_report + send 실행."""
    from scripts.hourly_monitor import build_report
    from services.alerting.notifier import send

    report = await build_report()
    print(report)
    ok = await send(report)
    status = "발송 성공" if ok else "발송 실패"
    print(f"[{datetime.now(tz=KST):%H:%M}] 텔레그램 {status}")
    return ok


async def send_daily_end_notice():
    """일일 종료 안내 메시지 발송."""
    from services.alerting.notifier import send
    now = datetime.now(tz=KST)
    msg = (
        f"*일일 종료 안내* ({now:%Y-%m-%d} {now:%H:%M})\n\n"
        "마지막 모니터링 보고가 완료되었습니다.\n"
        "Claude Code에서 `/daily end`를 실행하여 일일작업을 종료해 주세요."
    )
    await send(msg)
    print(f"[{now:%H:%M}] 일일 종료 안내 발송 완료")


def parse_times(arg: str) -> list[tuple[int, int]]:
    """'12:00,18:00' → [(12,0), (18,0)] 정렬."""
    times = []
    for t in arg.split(","):
        t = t.strip()
        h, m = t.split(":")
        times.append((int(h), int(m)))
    times.sort()
    return times


def seconds_until(hour: int, minute: int) -> float:
    """KST 기준 해당 시각까지 남은 초. 이미 지났으면 음수."""
    now = datetime.now(tz=KST)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (target - now).total_seconds()


async def main():
    if len(sys.argv) < 2:
        print("사용법: python scripts/scheduled_report.py 12:00,18:00 [--daily-end]")
        sys.exit(1)

    times = parse_times(sys.argv[1])
    daily_end = "--daily-end" in sys.argv

    # 시그널 핸들러
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    write_pid()
    print(f"[스케줄러] PID={os.getpid()}")
    print(f"[스케줄러] 예약 시각: {', '.join(f'{h:02d}:{m:02d}' for h,m in times)}")
    if daily_end:
        print(f"[스케줄러] 마지막 보고 후 /daily end 안내 발송")

    # 이미 지난 시각 건너뛰기
    pending = [(h, m) for h, m in times if seconds_until(h, m) > 0]
    skipped = len(times) - len(pending)
    if skipped:
        print(f"[스케줄러] {skipped}건 이미 지난 시각 건너뜀")

    if not pending:
        print("[스케줄러] 남은 예약 없음 — 종료")
        remove_pid()
        return

    for i, (h, m) in enumerate(pending):
        wait = seconds_until(h, m)
        if wait <= 0:
            continue

        print(f"[스케줄러] {h:02d}:{m:02d}까지 {wait:.0f}초 대기...")
        await asyncio.sleep(wait)

        print(f"\n{'='*50}")
        print(f"[{datetime.now(tz=KST):%H:%M}] 예약 보고 시작")
        print(f"{'='*50}")
        await run_report()

        is_last = (i == len(pending) - 1)
        if is_last and daily_end:
            await send_daily_end_notice()

    print(f"\n[스케줄러] 모든 예약 완료 — 종료")
    remove_pid()


if __name__ == "__main__":
    asyncio.run(main())
