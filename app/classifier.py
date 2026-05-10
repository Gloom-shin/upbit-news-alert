"""뉴스 호재 등급 분류 — Claude Haiku로 S/A/B/C 판정.

S: 메이저 거래소 신규 상장, 대형 파트너십(애플/MS/삼성 등 글로벌 빅테크), ETF 승인,
   공식 메인넷 런칭, 정책적 호재 (예: 미국 SEC 긍정적 판결)
A: 신규 토큰 에어드롭, 의미있는 기능 업그레이드, 중형 거래소 상장,
   유의미한 거래량/가격 펌핑 보도, 중형 파트너십
B: 단순 가격 상승 보도, 일상적 업데이트, 트위터 발언 수준
C: 단순 시세 / 뉴스 가치 미미 / 다른 종목 끼워팔기
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import anthropic

from . import config

logger = logging.getLogger(__name__)


@dataclass
class Classification:
    grade: str  # 'S' | 'A' | 'B' | 'C'
    reason: str

    @property
    def is_alert_worthy(self) -> bool:
        return self.grade in ("S", "A")


SYSTEM_PROMPT = """당신은 가상화폐 뉴스 영향도 평가자입니다. 주어진 한 건의 뉴스 헤드라인+요약을
다음 4단계 등급으로 분류하세요.

[등급 정의]
- S: 즉각 강한 호재. 예) 메이저 거래소(바이낸스/코인베이스/업비트) 신규 상장, ETF 승인,
     글로벌 빅테크와의 공식 파트너십, 공식 메인넷 메인 런칭, 큰 규모 정책 호재.
- A: 의미있는 호재. 예) 중형 거래소 상장, 토큰 에어드롭, 핵심 기능 업그레이드 발표,
     주요 미디어 단독 보도, 중요 파트너십.
- B: 약한 호재. 예) 단순 가격 상승 보도, 일상적 업데이트, 트위터 발언 수준.
- C: 호재 아님. 예) 단순 시세 보도, 다른 종목 끼워팔기, 출처 불명.

반드시 JSON으로만 응답하세요. 다른 텍스트는 절대 넣지 마세요.
{"grade": "S|A|B|C", "reason": "한 줄(최대 60자) 한국어 요약"}
"""


def _client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def classify(symbol: str, headline: str, summary: str) -> Classification:
    """Claude Haiku 호출. 실패 시 안전하게 'C' 반환."""
    user_msg = (
        f"[종목] {symbol}\n"
        f"[헤드라인] {headline}\n"
        f"[요약] {summary[:400]}\n\n"
        "위 뉴스를 등급 분류해 JSON으로만 답하세요."
    )
    try:
        resp = _client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        return _parse(text)
    except Exception as e:
        logger.warning("[classifier] Claude 호출 실패: %s", e)
        return Classification(grade="C", reason="classifier 호출 실패")


def _parse(text: str) -> Classification:
    """모델 응답에서 JSON 추출. 코드펜스(```) 포함도 처리."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return Classification(grade="C", reason="응답 파싱 실패")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return Classification(grade="C", reason="JSON 파싱 실패")
    grade = str(data.get("grade", "C")).upper().strip()
    if grade not in ("S", "A", "B", "C"):
        grade = "C"
    reason = str(data.get("reason", "")).strip()[:80] or "사유 미제공"
    return Classification(grade=grade, reason=reason)
