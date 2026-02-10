from celery import Celery

from app.config import settings

from app.logging_config import setup_logging

# Worker 로깅 초기화
setup_logging()

celery_app = Celery(
    "jd_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],  # 명시적으로 tasks 모듈 포함
)

celery_app.conf.update(
    # 직렬화
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Worker 동시성
    worker_concurrency=4,
    worker_prefetch_multiplier=1,  # 순서 보장에 유리
    # 안정성
    task_acks_late=True,  # 처리 완료 후 ACK
    task_reject_on_worker_lost=True,
    # 타임아웃
    task_time_limit=300,  # 5분 (200페이지 PDF 대비)
    task_soft_time_limit=240,  # 4분 소프트 타임아웃
    # 결과 저장
    result_expires=3600,  # 1시간 보관
)
