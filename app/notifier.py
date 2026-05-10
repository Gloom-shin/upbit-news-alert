"""Gmail SMTP 알림 — market-briefing/email_sender.py 패턴 차용.

App Password로 smtp.gmail.com:587 STARTTLS 인증.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str, *, html: bool = False) -> bool:
    if not (config.GMAIL_USER and config.GMAIL_APP_PASSWORD and config.EMAIL_RECIPIENT):
        logger.error("[Email] GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_RECIPIENT 미설정")
        return False
    if config.DRY_RUN:
        logger.info("[Email][DRY_RUN] %s\n---\n%s", subject, body[:300])
        return True

    msg = MIMEMultipart("alternative")
    msg["From"] = config.GMAIL_USER
    msg["To"] = config.EMAIL_RECIPIENT
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html" if html else "plain", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            s.sendmail(config.GMAIL_USER, [config.EMAIL_RECIPIENT], msg.as_string())
        logger.info("[Email] 전송 완료 → %s (%s)", config.EMAIL_RECIPIENT, subject)
        return True
    except Exception as e:
        logger.error("[Email] 전송 실패: %s", e)
        return False


def format_alert(
    *,
    grade: str,
    symbol: str,
    market: str,
    headline: str,
    summary: str,
    url: str,
    source: str,
    detected_price: float | None,
    avg_days_for_grade: float | None,
) -> tuple[str, str]:
    """이메일 제목/본문 생성. (subject, body) 반환."""
    subject = f"[upbit-알림][{grade}급] {symbol} - {headline[:50]}"
    price_line = f"{detected_price:,.0f} KRW" if detected_price else "—"
    avg_line = (
        f"{avg_days_for_grade:.1f}일"
        if isinstance(avg_days_for_grade, (int, float)) and avg_days_for_grade
        else "데이터 부족"
    )
    body = (
        f"등급: {grade}\n"
        f"종목: {symbol} ({market})\n"
        f"감지 가격: {price_line}\n"
        f"이 등급 과거 평균 상승 지속일: {avg_line}\n"
        f"\n— 호재 요약 —\n{summary[:500]}\n"
        f"\n— 출처 ({source}) —\n{url}\n"
    )
    return subject, body
