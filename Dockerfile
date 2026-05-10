FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# 의존성 캐시 레이어
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스
COPY app/ ./app/

# DB / 로그용 디렉토리 (compose에서 volume 마운트됨)
RUN mkdir -p /app/data

CMD ["python", "-m", "app.main"]
