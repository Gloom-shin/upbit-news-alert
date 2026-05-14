"""알림 윈도우 판정 테스트.

피크 30분 전 ~ 피크 종료 구간을 윈도우 안으로 본다.
config.ALERT_WINDOWS = [(8,30,10,0), (15,30,18,0), (22,0,24,0), (0,0,1,0), (1,30,4,0)]
"""
from datetime import datetime

import pytest

from app import alert_window


def at(h: int, m: int) -> datetime:
    """오늘 날짜의 KST H:M (날짜는 의미 없음, 시각만 본다)."""
    return datetime(2026, 5, 14, h, m)


@pytest.mark.parametrize("h,m", [
    # 국내 윈도우 (08:30 - 10:00)
    (8, 30), (8, 31), (9, 0), (9, 30), (9, 59),
    # 유럽 윈도우 (15:30 - 18:00)
    (15, 30), (16, 0), (17, 59),
    # 미국 윈도우 자정 전 (22:00 - 24:00)
    (22, 0), (22, 30), (23, 0), (23, 59),
    # 미국 윈도우 자정 후 (00:00 - 01:00)
    (0, 0), (0, 30), (0, 59),
    # 심야 윈도우 (01:30 - 04:00)
    (1, 30), (2, 0), (3, 59),
])
def test_in_window(h: int, m: int) -> None:
    assert alert_window.is_in_window(at(h, m)) is True, f"{h:02d}:{m:02d} should be in window"


@pytest.mark.parametrize("h,m", [
    # 새벽 윈도우 밖 (01:00 - 01:30 갭)
    (1, 0), (1, 15), (1, 29),
    # 04:00 직후
    (4, 0), (4, 30), (5, 0),
    # 오전 윈도우 직전
    (8, 0), (8, 29),
    # 오전 윈도우 직후
    (10, 0), (10, 30), (12, 0),
    # 오후 윈도우 직전
    (15, 0), (15, 29),
    # 오후 윈도우 직후
    (18, 0), (19, 0), (21, 0),
    # 미국 윈도우 직전
    (21, 59),
])
def test_out_of_window(h: int, m: int) -> None:
    assert alert_window.is_in_window(at(h, m)) is False, f"{h:02d}:{m:02d} should be OUT of window"


def test_label_returns_when_in_window() -> None:
    assert alert_window.current_window_label(at(9, 0)) is not None
    assert alert_window.current_window_label(at(22, 30)) is not None
    assert alert_window.current_window_label(at(0, 30)) is not None


def test_label_none_when_out() -> None:
    assert alert_window.current_window_label(at(12, 0)) is None
    assert alert_window.current_window_label(at(1, 15)) is None
