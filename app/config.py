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
