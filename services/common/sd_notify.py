"""경량 systemd sd_notify 래퍼.

의존성(systemd-python) 없이 `$NOTIFY_SOCKET` AF_UNIX 소켓으로 직접 전송.
NOTIFY_SOCKET이 없으면(로컬/Windows 등) 모든 호출은 silently no-op.

사용 예:
    from services.common.sd_notify import notify, ready, watchdog_ping
    ready()                 # 시작 완료 알림
    watchdog_ping()         # 헬스 핑 (WatchdogSec 주기보다 짧게)

참고: systemd.io/NOTIFY_SOCKET/
"""
from __future__ import annotations

import os
import socket


_SOCK_ADDR = os.environ.get("NOTIFY_SOCKET")


def _send(msg: str) -> bool:
    """NOTIFY_SOCKET으로 메시지 전송. 실패/미설정 시 False."""
    if not _SOCK_ADDR:
        return False
    try:
        # 리눅스 abstract 소켓 (첫 바이트가 \0)
        addr = "\0" + _SOCK_ADDR[1:] if _SOCK_ADDR.startswith("@") else _SOCK_ADDR
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode("utf-8"), addr)
        return True
    except Exception:
        return False


def notify(state: str) -> bool:
    """임의 상태 전송 (예: 'STATUS=running')."""
    return _send(state)


def ready() -> bool:
    """서비스 초기화 완료 — Type=notify 환경에서 필수."""
    return _send("READY=1")


def watchdog_ping() -> bool:
    """WATCHDOG=1 전송 — WatchdogSec 주기 내에 주기적으로 호출해야 함."""
    return _send("WATCHDOG=1")


def status(text: str) -> bool:
    """systemd STATUS 필드 갱신."""
    return _send(f"STATUS={text}")
