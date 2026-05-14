"""Gmail SMTP 알림 — HTML 알림 카드 + 일반 텍스트 fallback.

App Password로 smtp.gmail.com:587 STARTTLS 인증.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from . import config

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


# ── 한글 이름 매핑 캐시 (업비트 마켓 메타에서 조회) ────────────────
_korean_name_cache: dict[str, str] = {}


def _get_korean_name(market: str) -> str:
    """KRW-BTC → '비트코인'. 못 찾으면 빈 문자열."""
    global _korean_name_cache
    if not _korean_name_cache:
        try:
            from . import upbit
            for m in upbit.get_krw_markets():
                _korean_name_cache[m["market"]] = m.get("korean_name", "")
        except Exception as e:
            logger.warning("[notifier] 마켓 이름 조회 실패: %s", e)
    return _korean_name_cache.get(market, "")


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


# 등급별 색상 / 이모지 / 라벨
_GRADE_THEME = {
    "S": {"color": "#cf222e", "bg": "#ffe5e5", "emoji": "🔥", "label": "S급 호재"},
    "A": {"color": "#d29922", "bg": "#fff8e1", "emoji": "⚡", "label": "A급 호재"},
    "B": {"color": "#0969da", "bg": "#e3f0ff", "emoji": "💧", "label": "B급 호재"},
    "C": {"color": "#6e7781", "bg": "#f0f0f0", "emoji": "—",  "label": "참고"},
}


# 거래량 피크 시간대 (KST). (start_h, start_m, end_h, end_m, label, icon, description)
_PEAKS_KST: list[tuple[int, int, int, int, str, str, str]] = [
    (9, 0, 10, 0, "국내", "🇰🇷", "일봉 갱신 직후 · 당일 급등주 결정 시기"),
    (16, 0, 18, 0, "유럽/아시아", "🇪🇺", "박스권 돌파 시도 · 오후 수급 유입"),
    (22, 30, 25, 0, "미국", "🇺🇸", "거래량 최대 · BTC 무빙에 따른 급변동 (~익일 01:00)"),
    (2, 0, 4, 0, "심야 휩쏘", "🌙", "거래량은 적지만 청산성 급변동 빈번"),
]


def _next_peak_info(now_kst: datetime) -> tuple[int, str, str, str]:
    """현재 시각 기준으로 다음 피크 시작까지 (분, 라벨, 시각문자열, 설명)."""
    cur_min = now_kst.hour * 60 + now_kst.minute
    for sh, sm, _eh, _em, label, _icon, desc in _PEAKS_KST:
        pmin = sh * 60 + sm
        if pmin > cur_min:
            return pmin - cur_min, label, f"{sh:02d}:{sm:02d}", desc
    # 자정 넘어가야 가장 이른 피크
    sh, sm, _eh, _em, label, _icon, desc = _PEAKS_KST[0]
    pmin = sh * 60 + sm
    return (24 * 60 - cur_min) + pmin, label, f"{sh:02d}:{sm:02d}", desc


def _current_peak_label(now_kst: datetime) -> str | None:
    """현재 시각이 피크 구간 안이면 라벨 반환, 밖이면 None."""
    cur_min = now_kst.hour * 60 + now_kst.minute
    for sh, sm, eh, em, label, _icon, _desc in _PEAKS_KST:
        s = sh * 60 + sm
        e = eh * 60 + em
        # 미국처럼 자정 넘는 구간은 eh>=24로 표현됨
        if s <= cur_min < e:
            return label
        # 자정 넘는 구간의 다음날 부분 체크 (cur_min < e-1440)
        if e > 24 * 60 and cur_min < (e - 24 * 60):
            return label
    return None


def render_peak_times_block(now_kst: datetime) -> str:
    """4개 지역 피크 시간대 + 현재/다음 피크 강조 HTML 블록.

    format_alert와 briefing 양쪽에서 재사용. 외부 의존성은 datetime 하나뿐.
    """
    cur_peak = _current_peak_label(now_kst)
    next_min, next_label, next_time, _ = _next_peak_info(now_kst)

    if cur_peak:
        peak_status_html = (
            f"<b style='color:#cf222e'>지금 {escape(cur_peak)} 피크 진행 중</b> · "
            f"다음 피크 {escape(next_label)} {escape(next_time)} KST "
            f"({next_min}분 후)"
        )
    else:
        if next_min <= 30:
            tone = "color:#cf222e;font-weight:700"
            label_prefix = "곧 피크 진입"
        elif next_min <= 90:
            tone = "color:#d29922;font-weight:600"
            label_prefix = "다음 피크"
        else:
            tone = "color:#666"
            label_prefix = "다음 피크"
        peak_status_html = (
            f"<span style='{tone}'>{label_prefix}: "
            f"{escape(next_label)} {escape(next_time)} KST · "
            f"{next_min}분 후</span>"
        )

    peaks_rows = ""
    for sh, sm, eh, em, label, icon, desc in _PEAKS_KST:
        end_disp = f"{eh:02d}:{em:02d}" if eh < 24 else f"익일 {eh-24:02d}:{em:02d}"
        is_current = (label == cur_peak)
        is_next = (label == next_label) and not is_current
        if is_current:
            row_style = "background:#ffe5e5"
            badge = " <span style='font-size:10px;color:#cf222e;font-weight:700'>● NOW</span>"
        elif is_next:
            row_style = "background:#fff8e1"
            badge = " <span style='font-size:10px;color:#d29922;font-weight:700'>▶ NEXT</span>"
        else:
            row_style = ""
            badge = ""
        peaks_rows += (
            f"<tr style='{row_style}'>"
            f"<td style='padding:6px 8px;white-space:nowrap'>{icon} <b>{escape(label)}</b>{badge}</td>"
            f"<td style='padding:6px 8px;white-space:nowrap;color:#444'>{sh:02d}:{sm:02d}–{end_disp}</td>"
            f"<td style='padding:6px 8px;color:#666;font-size:11px'>{escape(desc)}</td>"
            f"</tr>"
        )

    return f"""
  <div style="background:#fafbfc;border:1px solid #d0d7de;border-radius:6px;padding:12px 14px;margin-bottom:18px">
    <div style="font-size:11px;font-weight:700;color:#57606a;letter-spacing:1px;margin-bottom:8px">
      ⏰ 거래량 피크 시간대 (KST)
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      {peaks_rows}
    </table>
    <div style="margin-top:10px;padding-top:8px;border-top:1px dashed #d0d7de;font-size:12px">
      {peak_status_html}
    </div>
  </div>"""


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
    run_days: int | None = None,
    reason: str | None = None,
) -> tuple[str, str, bool]:
    """이메일 제목 / HTML 본문 / html=True flag 반환. (subject, body, is_html)

    호환을 위해 send_email은 알아서 html 플래그 처리.
    호출자는 그냥 `subject, body, is_html = format_alert(...); send_email(subject, body, html=is_html)`.
    """
    theme = _GRADE_THEME.get(grade, _GRADE_THEME["B"])
    korean = _get_korean_name(market)
    now_kst = datetime.now(KST)

    # 제목: 가독성 위해 이모지 + 종목 한글 이름 포함
    name_str = f"{korean}({symbol})" if korean else symbol
    subject = f"{theme['emoji']} [{grade}급] {name_str} · {headline[:40]}"

    # 가격/통계 라인
    price_str = f"{detected_price:,.0f}원" if detected_price else "—"
    avg_str = (
        f"{avg_days_for_grade:.1f}일"
        if isinstance(avg_days_for_grade, (int, float)) and avg_days_for_grade
        else "데이터 부족"
    )
    run_str = f"{run_days}일 연속 상승 중" if run_days else "—"

    # 외부 링크
    upbit_url = f"https://upbit.com/exchange?code=CRIX.UPBIT.{market}"
    cryptoquant_url = f"https://cryptoquant.com/asset/{symbol.lower()}/summary"

    # 거래량 피크 시간대 블록 (briefing과 공통)
    peak_block_html = render_peak_times_block(now_kst)

    # HTML 본문
    body = f"""<html><body style="margin:0;padding:0;background:#f6f8fa;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#1f2328">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">

  <!-- 등급 배지 -->
  <div style="background:{theme['bg']};border-left:6px solid {theme['color']};border-radius:8px;padding:18px 20px;margin-bottom:18px">
    <div style="font-size:12px;font-weight:700;color:{theme['color']};letter-spacing:1px">{escape(theme['label'])}</div>
    <div style="font-size:24px;font-weight:800;color:#1f2328;margin-top:4px">
      {escape(korean) if korean else escape(symbol)}
      <span style="font-size:14px;color:#888;font-weight:500">{escape(symbol)}</span>
    </div>
  </div>

  <!-- 헤드라인 -->
  <h2 style="margin:0 0 8px;font-size:18px;line-height:1.5">{escape(headline)}</h2>
  <p style="margin:0 0 16px;color:#666;font-size:13px">
    출처 <b>{escape(source)}</b> · {now_kst.strftime('%Y-%m-%d %H:%M KST')}
  </p>

  <!-- 호재 요약 박스 -->
  <div style="background:#f6f8fa;border-radius:6px;padding:14px 16px;margin-bottom:16px;font-size:14px;line-height:1.6">
    {escape(summary[:500])}
  </div>

  <!-- 핵심 정보 테이블 -->
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:18px">
    <tr>
      <td style="padding:8px 0;color:#666;width:140px">💰 감지 시점 가격</td>
      <td style="padding:8px 0;font-weight:600">{price_str}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#666">📈 상승 지속</td>
      <td style="padding:8px 0;font-weight:600">{run_str}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#666">📊 이 등급 과거 평균 보유일</td>
      <td style="padding:8px 0;font-weight:600">{avg_str}</td>
    </tr>
    {f'<tr><td style="padding:8px 0;color:#666">🤖 분류 사유</td><td style="padding:8px 0">{escape(reason or "")}</td></tr>' if reason else ''}
  </table>

  <!-- 거래량 피크 시간대 -->
  {peak_block_html}
  <div style="margin:-12px 0 18px;font-size:11px;color:#888;line-height:1.5;padding:0 14px">
    이 알림은 피크 30분 전 윈도우에 발송됐어요. 피크 진입 전 검토하시고 진입 여부 판단하세요.
  </div>

  <!-- 빠른 액션 버튼 -->
  <div style="margin-bottom:18px">
    <a href="{escape(upbit_url)}" style="display:inline-block;background:{theme['color']};color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none;font-weight:600;font-size:13px;margin-right:6px">
      업비트에서 보기 ↗
    </a>
    <a href="{escape(url)}" style="display:inline-block;background:#fff;color:#0969da;border:1px solid #d0d7de;padding:9px 16px;border-radius:6px;text-decoration:none;font-weight:600;font-size:13px">
      원문 기사 ↗
    </a>
  </div>

  <hr style="border:none;border-top:1px solid #d0d7de;margin:24px 0">
  <p style="color:#888;font-size:11px;margin:0;line-height:1.6">
    이 알림은 자동 시스템이 호재 등급을 판단해 발송했어요. 투자 결정은 본인 판단으로.<br>
    upbit-news-alert · <a href="https://github.com/Gloom-shin/upbit-news-alert" style="color:#0969da">repo</a>
  </p>

</div>
</body></html>"""

    return subject, body, True


def format_alert_plain(
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
    """레거시 호환용 — 일반 텍스트 본문 (subject, body)."""
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
