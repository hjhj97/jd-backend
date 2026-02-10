import os
import sys

from loguru import logger

from app.config import settings


_is_logging_configured = False


def _console_format(record: dict) -> str:
    request_id = record["extra"].get("request_id", "system")
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        f"<cyan>{request_id}</cyan> | "
        "{message}"
    )


def setup_logging() -> None:
    """loguru 기반 로깅 설정.

    - 콘솔: 사람이 읽기 쉬운 컬러 포맷
    - logs/app.log: JSON 직렬화 (전체 로그)
    - logs/error.log: JSON 직렬화 (ERROR 이상만)
    """
    global _is_logging_configured
    if _is_logging_configured:
        return

    os.makedirs("logs", exist_ok=True)

    logger.remove()  # 기본 핸들러 제거

    # 콘솔 출력 (개발용)
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=_console_format,
    )

    # 파일 출력 - 전체 로그 (JSON)
    logger.add(
        "logs/app.log",
        level="INFO",
        serialize=True,
        rotation="50 MB",
        retention="7 days",
        compression="gz",
        enqueue=True,  # 멀티프로세스 안전 (Celery Worker 대비)
    )

    # 파일 출력 - ERROR 전용 (JSON)
    logger.add(
        "logs/error.log",
        level="ERROR",
        serialize=True,
        rotation="10 MB",
        retention="30 days",
        enqueue=True,
    )

    # request_id 기본값 바인딩 (contextualize 전에도 에러 방지)
    logger.configure(extra={"request_id": "system"})
    _is_logging_configured = True
