"""알림 윈도우 판정 (KST). 순수 함수, I/O 없음.

피크 거래량 30분 전부터 피크 종료까지를 "윈도우 안"으로 본다.
config.ALERT_WINDOWS 기반.
"""
from __future__ import annotations

from datetime import datetime, time

from . import config


def _to_minutes(h: int, m: int) -> int:
    return h * 60 + m


def is_in_window(now: datetime) -> bool:
    """현재 시각이 4개 윈도우 중 하나에 속하는지."""
    cur = _to_minutes(now.hour, now.minute)
    for sh, sm, eh, em in config.ALERT_WINDOWS:
        start = _to_minutes(sh, sm)
        end = _to_minutes(eh, em)
        # end가 24:00(=1440)인 경우 포함, 그 외는 [start, end) 반열림 구간
        if start <= cur < end:
            return True
    return False


def current_window_label(now: datetime) -> str | None:
    """현재 속한 윈도우 라벨 (디버그/로그용)."""
    if not is_in_window(now):
        return None
    cur = _to_minutes(now.hour, now.minute)
    for sh, sm, eh, em in config.ALERT_WINDOWS:
        start = _to_minutes(sh, sm)
        end = _to_minutes(eh, em)
        if start <= cur < end:
            return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"
    return None
