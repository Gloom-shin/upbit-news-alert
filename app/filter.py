"""상승 종목 필터: N일 연속 일봉 양봉(close > open) + 직전 일자가 음봉(연속 시작점 검증)."""
from __future__ import annotations

import logging
from typing import Iterable

from . import config
from .upbit import (
    get_daily_candles,
    get_krw_markets,
    normalize_candles_oldest_first,
)

logger = logging.getLogger(__name__)


def is_bullish_candle(c: dict) -> bool:
    return float(c["trade_price"]) > float(c["opening_price"])


def consecutive_up_run(candles_oldest_first: list[dict]) -> int:
    """가장 최신 캔들부터 거꾸로 봐서 연속 양봉이 몇 개인지."""
    run = 0
    for c in reversed(candles_oldest_first):
        if is_bullish_candle(c):
            run += 1
        else:
            break
    return run


def is_uptrend_candidate(candles_oldest_first: list[dict]) -> tuple[bool, int]:
    """연속 상승 일수가 [MIN..MAX] 구간에 있는지. (해당 여부, 연속일수)"""
    run = consecutive_up_run(candles_oldest_first)
    ok = config.MIN_CONSECUTIVE_UP_DAYS <= run <= config.MAX_CONSECUTIVE_UP_DAYS
    return ok, run


def find_uptrend_markets(markets: Iterable[str] | None = None) -> list[dict]:
    """상승 후보 종목 리스트. 각 항목 dict: {market, run_days, last_close, last_open}."""
    if markets is None:
        markets = [m["market"] for m in get_krw_markets()]

    results: list[dict] = []
    for market in markets:
        try:
            raw = get_daily_candles(market, count=config.MAX_CONSECUTIVE_UP_DAYS + 2)
            candles = normalize_candles_oldest_first(raw)
            if len(candles) < config.MIN_CONSECUTIVE_UP_DAYS:
                continue
            ok, run = is_uptrend_candidate(candles)
            if not ok:
                continue
            last = candles[-1]
            results.append(
                {
                    "market": market,
                    "symbol": market.split("-", 1)[1],
                    "run_days": run,
                    "last_close": float(last["trade_price"]),
                    "last_open": float(last["opening_price"]),
                    "last_date": last.get("candle_date_time_kst", ""),
                }
            )
        except Exception as e:  # 단일 종목 실패가 전체를 막지 않도록
            logger.warning("[filter] %s 분석 실패: %s", market, e)
            continue

    return results
