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

    # 핵심 KPI 4개 (큰 숫자 카드)
    total_events = len(events)
    closed_events = sum(1 for e in events if e.get("closed_at"))
    all_returns = []
    for ev in events:
        entry = ev.get("entry_price")
        cur = current_price_map.get(ev["market"]) if not ev.get("closed_at") else (ev.get("peak_price") or entry)
        r = _compute_return_pct(entry, cur)
        if r is not None:
            all_returns.append(r)
    avg_return = mean(all_returns) if all_returns else None
    avg_return_color = pct_color(avg_return)

    def kpi_card(label, value, color="#1f2328"):
        return f"""
        <div style="background:#fff;border:1px solid #e7e9eb;border-radius:8px;padding:12px 14px;flex:1;min-width:140px">
          <div style="font-size:11px;color:#888;letter-spacing:0.5px;font-weight:600">{escape(label)}</div>
          <div style="font-size:22px;font-weight:800;color:{color};margin-top:4px">{value}</div>
        </div>"""

    kpi_html = f"""
    <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px">
      {kpi_card("총 S/A 알림", str(total_events))}
      {kpi_card("종료된 이벤트", str(closed_events))}
      {kpi_card("평균 수익률", f"{avg_return:+.2f}%" if avg_return is not None else "—", avg_return_color)}
      {kpi_card("표본 충분?", "충분" if total_events >= 10 else f"{total_events}/10", "#d29922" if total_events < 10 else "#1a7f37")}
    </div>"""

    # 등급별 통계 카드
    def grade_card(g, theme_color, emoji):
        d = by_grade.get(g, {})
        n = len(d.get("events", []))
        rets = d.get("returns", [])
        days = d.get("days", [])
        avg_ret = mean(rets) if rets else None
        med_ret = median(rets) if rets else None
        avg_days = mean(days) if days else None

        if n == 0:
            stats = '<div style="color:#888;font-size:13px">아직 표본 없음</div>'
        else:
            stats = f"""
            <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:8px">
              <tr><td style="padding:4px 0;color:#666">표본</td><td style="text-align:right;font-weight:600">{n}건</td></tr>
              <tr><td style="padding:4px 0;color:#666">평균 수익률</td><td style="text-align:right;color:{pct_color(avg_ret)};font-weight:700">{avg_ret:+.2f}%</td></tr>
              <tr><td style="padding:4px 0;color:#666">중앙값</td><td style="text-align:right;color:{pct_color(med_ret)};font-weight:600">{med_ret:+.2f}%</td></tr>
              <tr><td style="padding:4px 0;color:#666">평균 보유일</td><td style="text-align:right;font-weight:600">{avg_days:.1f}일</td></tr>
            </table>""" if avg_ret is not None and med_ret is not None and avg_days is not None else f"""
            <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:8px">
              <tr><td style="padding:4px 0;color:#666">표본</td><td style="text-align:right;font-weight:600">{n}건</td></tr>
              <tr><td colspan="2" style="padding:6px 0;color:#888;font-size:12px">데이터 부족 (진행 중 또는 미종료)</td></tr>
            </table>"""

        return f"""
        <div style="background:#fff;border:1px solid #e7e9eb;border-left:4px solid {theme_color};border-radius:8px;padding:14px 16px;flex:1;min-width:200px">
          <div style="font-size:13px;font-weight:700;color:{theme_color}">{emoji} {g}급</div>
          {stats}
        </div>"""

    grades_html = f"""
    <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px">
      {grade_card("S", "#cf222e", "🔥")}
      {grade_card("A", "#d29922", "⚡")}
    </div>"""

    # 종료 기준별 카드
    crit_label = {
        "first_red": ("첫 음봉", "전일 대비 1번이라도 하락"),
        "trailing_drop": ("-7% 트레일링", "고점 대비 -7% 하락"),
        "below_entry": ("본전 이탈", "진입가 아래로 하락"),
        "consecutive_red": ("연속 음봉", "2일 연속 하락"),
    }
    crit_rows = []
    total_hits = sum(d.get("hits", 0) for d in by_crit.values())
    for c, (label, desc) in crit_label.items():
        d = by_crit.get(c, {"hits": 0, "days": []})
        hits = d["hits"]
        avg = f"{mean(d['days']):.1f}일" if d["days"] else "—"
        bar_pct = (hits / total_hits * 100) if total_hits > 0 else 0
        crit_rows.append(f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
            <span><b>{escape(label)}</b> <span style="color:#888;font-size:11px">{escape(desc)}</span></span>
            <span style="color:#666"><b>{hits}건</b> · 평균 {avg}</span>
          </div>
          <div style="background:#f0f0f0;border-radius:4px;height:6px;overflow:hidden">
            <div style="background:#d29922;height:100%;width:{bar_pct:.1f}%"></div>
          </div>
        </div>""")
    crit_html = "".join(crit_rows) if total_hits > 0 else '<div style="color:#888;font-size:13px;padding:12px;text-align:center">종료된 이벤트 없음 (모두 진행 중)</div>'

    # 이벤트 상세 카드들
    if events:
        event_cards = []
        for ev in events:
            entry = ev.get("entry_price")
            cur = current_price_map.get(ev["market"]) if not ev.get("closed_at") else (ev.get("peak_price") or entry)
            ret = _compute_return_pct(entry, cur)
            grade = ev["grade"]
            theme_color = "#cf222e" if grade == "S" else "#d29922"
            emoji = "🔥" if grade == "S" else "⚡"
            ret_str = f'<span style="color:{pct_color(ret)};font-weight:700">{ret:+.2f}%</span>' if ret is not None else "—"
            status_html = (
                f'<span style="background:#f6f8fa;color:#666;padding:2px 8px;border-radius:10px;font-size:11px">진행중 · {ev.get("days_held", "—")}일</span>'
                if not ev.get("closed_at")
                else f'<span style="background:#e7f0ff;color:#0969da;padding:2px 8px;border-radius:10px;font-size:11px">종료 · {ev.get("close_reason", "—")} · {ev.get("days_held", "—")}일</span>'
            )
            event_cards.append(f"""
            <div style="background:#fff;border:1px solid #e7e9eb;border-left:4px solid {theme_color};border-radius:6px;padding:12px 14px;margin-bottom:10px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                <div><span style="color:{theme_color};font-weight:700;font-size:11px">{emoji} {grade}급</span> <b style="font-size:14px;margin-left:6px">{escape(ev["symbol"])}</b></div>
                <div>{ret_str}</div>
              </div>
              <a href="{escape(ev.get("url", "#"))}" style="font-size:13px;color:#0969da;text-decoration:none;display:block;margin-bottom:6px">{escape((ev.get("headline") or "")[:80])}</a>
              <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#666">
                <span>진입 {entry:,.0f}원</span>
                {status_html}
              </div>
            </div>""")
        events_html = "".join(event_cards)
    else:
        events_html = '<div style="background:#f6f8fa;border-radius:6px;padding:20px;text-align:center;color:#888;font-size:13px">지난 기간 S/A급 알림 없음. 표본 더 쌓이는 중.</div>'

    insight_html = "<br>".join(escape(line) for line in insight.split("\n") if line.strip())

    return f"""<html><body style="margin:0;padding:0;background:#f6f8fa;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;color:#1f2328">
<div style="max-width:680px;margin:0 auto;padding:24px 16px">

  <!-- 헤더 -->
  <div style="margin-bottom:18px">
    <div style="font-size:12px;color:#666;letter-spacing:1px;font-weight:600">📊 WEEKLY REPORT</div>
    <h1 style="margin:4px 0 6px;font-size:24px">주간 알림 효과 검증</h1>
    <p style="margin:0;color:#666;font-size:13px">{now.strftime('%Y-%m-%d')} · 지난 {days_window}일간 데이터</p>
  </div>

  <!-- KPI -->
  {kpi_html}

  <!-- AI 인사이트 -->
  <div style="background:#fff;border:1px solid #e7e9eb;border-radius:10px;padding:18px 20px;margin-bottom:20px">
    <div style="font-size:11px;color:#666;letter-spacing:1px;font-weight:600;margin-bottom:8px">🔍 AI 인사이트</div>
    <p style="margin:0;line-height:1.7;font-size:14px">{insight_html}</p>
  </div>

  <!-- 등급별 -->
  <div style="font-size:11px;color:#666;letter-spacing:1px;font-weight:600;margin-bottom:10px">📈 등급별 성과</div>
  {grades_html}

  <!-- 종료 기준 -->
  <div style="background:#fff;border:1px solid #e7e9eb;border-radius:10px;padding:18px 20px;margin-bottom:20px">
    <div style="font-size:11px;color:#666;letter-spacing:1px;font-weight:600;margin-bottom:12px">🎯 종료 기준 분포</div>
    {crit_html}
  </div>

  <!-- 이벤트 상세 -->
  <div style="font-size:11px;color:#666;letter-spacing:1px;font-weight:600;margin-bottom:10px">📋 알림 상세 ({total_events}건)</div>
  {events_html}

  <hr style="border:none;border-top:1px solid #d0d7de;margin:24px 0">
  <p style="color:#888;font-size:11px;margin:0;line-height:1.6">
    — upbit-news-alert · 매주 일요일 23:00 KST · <a href="https://github.com/Gloom-shin/upbit-news-alert" style="color:#0969da">repo</a>
  </p>

</div>
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
