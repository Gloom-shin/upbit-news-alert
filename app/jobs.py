"""스케줄러가 실행하는 4개 잡.

- price_job        : 1시간마다 상승 종목 후보 갱신
- news_job         : 5분마다 RSS 수집 → 후보 종목과 매칭 → 등급 분류
                     → S/A급 + 윈도우 안이면 즉시 발송, 윈도우 밖이면 pending
- drain_queue_job  : 윈도우 시작 시각마다 pending 큐 일괄 발송
- track_job        : 매일 자정에 활성 트래킹 4기준 평가 → 종료된 것 정리
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from . import alert_window, config, db, news, tracker
from .classifier import classify
from .filter import find_uptrend_markets
from .notifier import format_alert, send_email
from .upbit import (
    get_daily_candles,
    get_ticker_prices,
    normalize_candles_oldest_first,
)

logger = logging.getLogger(__name__)

# 인메모리 후보 캐시 (price_job이 채우고 news_job이 사용)
_active_candidates: dict[str, dict] = {}


def get_active_candidates() -> dict[str, dict]:
    return dict(_active_candidates)


# ── 1) 가격 모니터링 ─────────────────────────────────────────────────


def price_job() -> None:
    logger.info("[price_job] 시작")
    candidates = find_uptrend_markets()
    _active_candidates.clear()
    for c in candidates:
        _active_candidates[c["symbol"]] = c
    logger.info(
        "[price_job] 완료 — 상승 후보 %d종목: %s",
        len(candidates),
        ", ".join(c["symbol"] for c in candidates[:20]),
    )


# ── 2) 뉴스 수집 + 매칭 + 등급 + 알림 ──────────────────────────────


def news_job() -> None:
    logger.info("[news_job] 시작")
    if not _active_candidates:
        logger.info("[news_job] 후보 종목 없음 — price_job 1회 강제 실행")
        try:
            price_job()
        except Exception as e:
            logger.warning("[news_job] price_job 실행 실패: %s", e)
            return

    candidate_symbols = set(_active_candidates.keys())
    items = news.fetch_all()
    logger.info("[news_job] RSS %d건 수집", len(items))

    now = datetime.now()
    in_window = alert_window.is_in_window(now)
    window_label = alert_window.current_window_label(now)
    if in_window:
        logger.info("[news_job] 알림 윈도우 %s — 즉시 발송 모드", window_label)
    else:
        logger.info("[news_job] 알림 윈도우 밖 — S/A 발견 시 pending 큐로 저장")

    grade_avg_days = _grade_avg_days_cache()
    sent = 0
    pending = 0
    for item in items:
        matched = news.detect_symbols(item.full_text, candidate_symbols)
        if not matched:
            continue
        for symbol in matched:
            cand = _active_candidates.get(symbol)
            if not cand:
                continue
            cls = classify(symbol, item.title, item.summary)
            event_id = db.insert_news_event(
                symbol=symbol,
                market=cand["market"],
                grade=cls.grade,
                headline=item.title,
                summary=item.summary,
                url=item.url,
                source=item.source,
                detected_price=cand["last_close"],
                classified_reason=cls.reason,
            )
            if event_id is None:
                # 동일 URL 이미 처리됨
                continue
            logger.info(
                "[news_job] 매칭 %s/%s [%s] %s",
                cand["market"], symbol, cls.grade, item.title[:60],
            )
            if not cls.is_alert_worthy:
                continue
            if not in_window:
                # pending 큐로만 적재 (notified=0 유지). drain_queue_job이 발송.
                pending += 1
                logger.info("[news_job] pending 적재 → %s/%s [%s]",
                            cand["market"], symbol, cls.grade)
                continue
            ok = _send_one(
                event_id=event_id,
                grade=cls.grade,
                symbol=symbol,
                market=cand["market"],
                headline=item.title,
                summary=item.summary,
                url=item.url,
                source=item.source,
                detected_price=cand["last_close"],
                avg_days_for_grade=grade_avg_days.get(cls.grade),
                run_days=cand.get("run_days"),
                reason=cls.reason,
            )
            if ok:
                sent += 1
    logger.info("[news_job] 완료 — 즉시 발송 %d건, pending 적재 %d건", sent, pending)


def _send_one(
    *,
    event_id: int,
    grade: str,
    symbol: str,
    market: str,
    headline: str,
    summary: str,
    url: str,
    source: str,
    detected_price: float | None,
    avg_days_for_grade: float | None,
    run_days: int | None,
    reason: str,
) -> bool:
    """이메일 1건 발송 + DB 업데이트 (mark_notified + open_price_tracking).

    윈도우 안 즉시 발송과 drain_queue_job 모두에서 사용.
    """
    subject, body, is_html = format_alert(
        grade=grade,
        symbol=symbol,
        market=market,
        headline=headline,
        summary=summary,
        url=url,
        source=source,
        detected_price=detected_price,
        avg_days_for_grade=avg_days_for_grade,
        run_days=run_days,
        reason=reason,
    )
    if not send_email(subject, body, html=is_html):
        return False
    db.mark_notified(event_id)
    if detected_price is not None:
        db.open_price_tracking(
            news_event_id=event_id,
            symbol=symbol,
            market=market,
            entry_price=detected_price,
        )
    return True


# ── 2.5) 큐 드레인 — 윈도우 시작 시각마다 pending S/A 일괄 발송 ────────


def drain_queue_job() -> None:
    logger.info("[drain_queue_job] 시작")
    pending_rows = db.get_pending_alerts(within_hours=config.PENDING_TTL_HOURS)
    if not pending_rows:
        logger.info("[drain_queue_job] pending 없음")
        return

    grade_avg_days = _grade_avg_days_cache()
    candidates = _active_candidates  # run_days 보강용 (없으면 None 허용)
    sent = 0
    for row in pending_rows:
        symbol = row["symbol"]
        cand = candidates.get(symbol) or {}
        ok = _send_one(
            event_id=row["id"],
            grade=row["grade"],
            symbol=symbol,
            market=row["market"] or "",
            headline=row["headline"],
            summary=row["summary"] or "",
            url=row["url"],
            source=row["source"] or "",
            detected_price=row["detected_price"],
            avg_days_for_grade=grade_avg_days.get(row["grade"]),
            run_days=cand.get("run_days"),
            reason=row["classified_reason"] or "",
        )
        if ok:
            sent += 1
    logger.info("[drain_queue_job] 완료 — %d건 발송 (대기 %d건 중)",
                sent, len(pending_rows))


def _grade_avg_days_cache() -> dict[str, float]:
    out: dict[str, float] = {}
    for row in db.stats_by_grade():
        if row.get("avg_days") is not None:
            out[row["grade"]] = float(row["avg_days"])
    return out


# ── 3) 일일 추적 — 4기준 평가 ──────────────────────────────────────


def track_job() -> None:
    logger.info("[track_job] 시작")
    active = db.get_active_trackings()
    if not active:
        logger.info("[track_job] 활성 트래킹 없음")
        return

    markets = [t["market"] for t in active]
    try:
        ticker = get_ticker_prices(markets)
    except Exception as e:
        logger.warning("[track_job] 시세 조회 실패: %s", e)
        ticker = {}

    closed = 0
    for t in active:
        try:
            _evaluate_one(t, ticker.get(t["market"]))
        except Exception as e:
            logger.warning("[track_job] %s 평가 실패: %s", t["market"], e)
            continue
        # 다시 조회 — 종료됐는지 확인
    closed = sum(1 for r in db.get_active_trackings() if r["closed_at"] is not None)
    logger.info("[track_job] 완료 — 활성 %d개, 이번 사이클 종료 %d개",
                len(active), closed)


def _evaluate_one(tracking_row, current_price: float | None) -> None:
    market = tracking_row["market"]
    entry_price = float(tracking_row["entry_price"])
    started_at = datetime.fromisoformat(tracking_row["started_at"])
    days_since_entry = max(1, (datetime.now() - started_at).days)

    raw = get_daily_candles(market, count=days_since_entry + 5)
    candles = normalize_candles_oldest_first(raw)
    # 진입 시점 캔들은 제외 — 진입 이후의 캔들만 사용
    after_entry = candles[-days_since_entry:] if days_since_entry < len(candles) else candles[1:]

    if not after_entry:
        return

    # 고점 갱신
    new_peak = max(float(c["trade_price"]) for c in after_entry)
    if new_peak > float(tracking_row["peak_price"]):
        db.update_peak(tracking_row["id"], new_peak)

    outcomes = tracker.evaluate_all(
        after_entry,
        entry_price=entry_price,
        trailing_threshold=config.TRAILING_DROP_THRESHOLD,
        consecutive_n=config.CONSECUTIVE_RED_DAYS,
    )

    # 4가지 모두 발생한 것은 each 별도로 기록
    for crit, idx in outcomes.items():
        if idx is None:
            continue
        triggered_close = float(after_entry[idx]["trade_price"])
        db.record_outcome(
            tracking_id=tracking_row["id"],
            criterion=crit,
            triggered_price=triggered_close,
            days_to_trigger=idx + 1,
        )

    primary = tracker.primary_close_reason(outcomes)
    if primary is not None:
        crit, days = primary
        db.close_tracking(
            tracking_id=tracking_row["id"],
            close_reason=crit,
            days_held=days,
        )


# ── 단발 실행 헬퍼 (CLI --once) ─────────────────────────────────────


def run_once(name: str) -> None:
    {
        "price": price_job,
        "news": news_job,
        "track": track_job,
        "drain": drain_queue_job,
    }[name]()
