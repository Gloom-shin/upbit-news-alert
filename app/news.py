"""뉴스 수집 — 코인니스 / 토큰포스트 RSS. 종목 매칭 + 본문 일부 추출."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import feedparser
import requests
from bs4 import BeautifulSoup

from . import config

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    summary: str
    url: str
    source: str
    published: str = ""

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.summary}"


# ── RSS 수집 ─────────────────────────────────────────────────────────


def _fetch_rss(url: str, source: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "upbit-news-alert/0.1"})
    except Exception as e:
        logger.warning("[news] %s RSS 파싱 실패: %s", source, e)
        return items

    for entry in parsed.entries[:30]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary_html = entry.get("summary") or entry.get("description") or ""
        summary = _strip_html(summary_html)[:500]
        items.append(
            NewsItem(
                title=title,
                summary=summary,
                url=link,
                source=source,
                published=entry.get("published", ""),
            )
        )
    return items


def _strip_html(s: str) -> str:
    if not s:
        return ""
    try:
        return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", s).strip()


def fetch_coinness() -> list[NewsItem]:
    return _fetch_rss(config.COINNESS_RSS, "coinness")


def fetch_tokenpost() -> list[NewsItem]:
    return _fetch_rss(config.TOKENPOST_RSS, "tokenpost")


def fetch_all() -> list[NewsItem]:
    return fetch_coinness() + fetch_tokenpost()


# ── 종목 매칭 ────────────────────────────────────────────────────────

# 한글 코인 별칭 → 심볼 매핑 (우선 자주 거론되는 것만, 필요 시 확장)
KOREAN_NAME_TO_SYMBOL = {
    "비트코인": "BTC",
    "이더리움": "ETH",
    "리플": "XRP",
    "도지": "DOGE",
    "도지코인": "DOGE",
    "솔라나": "SOL",
    "에이다": "ADA",
    "카르다노": "ADA",
    "트론": "TRX",
    "폴카닷": "DOT",
    "체인링크": "LINK",
    "라이트코인": "LTC",
    "이더리움클래식": "ETC",
    "스텔라루멘": "XLM",
    "스텔라": "XLM",
    "비트코인캐시": "BCH",
    "이오스": "EOS",
    "샌드박스": "SAND",
    "디센트럴랜드": "MANA",
    "유니스왑": "UNI",
    "앱토스": "APT",
    "아발란체": "AVAX",
    "코스모스": "ATOM",
    "수이": "SUI",
    "톤코인": "TON",
    "톤": "TON",
    "시바이누": "SHIB",
    "페페": "PEPE",
    "위믹스": "WEMIX",
    "클레이": "KLAY",
    "클레이튼": "KLAY",
    "체인": "XCHAIN",
}


def detect_symbols(text: str, candidate_symbols: set[str]) -> set[str]:
    """텍스트에서 후보 종목 심볼 또는 한글 별칭이 언급되었는지 검출.
    반환: 매칭된 심볼 집합."""
    matched: set[str] = set()
    upper = text.upper()
    # 1) 영문 심볼 직접 매칭 — 단어 경계 사용
    for sym in candidate_symbols:
        if re.search(rf"(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])", upper):
            matched.add(sym)
    # 2) 한글 별칭 매칭
    for korean, sym in KOREAN_NAME_TO_SYMBOL.items():
        if sym in candidate_symbols and korean in text:
            matched.add(sym)
    return matched


def is_news_url_alive(url: str, timeout: int = 5) -> bool:
    """배달 직전 URL 살아있는지 가벼운 HEAD 체크 (선택)."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False
