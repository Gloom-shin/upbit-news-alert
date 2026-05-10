"""상승 종료 4기준 판정 — 단위테스트가 이 함수들을 직접 검증한다.

기준:
  1. first_red       — 진입 후 첫 음봉
  2. trailing_drop   — 고점 대비 -X% 이상 하락 (기본 7%)
  3. below_entry     — 종가가 진입가 아래로 떨어짐
  4. consecutive_red — N일 연속 음봉 (기본 2일)

캔들 입력 규약: oldest → newest 순서의 list[dict]. dict는 최소
  {"opening_price": float, "trade_price": float, "candle_date_time_kst": str}
필드를 가진다. (Upbit candle 응답 키 그대로)
"""
from __future__ import annotations

from typing import Any

from . import config


def _is_red(c: dict[str, Any]) -> bool:
    return float(c["trade_price"]) < float(c["opening_price"])


def detect_first_red(candles_after_entry: list[dict]) -> int | None:
    """진입 시점 다음 캔들부터 봤을 때 첫 음봉의 인덱스(0-based, 진입 다음 N번째). 없으면 None."""
    for i, c in enumerate(candles_after_entry):
        if _is_red(c):
            return i
    return None


def detect_trailing_drop(
    candles_after_entry: list[dict],
    entry_price: float,
    threshold: float = config.TRAILING_DROP_THRESHOLD,
) -> int | None:
    """고점 추적: 시작 시점 이후의 종가들 중 max를 갱신해가며,
    종가가 max * (1 - threshold) 아래로 내려가면 그 인덱스 반환."""
    peak = entry_price
    for i, c in enumerate(candles_after_entry):
        close = float(c["trade_price"])
        if close > peak:
            peak = close
        if close <= peak * (1.0 - threshold):
            return i
    return None


def detect_below_entry(candles_after_entry: list[dict], entry_price: float) -> int | None:
    """종가가 진입가 미만으로 떨어진 첫 인덱스."""
    for i, c in enumerate(candles_after_entry):
        if float(c["trade_price"]) < entry_price:
            return i
    return None


def detect_consecutive_red(
    candles_after_entry: list[dict],
    n: int = config.CONSECUTIVE_RED_DAYS,
) -> int | None:
    """연속 N일 음봉이 처음 발생한 마지막 인덱스(즉 N번째 음봉 위치). 없으면 None."""
    if n <= 0:
        return None
    streak = 0
    for i, c in enumerate(candles_after_entry):
        if _is_red(c):
            streak += 1
            if streak >= n:
                return i
        else:
            streak = 0
    return None


def evaluate_all(
    candles_after_entry: list[dict],
    entry_price: float,
    *,
    trailing_threshold: float = config.TRAILING_DROP_THRESHOLD,
    consecutive_n: int = config.CONSECUTIVE_RED_DAYS,
) -> dict[str, int | None]:
    """4가지 기준 동시 평가. 각 키에 발생 인덱스 또는 None."""
    return {
        "first_red": detect_first_red(candles_after_entry),
        "trailing_drop": detect_trailing_drop(
            candles_after_entry, entry_price, threshold=trailing_threshold
        ),
        "below_entry": detect_below_entry(candles_after_entry, entry_price),
        "consecutive_red": detect_consecutive_red(
            candles_after_entry, n=consecutive_n
        ),
    }


def primary_close_reason(outcomes: dict[str, int | None]) -> tuple[str, int] | None:
    """가장 먼저 발생한 기준 = 1차 종료 사유. (criterion, days_to_trigger) 또는 None.
    여러 기준이 같은 인덱스에 동시 발생하면 우선순위:
    first_red > below_entry > trailing_drop > consecutive_red
    (보수적으로 빠른 신호에 가중)."""
    priority = ["first_red", "below_entry", "trailing_drop", "consecutive_red"]
    triggered = [(p, outcomes[p]) for p in priority if outcomes.get(p) is not None]
    if not triggered:
        return None
    triggered.sort(key=lambda x: (x[1], priority.index(x[0])))
    crit, idx = triggered[0]
    return crit, idx + 1  # days_to_trigger = 1-based (entry 다음 1일째 = 1)
