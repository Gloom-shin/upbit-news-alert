"""4기준 종료 판정 단위 테스트 — 12 cases.

캔들 입력 규약 (oldest → newest):
  c(o, t) = {"opening_price": o, "trade_price": t, "candle_date_time_kst": ""}
양봉: trade_price > opening_price
음봉: trade_price < opening_price
"""
from __future__ import annotations

import pytest

from app import tracker


def c(opening: float, trade: float) -> dict:
    return {
        "opening_price": opening,
        "trade_price": trade,
        "candle_date_time_kst": "2026-05-11T00:00:00",
    }


# ─────────────────────────────────────────────────────────
#  detect_first_red
# ─────────────────────────────────────────────────────────


def test_first_red_immediate():
    """진입 다음날 바로 음봉."""
    candles = [c(110, 100)]  # idx 0: 음봉
    assert tracker.detect_first_red(candles) == 0


def test_first_red_after_two_green():
    """이틀 양봉 후 셋째 날 음봉."""
    candles = [c(100, 110), c(110, 120), c(120, 115)]
    assert tracker.detect_first_red(candles) == 2


def test_first_red_none_when_all_green():
    candles = [c(100, 110), c(110, 120), c(120, 130)]
    assert tracker.detect_first_red(candles) is None


# ─────────────────────────────────────────────────────────
#  detect_trailing_drop  (default threshold 7%)
# ─────────────────────────────────────────────────────────


def test_trailing_drop_triggered():
    """고점 200 도달 후 184(-8%) 종가 — 7% 임계 돌파."""
    entry = 100.0
    candles = [
        c(100, 150),   # close 150 (peak=150)
        c(150, 200),   # close 200 (peak=200)
        c(200, 184),   # close 184 = 200 * 0.92  →  -8%, 트리거
    ]
    assert tracker.detect_trailing_drop(candles, entry, threshold=0.07) == 2


def test_trailing_drop_not_triggered_below_threshold():
    """고점 대비 -5%만 — 트리거 안 됨."""
    entry = 100.0
    candles = [
        c(100, 150),
        c(150, 200),
        c(200, 190),  # -5%
    ]
    assert tracker.detect_trailing_drop(candles, entry, threshold=0.07) is None


def test_trailing_drop_uses_running_peak_not_entry():
    """진입가가 100인데 종가가 99로 떨어졌어도 peak=100이면
    -1%라 트레일링 트리거 안 됨 (below_entry는 다른 함수의 영역)."""
    entry = 100.0
    candles = [c(100, 99)]
    assert tracker.detect_trailing_drop(candles, entry, threshold=0.07) is None


# ─────────────────────────────────────────────────────────
#  detect_below_entry
# ─────────────────────────────────────────────────────────


def test_below_entry_triggered():
    entry = 100.0
    candles = [c(100, 105), c(105, 99)]   # idx 1 종가 99 < 100
    assert tracker.detect_below_entry(candles, entry) == 1


def test_below_entry_not_triggered_when_equal():
    """진입가와 동일하면 트리거 안 됨 (strictly less than)."""
    entry = 100.0
    candles = [c(100, 100), c(100, 101)]
    assert tracker.detect_below_entry(candles, entry) is None


# ─────────────────────────────────────────────────────────
#  detect_consecutive_red  (default n=2)
# ─────────────────────────────────────────────────────────


def test_consecutive_red_triggered():
    """음, 양, 음, 음 — 4번째에서 연속 2 만족."""
    candles = [c(110, 100), c(100, 110), c(120, 115), c(115, 110)]
    assert tracker.detect_consecutive_red(candles, n=2) == 3


def test_consecutive_red_resets_on_green():
    """연속이 깨졌다가 다시 시작되는 경우."""
    candles = [c(110, 100), c(100, 105), c(110, 100), c(100, 95)]
    # idx0 음, idx1 양(reset), idx2 음, idx3 음 → 트리거 idx=3
    assert tracker.detect_consecutive_red(candles, n=2) == 3


def test_consecutive_red_n_equals_3():
    """3일 연속 음봉 케이스."""
    candles = [c(110, 100), c(100, 95), c(95, 90)]
    assert tracker.detect_consecutive_red(candles, n=3) == 2


# ─────────────────────────────────────────────────────────
#  primary_close_reason — 우선순위 처리
# ─────────────────────────────────────────────────────────


def test_primary_close_reason_picks_earliest_index():
    """여러 기준이 발생했을 때 가장 빠른 인덱스의 기준이 선택됨."""
    outcomes = {
        "first_red": 2,
        "trailing_drop": 1,
        "below_entry": 5,
        "consecutive_red": 3,
    }
    crit, days = tracker.primary_close_reason(outcomes)
    assert crit == "trailing_drop"
    assert days == 2  # idx 1 → days_to_trigger 1-based = 2
