"""환경 변수 로드 (config/settings.py 패턴, market-briefing 참고)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Claude
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Email (Gmail SMTP)
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

# Filtering
MIN_CONSECUTIVE_UP_DAYS = int(os.getenv("MIN_CONSECUTIVE_UP_DAYS", "2"))  # 2~3일 연속 상승
MAX_CONSECUTIVE_UP_DAYS = int(os.getenv("MAX_CONSECUTIVE_UP_DAYS", "3"))
MIN_DAILY_RISE_PCT = float(os.getenv("MIN_DAILY_RISE_PCT", "0.0"))  # 종가 > 시가면 상승

# Schedule (분)
PRICE_INTERVAL_MIN = int(os.getenv("PRICE_INTERVAL_MIN", "60"))   # 가격 모니터링 1시간
NEWS_INTERVAL_MIN = int(os.getenv("NEWS_INTERVAL_MIN", "5"))      # 뉴스 수집 5분
TRACK_HOUR_KST = int(os.getenv("TRACK_HOUR_KST", "0"))            # 매일 자정 추적
TRACK_MINUTE_KST = int(os.getenv("TRACK_MINUTE_KST", "5"))

# News sources (RSS)
COINNESS_RSS = os.getenv("COINNESS_RSS", "https://coinness.live/rss")
TOKENPOST_RSS = os.getenv("TOKENPOST_RSS", "https://www.tokenpost.kr/rss")
NEWS_HTTP_TIMEOUT = int(os.getenv("NEWS_HTTP_TIMEOUT", "10"))

# Outcome tracking thresholds
TRAILING_DROP_THRESHOLD = float(os.getenv("TRAILING_DROP_THRESHOLD", "0.07"))  # -7%
CONSECUTIVE_RED_DAYS = int(os.getenv("CONSECUTIVE_RED_DAYS", "2"))             # 연속 음봉 N일

# 알림 윈도우 (피크 30분 전 ~ 피크 종료, KST). (start_hour, start_min, end_hour, end_min)
# 22:00-01:00 미국 윈도우는 자정을 넘기므로 두 조각(22:00-24:00, 00:00-01:00)으로 표현
ALERT_WINDOWS: list[tuple[int, int, int, int]] = [
    (8, 30, 10, 0),    # 국내 피크 (09:00-10:00) 30분 전
    (15, 30, 18, 0),   # 유럽/아시아 (16:00-18:00) 30분 전
    (22, 0, 24, 0),    # 미국 피크 (22:30-01:00) 30분 전 — 자정까지
    (0, 0, 1, 0),      # 미국 피크 자정 이후 잔여
    (1, 30, 4, 0),     # 심야 휩쏘 (02:00-04:00) 30분 전
]

# drain_queue_job 트리거 시각 (각 윈도우 시작 시점)
DRAIN_TIMES: list[tuple[int, int]] = [
    (8, 30),
    (15, 30),
    (22, 0),
    (1, 30),
]

# pending 큐 TTL — 이보다 오래된 미발송 S/A는 폐기
PENDING_TTL_HOURS = int(os.getenv("PENDING_TTL_HOURS", "24"))

# Misc
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TZ = os.getenv("TZ", "Asia/Seoul")
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "alerts.db")))


def validate(strict: bool = True) -> list[str]:
    """필수 키 누락 검사. strict=False면 경고만 반환."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not GMAIL_USER:
        missing.append("GMAIL_USER")
    if not GMAIL_APP_PASSWORD:
        missing.append("GMAIL_APP_PASSWORD")
    if not EMAIL_RECIPIENT:
        missing.append("EMAIL_RECIPIENT")
    if strict and missing:
        raise RuntimeError(f"필수 환경변수 누락: {', '.join(missing)}")
    return missing
