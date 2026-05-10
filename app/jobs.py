"""스케줄러가 실행하는 3개 잡.

- price_job   : 1시간마다 상승 종목 후보 갱신
- news_job    : 5분마다 RSS 수집 → 후보 종목과 매칭 → 등급 분류 → S/A급 알림
- track_job   : 매일 자정에 활성 트래킹 4기준 평가 → 종료된 것 정리
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from . import config, db, news, tracker
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

    grade_avg_days = _grade_avg_days_cache()
    sent = 0
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
            if cls.is_alert_worthy:
                subject, body = format_alert(
                    grade=cls.grade,
                    symbol=symbol,
                    market=cand["market"],
                    headline=item.title,
                    summary=item.summary,
                    url=item.url,
                    source=item.source,
                    detected_price=cand["last_close"],
                    avg_days_for_grade=grade_avg_days.get(cls.grade),
                )
                if send_email(subject, body):
                    db.mark_notified(event_id)
                    db.open_price_tracking(
                        news_event_id=event_id,
                        symbol=symbol,
                        market=cand["market"],
                        entry_price=cand["last_close"],
                    )
                    sent += 1
    logger.info("[news_job] 완료 — 알림 발송 %d건", sent)


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
    }[name]()
