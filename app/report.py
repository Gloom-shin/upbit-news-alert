"""통계 리포트 CLI.

사용: python -m app.report
"""
from __future__ import annotations

import sys

from . import db


def main() -> int:
    db.init_db()
    rows = db.stats_by_grade()
    if not rows:
        print("(아직 종료된 트래킹이 없습니다 — 매일 자정 track_job 실행 후 데이터 누적됩니다)")
        return 0

    headers = ["등급", "건수", "평균보유일", "첫음봉", "트레일링", "본전이탈", "연속2음봉"]
    widths = [4, 6, 10, 8, 10, 10, 10]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))

    for r in rows:
        cells = [
            (r.get("grade") or "-").ljust(widths[0]),
            str(r.get("total") or 0).rjust(widths[1]),
            f"{r.get('avg_days') or 0:.2f}".rjust(widths[2]),
            str(r.get("r_first_red") or 0).rjust(widths[3]),
            str(r.get("r_trailing") or 0).rjust(widths[4]),
            str(r.get("r_below_entry") or 0).rjust(widths[5]),
            str(r.get("r_two_red") or 0).rjust(widths[6]),
        ]
        print("  ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
