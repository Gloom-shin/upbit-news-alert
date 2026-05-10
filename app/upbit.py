"""업비트 공개 API 래퍼 (인증 불필요). coin-trader/src/exchange/upbit.py 참고."""
from __future__ import annotations

import logging
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.upbit.com/v1"
DEFAULT_TIMEOUT = 10


def _get(path: str, params: dict | None = None) -> list | dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_krw_markets() -> list[dict]:
    """KRW 마켓 종목 목록. [{market: 'KRW-BTC', korean_name, english_name, ...}, ...]"""
    markets = _get("/market/all", params={"isDetails": "false"})
    return [m for m in markets if isinstance(m, dict) and m.get("market", "").startswith("KRW-")]


def get_daily_candles(market: str, count: int = 5) -> list[dict]:
    """일봉. 최신 → 과거 순으로 정렬되어 반환됨.
    Upbit candle 구조 키: opening_price, trade_price(=close), high_price, low_price, candle_date_time_kst.
    """
    return _get("/candles/days", params={"market": market, "count": count})


def get_ticker_prices(markets: Iterable[str]) -> dict[str, float]:
    """현재가 일괄 조회. {market: trade_price}"""
    markets_param = ",".join(markets)
    data = _get("/ticker", params={"markets": markets_param})
    return {row["market"]: float(row["trade_price"]) for row in data}


def normalize_candles_oldest_first(candles: list[dict]) -> list[dict]:
    """업비트 응답은 최신 → 과거 순. 분석 편의를 위해 오래된 것 → 최신 순으로 뒤집어 돌려준다."""
    return list(reversed(candles))
