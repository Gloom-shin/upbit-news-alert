"""주간 알림 효과 검증 리포트.

S/A급 알림이 실제로 며칠간 상승했는지, 어떤 종료 기준이 가장 자주 발동했는지,
등급별 평균 수익률은 어땠는지 등을 HTML 이메일로 정리해서 발송.

스케줄: 매주 일요일 KST 23:00 (UTC 14:00)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from statistics import mean, median

from . import config, notifier, upbit

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _connect():
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_events(days: int) -> list[dict]:
    """지난 N일간 S/A급 이벤트 + 추적/종료 정보 결합."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
      SELECT
        ne.id              AS event_id,
        ne.symbol, ne.market, ne.grade, ne.headline, ne.url, ne.source,
        ne.detected_price, ne.created_at,
        pt.id              AS pt_id,
        pt.entry_price, pt.peak_price,
        pt.started_at, pt.closed_at, pt.close_reason, pt.days_held
      FROM news_events ne
      LEFT JOIN price_tracking pt ON pt.news_event_id = ne.id
      WHERE ne.grade IN ('S', 'A')
        AND ne.created_at >= ?
      ORDER BY ne.created_at DESC
    """
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, (cutoff,)).fetchall()]


def _fetch_outcomes(event_ids: list[int]) -> dict[int, list[dict]]:
    """이벤트별 4기준 outcome 모음."""
    if not event_ids:
        return {}
    placeholders = ",".join("?" * len(event_ids))
    sql = f"""
      SELECT eo.*, pt.news_event_id
      FROM event_outcomes eo
      JOIN price_tracking pt ON pt.id = eo.price_tracking_id
      WHERE pt.news_event_id IN ({placeholders})
    """
    by_event: dict[int, list[dict]] = {}
    with _connect() as conn:
        for r in conn.execute(sql, event_ids).fetchall():
            by_event.setdefault(r["news_event_id"], []).append(dict(r))
    return by_event


def _current_prices(markets: list[str]) -> dict[str, float]:
    """미종료 이벤트의 현재가 fetch (배치)."""
    if not markets:
        return {}
    try:
        return upbit.get_ticker_prices(set(markets))
    except Exception as e:
        logger.warning("[report] 현재가 조회 실패: %s", e)
        return {}


def _compute_return_pct(entry: float | None, current: float | None) -> float | None:
    if not entry or not current or entry <= 0:
        return None
    return (current - entry) / entry * 100


def _aggregate_by_grade(events: list[dict], outcomes_by_event: dict[int, list[dict]],
                        current_price_map: dict[str, float]) -> dict[str, dict]:
    """등급(S/A)별 통계 집계."""
    by_grade: dict[str, dict] = {"S": {"events": [], "returns": [], "days": []},
                                 "A": {"events": [], "returns": [], "days": []}}
    for ev in events:
        g = ev["grade"]
        if g not in by_grade:
            continue
        by_grade[g]["events"].append(ev)
        # 수익률 계산
        if ev.get("closed_at"):
            # 종료된 이벤트: peak_price 또는 outcome의 triggered_price 활용
            close_price = ev.get("peak_price") or ev.get("entry_price")
            outs = outcomes_by_event.get(ev["event_id"], [])
            if outs:
                close_price = outs[0].get("triggered_price") or close_price
            ret = _compute_return_pct(ev.get("entry_price"), close_price)
            days = ev.get("days_held")
        else:
            # 미종료: 현재가 기준
            cur = current_price_map.get(ev["market"])
            ret = _compute_return_pct(ev.get("entry_price"), cur)
            try:
                start = datetime.fromisoformat(ev["started_at"].replace("Z", "+00:00")) if ev.get("started_at") else None
                days = (datetime.now(timezone.utc) - start).days if start else None
            except Exception:
                days = None
        if ret is not None:
            by_grade[g]["returns"].append(ret)
        if days is not None:
            by_grade[g]["days"].append(days)
    return by_grade


def _aggregate_by_criterion(outcomes_by_event: dict[int, list[dict]]) -> dict[str, dict]:
    """4기준별 통계 — 어떤 기준이 가장 자주 먼저 발동했는지, 평균 발동일."""
    criteria = ["first_red", "trailing_drop", "below_entry", "consecutive_red"]
    by_crit: dict[str, dict] = {c: {"hits": 0, "days": []} for c in criteria}
    for outs in outcomes_by_event.values():
        for o in outs:
            c = o.get("criterion")
            if c in by_crit:
                by_crit[c]["hits"] += 1
                if o.get("days_to_trigger") is not None:
                    by_crit[c]["days"].append(o["days_to_trigger"])
    return by_crit


def _ai_insight(by_grade: dict, by_crit: dict, total_events: int, days_window: int) -> str:
    """Claude이 요약 의견 생성."""
    if not config.ANTHROPIC_API_KEY:
        return "(ANTHROPIC_API_KEY 미설정 — 인사이트 생략)"
    try:
        from anthropic import Anthropic
    except ImportError:
        return "(Claude SDK 미설치)"

    # 요약용 데이터 직렬화
    summary = f"기간: 지난 {days_window}일 / 총 S/A 알림 {total_events}건\n"
    for g in ("S", "A"):
        d = by_grade.get(g, {})
        n = len(d.get("events", []))
        avg_ret = mean(d["returns"]) if d.get("returns") else None
        avg_days = mean(d["days"]) if d.get("days") else None
        summary += f"- {g}급: {n}건"
        if avg_ret is not None:
            summary += f", 평균 수익률 {avg_ret:+.2f}%"
        if avg_days is not None:
            summary += f", 평균 보유 {avg_days:.1f}일"
        summary += "\n"
    summary += "4기준별 발동 횟수: "
    summary += ", ".join(f"{c}={d['hits']}" for c, d in by_crit.items())

    prompt = f"""다음은 가상화폐 호재 알림 효과 검증 데이터야.

{summary}

이 데이터로 한국 크립토 트레이더에게 도움될 만한 짧은 의견을 한국어로 2~3문장으로 써줘.
- 표본이 부족하면 "표본 부족"이라고 솔직하게
- 단정적 투자 조언은 피하고 관찰 위주
- 마크다운 금지, 순수 텍스트만"""

    try:
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning("[report] Claude 인사이트 실패: %s", e)
        return f"(인사이트 생성 실패: {e})"


def _format_html(events: list[dict], by_grade: dict, by_crit: dict,
                 current_price_map: dict, insight: str,
                 days_window: int, now: datetime) -> str:
    def pct_color(p):
        if p is None: return "#666"
        return "#1a7f37" if p >= 0 else "#cf222e"

    # 이벤트 표
    if events:
        rows = []
        for ev in events:
            entry = ev.get("entry_price")
            cur = current_price_map.get(ev["market"]) if not ev.get("closed_at") else (ev.get("peak_price") or entry)
            ret = _compute_return_pct(entry, cur)
            status = "종료" if ev.get("closed_at") else "진행중"
            reason = ev.get("close_reason") or "—"
            days = ev.get("days_held") or "—"
            rows.append(
                f'<tr><td>{escape(ev["grade"])}</td>'
                f'<td><b>{escape(ev["symbol"])}</b></td>'
                f'<td><a href="{escape(ev["url"])}">{escape(ev["headline"][:50])}</a></td>'
                f'<td>{entry:,.0f}원</td>'
                f'<td style="color:{pct_color(ret)};font-weight:600">{ret:+.2f}%</td>'
                if ret is not None else
                f'<tr><td>{escape(ev["grade"])}</td>'
                f'<td><b>{escape(ev["symbol"])}</b></td>'
                f'<td><a href="{escape(ev["url"])}">{escape(ev["headline"][:50])}</a></td>'
                f'<td>{entry:,.0f}원</td>'
                f'<td>—</td>'
            )
            rows.append(f'<td>{days}일</td><td>{escape(status)}</td><td>{escape(reason)}</td></tr>')
        events_table = "<table style='border-collapse:collapse;width:100%;font-size:13px'>" + \
            "<tr style='background:#f6f8fa'><th style='padding:6px;text-align:left'>등급</th>" + \
            "<th style='padding:6px;text-align:left'>종목</th>" + \
            "<th style='padding:6px;text-align:left'>호재</th>" + \
            "<th style='padding:6px;text-align:right'>진입</th>" + \
            "<th style='padding:6px;text-align:right'>수익률</th>" + \
            "<th style='padding:6px;text-align:right'>보유일</th>" + \
            "<th style='padding:6px;text-align:left'>상태</th>" + \
            "<th style='padding:6px;text-align:left'>종료기준</th></tr>" + \
            "".join(rows) + "</table>"
    else:
        events_table = "<p><i>지난 기간 S/A급 알림 없음. 표본 더 쌓이는 중.</i></p>"

    # 등급별 통계
    def fmt_stat(g):
        d = by_grade.get(g, {})
        n = len(d.get("events", []))
        if n == 0:
            return f"<tr><td>{g}</td><td>0</td><td>—</td><td>—</td><td>—</td></tr>"
        rets = d.get("returns", [])
        days = d.get("days", [])
        avg_ret = f"{mean(rets):+.2f}%" if rets else "—"
        med_ret = f"{median(rets):+.2f}%" if rets else "—"
        avg_days = f"{mean(days):.1f}일" if days else "—"
        return f"<tr><td><b>{g}</b></td><td>{n}</td><td>{avg_ret}</td><td>{med_ret}</td><td>{avg_days}</td></tr>"

    grade_table = "<table style='border-collapse:collapse;font-size:13px'>" + \
        "<tr style='background:#f6f8fa'><th style='padding:6px'>등급</th><th style='padding:6px'>표본</th>" + \
        "<th style='padding:6px'>평균 수익률</th><th style='padding:6px'>중앙값</th><th style='padding:6px'>평균 보유</th></tr>" + \
        fmt_stat("S") + fmt_stat("A") + "</table>"

    # 종료 기준별
    crit_label = {
        "first_red": "첫 음봉",
        "trailing_drop": "-7% 트레일링",
        "below_entry": "본전 이탈",
        "consecutive_red": "연속 2일 음봉",
    }
    crit_rows = []
    for c, label in crit_label.items():
        d = by_crit.get(c, {"hits": 0, "days": []})
        hits = d["hits"]
        avg = f"{mean(d['days']):.1f}일" if d["days"] else "—"
        crit_rows.append(f"<tr><td>{label}</td><td>{hits}</td><td>{avg}</td></tr>")
    crit_table = "<table style='border-collapse:collapse;font-size:13px'>" + \
        "<tr style='background:#f6f8fa'><th style='padding:6px'>종료 기준</th><th style='padding:6px'>발동</th><th style='padding:6px'>평균 발동일</th></tr>" + \
        "".join(crit_rows) + "</table>"

    insight_html = "<br>".join(escape(line) for line in insight.split("\n") if line.strip())

    return f"""<html><body style="font-family:-apple-system,sans-serif;max-width:760px;margin:0 auto;padding:20px;color:#1f2328">
<h2 style="margin:0 0 8px">📊 주간 알림 효과 검증</h2>
<p style="color:#666;margin:0 0 24px">{now.strftime('%Y-%m-%d')} 기준 · 지난 {days_window}일간 데이터</p>

<div style="background:#f6f8fa;padding:16px;border-radius:8px;margin-bottom:20px">
<h3 style="margin:0 0 8px">🔍 인사이트</h3>
<p style="margin:0;line-height:1.6">{insight_html}</p>
</div>

<h3>📈 등급별 성과</h3>
{grade_table}

<h3 style="margin-top:24px">🎯 종료 기준 분포</h3>
{crit_table}
<p style="color:#888;font-size:12px;margin-top:4px">첫 음봉 = 한 번이라도 전일 대비 하락. -7% 트레일링 = 고점 대비 -7% 하락. 본전 이탈 = 진입가 이하. 연속 2일 음봉 = 연속 2일 하락.</p>

<h3 style="margin-top:24px">📋 알림 상세</h3>
{events_table}

<hr style="border:none;border-top:1px solid #d0d7de;margin:24px 0">
<p style="color:#666;font-size:12px;margin:0">— upbit-news-alert · 매주 일요일 23:00 KST</p>
</body></html>"""


def run_report(days_window: int = 7) -> bool:
    now = datetime.now(KST)
    logger.info("[report] 시작 %s, 지난 %d일", now.isoformat(), days_window)

    events = _fetch_events(days_window)
    logger.info("[report] S/A 이벤트 %d건 조회", len(events))

    outcomes_by_event = _fetch_outcomes([e["event_id"] for e in events])

    open_markets = [e["market"] for e in events if not e.get("closed_at") and e.get("market")]
    current_price_map = _current_prices(open_markets)

    by_grade = _aggregate_by_grade(events, outcomes_by_event, current_price_map)
    by_crit = _aggregate_by_criterion(outcomes_by_event)

    insight = _ai_insight(by_grade, by_crit, len(events), days_window)
    logger.info("[report] 인사이트 %d자", len(insight))

    html = _format_html(events, by_grade, by_crit, current_price_map, insight, days_window, now)
    subject = f"📊 주간 알림 효과 검증 — {now.strftime('%Y-%m-%d')}"

    ok = notifier.send_email(subject, html, html=True)
    logger.info("[report] 발송 %s", "성공" if ok else "실패")
    return ok


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
    return 0 if run_report() else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
