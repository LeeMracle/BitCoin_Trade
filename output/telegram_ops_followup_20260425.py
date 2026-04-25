"""04-25 토 운영 감시 후속 처리 마무리 보고."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.alerting.notifier import send  # noqa: E402

MSG = """✅ *BATA 운영 감시 후속 처리* (04-25)

*1) regime\\_check crontab — 복구 완료*
- `venv/bin/python` → `.venv/bin/python` 1글자 치환
- 백업: `~/crontab.bak.20260425`
- 수동 1회 실행 OK (exit 0)
- regime\\_state 갱신: 04-25 15:21 KST
- 현재 BEAR 유지 (BTC 115,480k < EMA200 124,981k)
- 다음 매시 25분부터 자연 실행 재개

*2) VB vb\\_recheck\\_trigger — 수동 운영 유지*
- DRY-RUN 빈도 낮아 cron 미등록 합리
- 매주 일요일 자비스 일일 브리핑에서 수동 호출

*3) 신규 lesson 기록*
- `lessons/20260425_1_crontab_venv_path_drift.md`
- CLAUDE.md 교훈 #18 추가
- 핵심: venv 리네임 시 crontab/systemd 동시 갱신 필수, stderr→로그파일 리디렉션은 silent fail

— Jarvis"""


async def main():
    ok = await send(MSG)
    print("OK" if ok else "FAIL")


if __name__ == "__main__":
    asyncio.run(main())
