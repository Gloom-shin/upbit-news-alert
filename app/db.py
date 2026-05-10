"""SQLite 영속화. 3개 테이블: news_events, price_tracking, event_outcomes."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS news_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    market TEXT,
    grade TEXT,                    -- S / A / B / C
    headline TEXT NOT NULL,
    summary TEXT,
    url TEXT NOT NULL UNIQUE,
    source TEXT,
    detected_price REAL,
    classified_reason TEXT,
    created_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_news_events_symbol ON news_events(symbol);
CREATE INDEX IF NOT EXISTS idx_news_events_grade ON news_events(grade);

CREATE TABLE IF NOT EXISTS price_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_event_id INTEGER NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    entry_price REAL NOT NULL,
    peak_price REAL NOT NULL,
    started_at TEXT NOT NULL,
    closed_at TEXT,                 -- NULL이면 진행 중
    close_reason TEXT,              -- 'first_red' / 'trailing_drop' / 'below_entry' / 'consecutive_red'
    days_held INTEGER,
    FOREIGN KEY (news_event_id) REFERENCES news_events(id)
);

CREATE INDEX IF NOT EXISTS idx_price_tracking_symbol ON price_tracking(symbol);

CREATE TABLE IF NOT EXISTS event_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    price_tracking_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,        -- 'first_red' / 'trailing_drop' / 'below_entry' / 'consecutive_red'
    triggered_at TEXT NOT NULL,
    triggered_price REAL,
    days_to_trigger INTEGER,
    FOREIGN KEY (price_tracking_id) REFERENCES price_tracking(id),
    UNIQUE (price_tracking_id, criterion)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_tracking ON event_outcomes(price_tracking_id);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
    logger.info("[db] 초기화 완료 → %s", config.DB_PATH)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def insert_news_event(
    *,
    symbol: str,
    market: str | None,
    grade: str,
    headline: str,
    summary: str,
    url: str,
    source: str,
    detected_price: float | None,
    classified_reason: str,
) -> int | None:
    """news_events INSERT. URL UNIQUE 충돌 시 None."""
    try:
        with conn() as c:
            cur = c.execute(
                """
                INSERT INTO news_events
                (symbol, market, grade, headline, summary, url, source,
                 detected_price, classified_reason, created_at, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (symbol, market, grade, headline, summary, url, source,
                 detected_price, classified_reason, now_iso()),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def mark_notified(news_event_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE news_events SET notified=1 WHERE id=?", (news_event_id,))


def open_price_tracking(*, news_event_id: int, symbol: str, market: str, entry_price: float) -> int:
    with conn() as c:
        cur = c.execute(
            """
            INSERT OR IGNORE INTO price_tracking
            (news_event_id, symbol, market, entry_price, peak_price, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (news_event_id, symbol, market, entry_price, entry_price, now_iso()),
        )
        return cur.lastrowid or 0


def get_active_trackings() -> list[sqlite3.Row]:
    with conn() as c:
        return list(c.execute("SELECT * FROM price_tracking WHERE closed_at IS NULL"))


def update_peak(tracking_id: int, peak_price: float) -> None:
    with conn() as c:
        c.execute(
            "UPDATE price_tracking SET peak_price=? WHERE id=? AND peak_price < ?",
            (peak_price, tracking_id, peak_price),
        )


def record_outcome(
    *,
    tracking_id: int,
    criterion: str,
    triggered_price: float,
    days_to_trigger: int,
) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO event_outcomes
            (price_tracking_id, criterion, triggered_at, triggered_price, days_to_trigger)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tracking_id, criterion, now_iso(), triggered_price, days_to_trigger),
        )


def close_tracking(*, tracking_id: int, close_reason: str, days_held: int) -> None:
    with conn() as c:
        c.execute(
            "UPDATE price_tracking SET closed_at=?, close_reason=?, days_held=? WHERE id=? AND closed_at IS NULL",
            (now_iso(), close_reason, days_held, tracking_id),
        )


def stats_by_grade() -> list[dict]:
    """등급별 평균 보유일수, 종료 사유 분포."""
    with conn() as c:
        rows = c.execute(
            """
            SELECT
                ne.grade,
                COUNT(pt.id)         AS total,
                AVG(pt.days_held)    AS avg_days,
                SUM(CASE WHEN pt.close_reason='first_red'        THEN 1 ELSE 0 END) AS r_first_red,
                SUM(CASE WHEN pt.close_reason='trailing_drop'    THEN 1 ELSE 0 END) AS r_trailing,
                SUM(CASE WHEN pt.close_reason='below_entry'      THEN 1 ELSE 0 END) AS r_below_entry,
                SUM(CASE WHEN pt.close_reason='consecutive_red'  THEN 1 ELSE 0 END) AS r_two_red
            FROM news_events ne
            JOIN price_tracking pt ON ne.id = pt.news_event_id
            WHERE pt.closed_at IS NOT NULL
            GROUP BY ne.grade
            ORDER BY CASE ne.grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END
            """
        ).fetchall()
        return [dict(r) for r in rows]
