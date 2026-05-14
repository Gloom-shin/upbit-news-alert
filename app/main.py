"""CLI 진입점.

사용:
    python -m app.main                  # 스케줄러로 무한 실행
    python -m app.main --dry-run        # 키 없이 import OK 검증 + 부팅 로그만 찍고 종료
    python -m app.main --once price     # 1사이클만
    python -m app.main --once news
    python -m app.main --once track
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db, jobs


def _setup_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]

    log_path = config.ROOT / "data" / "app.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    # APScheduler 자체 로그는 INFO로 충분
    logging.getLogger("apscheduler").setLevel(logging.INFO)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="upbit-news-alert")
    p.add_argument("--dry-run", action="store_true",
                   help="환경 검증 + 모듈 import만 확인하고 종료")
    p.add_argument("--once", choices=["price", "news", "track", "drain", "briefing"],
                   help="해당 잡을 1회만 실행하고 종료")
    return p.parse_args(argv)


def run_scheduler() -> None:
    log = logging.getLogger("scheduler")
    config.validate(strict=True)
    db.init_db()

    sched = BlockingScheduler(timezone=config.TZ)
    sched.add_job(
        jobs.price_job,
        IntervalTrigger(minutes=config.PRICE_INTERVAL_MIN),
        id="price_monitor",
        next_run_time=None,
    )
    sched.add_job(
        jobs.news_job,
        IntervalTrigger(minutes=config.NEWS_INTERVAL_MIN),
        id="news_fetch",
        next_run_time=None,
    )
    sched.add_job(
        jobs.track_job,
        CronTrigger(hour=config.TRACK_HOUR_KST, minute=config.TRACK_MINUTE_KST),
        id="daily_track",
    )
    # 윈도우 시작 시각마다 pending 큐 드레인 + 브리핑 발송
    for h, m in config.DRAIN_TIMES:
        sched.add_job(
            jobs.drain_queue_job,
            CronTrigger(hour=h, minute=m),
            id=f"drain_queue_{h:02d}{m:02d}",
        )
        sched.add_job(
            jobs.briefing_job,
            CronTrigger(hour=h, minute=m),
            id=f"briefing_{h:02d}{m:02d}",
        )
    log.info("scheduler started — TZ=%s", config.TZ)
    log.info("registered jobs: %s", [j.id for j in sched.get_jobs()])
    log.info("intervals: price=%dm news=%dm track=%02d:%02d KST",
             config.PRICE_INTERVAL_MIN, config.NEWS_INTERVAL_MIN,
             config.TRACK_HOUR_KST, config.TRACK_MINUTE_KST)
    log.info("drain + briefing times (KST): %s",
             ", ".join(f"{h:02d}:{m:02d}" for h, m in config.DRAIN_TIMES))

    # 부팅 직후 가격 모니터링 1회 실행 (콜드 스타트 방지)
    sched.add_job(jobs.price_job, id="boot_price", next_run_time=_now_plus(2))
    sched.add_job(jobs.news_job, id="boot_news", next_run_time=_now_plus(20))

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


def _now_plus(seconds: int):
    from datetime import datetime, timedelta
    return datetime.now() + timedelta(seconds=seconds)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    log = logging.getLogger("main")
    args = parse_args(argv)

    if args.dry_run:
        missing = config.validate(strict=False)
        log.info("DRY_RUN — DB_PATH=%s", config.DB_PATH)
        log.info("DRY_RUN — missing env keys: %s", missing or "(none)")
        try:
            from . import (  # noqa: F401  (import 가능성만 검증)
                classifier, filter, news, notifier, tracker, upbit,
            )
        except Exception as e:
            log.error("DRY_RUN — import 실패: %s", e)
            return 2
        log.info("DRY_RUN — 모든 모듈 import OK")
        return 0

    if args.once:
        db.init_db()
        log.info("--once %s 실행", args.once)
        jobs.run_once(args.once)
        return 0

    run_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
