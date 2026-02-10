# =============================================================================
# 멀티스테이지 Dockerfile
# 하나의 이미지에서 api / worker / flower 타겟을 분리하여 빌드
# =============================================================================

# --- Base Stage ---
FROM python:3.11-slim AS base

WORKDIR /app

# 시스템 의존성 (필요시 추가)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 로그 디렉토리 생성
RUN mkdir -p /app/logs

# --- API Server ---
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# --- Celery Worker ---
FROM base AS worker
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]

# --- Flower Monitoring ---
FROM base AS flower
EXPOSE 5555
CMD ["celery", "-A", "app.worker.celery_app", "flower", "--port=5555"]
