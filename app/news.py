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

# 모호하거나 생태계/일반어로 자주 쓰여 오탐을 일으키는 키워드는 단독 매칭 금지.
# 예: "코스모스"는 SEI/NEAR/INJ 같은 Cosmos SDK 체인 뉴스에서 자주 등장 → ATOM 오탐
# "스텔라"는 일반 명사로 게임/회사명에도 쓰임. "톤"도 메신저 텔레그램 외 잡소음 가능.
_AMBIGUOUS_KOREAN_NAMES = {
    "코스모스",   # Cosmos는 생태계 이름이기도 함 (ATOM ≠ Cosmos 생태계)
    "톤",         # 짧음 + 일반어 (TON)
    "스텔라",     # 일반 명사
    "체인",       # 일반 명사
    "샌드박스",   # 일반 명사 (SAND ≠ 게임 샌드박스)
}

# 사용자 정의 한글 별명 — 업비트 korean_name과 별개로 자주 쓰이는 별칭
_EXTRA_KOREAN_ALIASES: dict[str, str] = {
    "비트코인": "BTC",
    "도지코인": "DOGE",
    "도지": "DOGE",
    "리플": "XRP",
    "라이트코인": "LTC",
    "이더리움클래식": "ETC",
    "스텔라루멘": "XLM",
    "비트코인캐시": "BCH",
    "유니스왑": "UNI",
    "이오스": "EOS",
    "시바이누": "SHIB",
    "톤코인": "TON",
    "클레이튼": "KLAY",
}


def _build_name_to_symbol_map(candidate_symbols: set[str]) -> dict[str, str]:
    """업비트 KRW 마켓 리스트에서 한글/영문 이름 → 심볼 동적 매핑 빌드.
    candidate_symbols(현재 상승 후보)에 속한 것만 포함."""
    name_map: dict[str, str] = {}

    # 1) Upbit가 제공하는 공식 korean_name / english_name
    try:
        from . import upbit  # 순환 임포트 회피 위해 함수 내부 임포트
        for m in upbit.get_krw_markets():
            symbol = m["market"].split("-", 1)[1]
            if symbol not in candidate_symbols:
                continue
            korean = (m.get("korean_name") or "").strip()
            english = (m.get("english_name") or "").strip()
            if korean and korean not in _AMBIGUOUS_KOREAN_NAMES and len(korean) >= 2:
                name_map[korean] = symbol
            if english and len(english) >= 3:
                # 영어 이름은 그대로 (대소문자 무시 매칭은 검출 함수에서)
                name_map[english.upper()] = symbol
    except Exception as e:
        logger.warning("[news] 동적 이름 매핑 빌드 실패: %s", e)

    # 2) 추가 별칭 (업비트 표준명에 없는 흔한 호칭)
    for name, sym in _EXTRA_KOREAN_ALIASES.items():
        if sym in candidate_symbols and name not in _AMBIGUOUS_KOREAN_NAMES:
            name_map[name] = sym
    return name_map


def detect_symbols(text: str, candidate_symbols: set[str]) -> set[str]:
    """텍스트에서 후보 종목 심볼/한글 정식명/영문명/별칭이 언급되었는지 검출.
    반환: 매칭된 심볼 집합. 오탐 방지를 위해 단어 경계와 모호어 필터 적용."""
    matched: set[str] = set()
    upper = text.upper()

    # 1) 영문 심볼 직접 매칭 — 단어 경계 사용 (대소문자 무시)
    for sym in candidate_symbols:
        if re.search(rf"(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])", upper):
            matched.add(sym)

    # 2) 한글/영문 이름 매칭 — 업비트 마켓 메타에서 동적으로 빌드
    name_map = _build_name_to_symbol_map(candidate_symbols)
    for name, sym in name_map.items():
        if name in _AMBIGUOUS_KOREAN_NAMES:
            continue
        # 한글은 그대로 부분 매칭, 영문은 단어 경계 매칭
        if re.match(r"^[A-Z0-9 ]+$", name):
            # 영문 이름
            if re.search(rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])", upper):
                matched.add(sym)
        else:
            # 한글/혼합 이름 — 부분 매칭이지만 길이 2 이상이라 노이즈 적음
            if name in text:
                matched.add(sym)

    return matched


def is_news_url_alive(url: str, timeout: int = 5) -> bool:
    """배달 직전 URL 살아있는지 가벼운 HEAD 체크 (선택)."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False
