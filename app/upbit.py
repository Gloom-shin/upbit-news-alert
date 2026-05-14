"""업비트 공개 API 래퍼 (인증 불필요). coin-trader/src/exchange/upbit.py 참고.

Rate limit 방어:
- Upbit Quotation API 한도: 10 req/sec, 600 req/min per IP
- 프로세스 전역 throttle(_MIN_INTERVAL_SEC)로 호출 간격 보장
- 429 응답 시 지수 백오프(1s/2s/4s)로 최대 3회 재시도
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.upbit.com/v1"
DEFAULT_TIMEOUT = 10

# 호출 간 최소 간격(초). 10 req/sec 한도 대비 안전 마진 확보 → ~8 req/sec
_MIN_INTERVAL_SEC = 0.12
_throttle_lock = threading.Lock()
_last_call_ts = 0.0


def _throttle() -> None:
    """프로세스 전역 throttle. 마지막 호출 후 _MIN_INTERVAL_SEC가 지나지 않았으면 sleep."""
    global _last_call_ts
    with _throttle_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SEC - (now - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.monotonic()


def _get(path: str, params: dict | None = None, *, max_retries: int = 3) -> list | dict:
    """GET 호출 + throttle + 429 재시도."""
    url = f"{BASE_URL}{path}"
    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        _throttle()
        resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()  # 마지막 시도까지 실패 → 호출자에게 위임
            logger.warning("[upbit] 429 — %.1fs 후 재시도 (%d/%d) %s",
                           backoff, attempt, max_retries, path)
            time.sleep(backoff)
            backoff *= 2
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")  # for type-checker


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
