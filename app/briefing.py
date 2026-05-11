"""매일 아침 8 AM KST에 발송되는 코인 브리핑.

- 업비트 KRW 마켓 24시간 변화율 상위/하위 10
- Claude이 작성하는 시장 한줄평
- 최근 24시간 S/A급 알림 요약
- 2~3일 연속 상승 후보 종목
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from html import escape

from . import config, filter as flt, notifier, upbit

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _fetch_movers(top_n: int = 10) -> tuple[list[dict], list[dict]]:
    """업비트 전체 KRW 마켓 24시간 변화율 기준 상승률 TOP N / 하락률 TOP N."""
    markets = upbit.get_krw_markets()
    market_codes = [m["market"] for m in markets]
    name_map = {m["market"]: m.get("korean_name", m["market"]) for m in markets}

    # ticker는 한 번에 100개씩 분할 호출
    rows: list[dict] = []
    for i in range(0, len(market_codes), 100):
        batch = market_codes[i:i + 100]
        try:
            data = upbit._get("/ticker", params={"markets": ",".join(batch)})
            for r in data:
                rows.append({
                    "market": r["market"],
                    "name": name_map.get(r["market"], r["market"]),
                    "price": float(r["trade_price"]),
                    "change_rate_24h": float(r.get("signed_change_rate", 0.0)),
                    "volume_krw_24h": float(r.get("acc_trade_price_24h", 0.0)),
                })
        except Exception as e:
            logger.warning("ticker batch 실패: %s", e)
        time.sleep(0.15)

    rows.sort(key=lambda x: x["change_rate_24h"], reverse=True)
    gainers = rows[:top_n]
    losers = list(reversed(rows[-top_n:]))
    return gainers, losers


def _fetch_recent_alerts(hours: int = 24) -> list[dict]:
    """최근 N시간 내 발송된 S/A급 알림 목록."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        with sqlite3.connect(str(config.DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT grade, symbol, headline, url, source, detected_price, created_at
                FROM news_events
                WHERE grade IN ('S', 'A')
                  AND created_at >= ?
                ORDER BY created_at DESC
                """,
                (cutoff,),
            )
            return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logger.warning("alerts DB 조회 실패: %s", e)
        return []


def _fetch_uptrend_picks() -> list[str]:
    """현재 2~3일 연속 상승 종목 (price_job과 동일 로직)."""
    try:
        return flt.find_uptrend_markets()
    except Exception as e:
        logger.warning("uptrend filter 실패: %s", e)
        return []


def _ai_commentary(gainers: list[dict], losers: list[dict], picks: list[str]) -> str:
    """Claude이 오늘의 시장 분위기를 2~3문단으로 한국어로 짧게 코멘트."""
    try:
        from anthropic import Anthropic
    except ImportError:
        return "(Claude SDK 미설치 — 코멘트 생략)"

    if not config.ANTHROPIC_API_KEY:
        return "(ANTHROPIC_API_KEY 미설정 — 코멘트 생략)"

    top5 = ", ".join(f"{g['name']}({g['change_rate_24h']*100:+.1f}%)" for g in gainers[:5])
    bot5 = ", ".join(f"{l['name']}({l['change_rate_24h']*100:+.1f}%)" for l in losers[:5])
    picks_str = ", ".join(p["symbol"] for p in picks[:10]) if picks else "(없음)"

    prompt = f"""다음은 오늘 한국시간 아침 8시 기준 업비트 KRW 마켓 데이터야.

상승률 TOP5: {top5}
하락률 TOP5: {bot5}
2~3일 연속 상승 중인 종목: {picks_str}

이 데이터를 보고 한국 크립토 트레이더에게 도움될 만한 시장 분위기 코멘트를 한국어로 2문단(각 2~3문장)으로 짧게 써줘.
- 첫 문단: 시장 전체 분위기 (강세/약세/혼조), 특징
- 둘째 문단: 주목할 만한 흐름이나 종목, 주의할 점
- 친근한 톤. 단정적 투자 조언은 피하고 관찰자 시점으로.
- 마크다운 포맷 금지, 순수 텍스트만."""

    try:
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning("Claude commentary 실패: %s", e)
        return f"(AI 코멘트 생성 실패: {e})"


def _format_html(gainers: list[dict], losers: list[dict], alerts: list[dict],
                 picks: list[str], commentary: str, now: datetime) -> str:
    def row_to_li(r: dict) -> str:
        pct = r["change_rate_24h"] * 100
        color = "#1a7f37" if pct >= 0 else "#cf222e"
        return (
            f'<li><span style="display:inline-block;width:90px">{escape(r["name"])}</span>'
            f'<span style="color:{color};font-weight:600">{pct:+.2f}%</span> '
            f'<span style="color:#666">{r["price"]:,.0f}원</span></li>'
        )

    gainers_html = "\n".join(row_to_li(r) for r in gainers) or "<li>—</li>"
    losers_html = "\n".join(row_to_li(r) for r in losers) or "<li>—</li>"
    picks_html = ", ".join(f"<b>{escape(p['symbol'])}</b> ({p['run_days']}일 연속)" for p in picks) if picks else "<i>해당 없음</i>"

    if alerts:
        alerts_html = "<ul>" + "".join(
            f'<li>[{escape(a["grade"])}급] <b>{escape(a["symbol"])}</b> — '
            f'<a href="{escape(a["url"])}">{escape(a["headline"][:60])}</a></li>'
            for a in alerts
        ) + "</ul>"
    else:
        alerts_html = "<i>지난 24시간 S/A급 알림 없음</i>"

    commentary_html = "<br>".join(escape(line) for line in commentary.split("\n") if line.strip())

    return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:640px;margin:0 auto;padding:20px;color:#1f2328">
<h2 style="margin:0 0 8px">📈 오늘의 업비트 브리핑</h2>
<p style="color:#666;margin:0 0 20px">{now.strftime('%Y년 %m월 %d일 (%A)')} · 한국시간 {now.strftime('%H:%M')}</p>

<div style="background:#f6f8fa;padding:16px;border-radius:8px;margin:16px 0">
<h3 style="margin:0 0 8px">💬 AI 시장 코멘트</h3>
<p style="margin:0;line-height:1.6">{commentary_html}</p>
</div>

<h3>🎯 주목할 종목 (2~3일 연속 상승 중)</h3>
<p>{picks_html}</p>

<h3>🚨 지난 24시간 S/A급 알림</h3>
{alerts_html}

<div style="display:flex;gap:24px;margin-top:24px">
<div style="flex:1">
<h3 style="margin:0 0 8px">🚀 상승률 TOP 10</h3>
<ul style="list-style:none;padding:0;margin:0;font-family:ui-monospace,monospace;font-size:13px">
{gainers_html}
</ul>
</div>
<div style="flex:1">
<h3 style="margin:0 0 8px">📉 하락률 TOP 10</h3>
<ul style="list-style:none;padding:0;margin:0;font-family:ui-monospace,monospace;font-size:13px">
{losers_html}
</ul>
</div>
</div>

<hr style="border:none;border-top:1px solid #d0d7de;margin:24px 0">
<p style="color:#666;font-size:12px;margin:0">— upbit-news-alert · 매일 아침 8시 KST 발송 · <a href="https://github.com/Gloom-shin/upbit-news-alert">repo</a></p>
</body></html>"""


def run_briefing() -> bool:
    now = datetime.now(KST)
    logger.info("[briefing] 시작 %s", now.isoformat())

    gainers, losers = _fetch_movers(top_n=10)
    logger.info("[briefing] 상승 TOP1: %s (%.2f%%), 하락 TOP1: %s (%.2f%%)",
                gainers[0]["name"] if gainers else "-",
                (gainers[0]["change_rate_24h"] * 100) if gainers else 0,
                losers[0]["name"] if losers else "-",
                (losers[0]["change_rate_24h"] * 100) if losers else 0)

    alerts = _fetch_recent_alerts(hours=24)
    logger.info("[briefing] 최근 24h S/A 알림: %d건", len(alerts))

    picks = _fetch_uptrend_picks()
    logger.info("[briefing] 2~3일 연속 상승 후보: %d종목", len(picks))

    commentary = _ai_commentary(gainers, losers, picks)
    logger.info("[briefing] AI 코멘트 %d자 생성", len(commentary))

    html_body = _format_html(gainers, losers, alerts, picks, commentary, now)
    subject = f"📈 오늘의 업비트 브리핑 — {now.strftime('%Y-%m-%d')}"

    ok = notifier.send_email(subject, html_body, html=True)
    logger.info("[briefing] 이메일 발송 %s", "성공" if ok else "실패")
    return ok


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    )
    ok = run_briefing()
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
