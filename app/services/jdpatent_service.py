"""JDPatent internal HTTP client service."""

import time
from typing import Any

import httpx
from loguru import logger

from app.config import settings


def submit_jdpatent_job(
    *,
    task_id: str,
    raw_text: str,
    user_id: str | None = None,
    user_prefer: str = "nation",
    user_prefer_nation: str | None = "South Korea",
    user_prefer_area: str | None = None,
    patent_type: str | None = None,
    patent_kind_code: str | None = None,
) -> None:
    url = f"{settings.JDPATENT_API_URL}/api/v1/jobs"
    payload = {
        "task_id": task_id,
        "raw_text": raw_text,
        "user_id": user_id,
        "user_prefer": user_prefer,
        "user_prefer_nation": user_prefer_nation,
        "user_prefer_area": user_prefer_area,
        "patent_type": patent_type,
        "patent_kind_code": patent_kind_code,
    }
    try:
        with httpx.Client(timeout=settings.JDPATENT_SUBMIT_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
    except Exception as exc:
        logger.bind(
            event="report_generation_enqueue_failed",
            task_id=task_id,
            error=str(exc),
        ).error("리포트 생성 작업 큐 등록 실패")
        raise

    logger.bind(
        event="report_generation_enqueued",
        task_id=task_id,
        user_id=user_id,
        raw_text_length=len(raw_text),
        patent_type=patent_type,
        patent_kind_code=patent_kind_code,
    ).info("리포트 생성 작업 큐 등록 성공")


def poll_jdpatent_result(task_id: str) -> dict[str, Any]:
    url = f"{settings.JDPATENT_API_URL}/api/v1/jobs/{task_id}"
    elapsed = 0.0

    while elapsed <= settings.JDPATENT_POLL_TIMEOUT_SECONDS:
        try:
            with httpx.Client(timeout=settings.JDPATENT_SUBMIT_TIMEOUT_SECONDS) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.bind(
                event="report_generation_failed",
                task_id=task_id,
                failure_reason="jdpatent_poll_http_error",
                error=str(exc),
            ).error("리포트 생성 실패")
            raise

        status = data.get("status")
        if status == "SUCCESS":
            result = data.get("result", {})
            if isinstance(result, dict):
                if str(result.get("status", "")).lower() == "error":
                    reason = result.get("reason") or "JDPatent processing failed"
                    logger.bind(
                        event="report_generation_failed",
                        task_id=task_id,
                        failure_reason="jdpatent_logical_error",
                        error=str(reason),
                    ).error("리포트 생성 실패")
                    raise RuntimeError(str(reason))
                if "error" in result:
                    raw_error = result.get("error")
                    logger.bind(
                        event="report_generation_failed",
                        task_id=task_id,
                        failure_reason="jdpatent_result_error",
                        error=str(raw_error),
                    ).error("리포트 생성 실패")
                    raise RuntimeError(str(raw_error))
            logger.bind(
                event="report_generation_succeeded",
                task_id=task_id,
            ).info("리포트 생성 성공")
            return result
        if status == "FAILURE":
            raw_error = data.get("error") or "JDPatent task failed"
            logger.bind(
                event="report_generation_failed",
                task_id=task_id,
                failure_reason="jdpatent_task_failed",
                error=str(raw_error),
            ).error("리포트 생성 실패")
            raise RuntimeError(str(raw_error))

        time.sleep(settings.JDPATENT_POLL_INTERVAL_SECONDS)
        elapsed += settings.JDPATENT_POLL_INTERVAL_SECONDS

    logger.bind(
        event="report_generation_failed",
        task_id=task_id,
        failure_reason="jdpatent_timeout",
        elapsed_seconds=elapsed,
    ).error("리포트 생성 실패")
    raise RuntimeError(
        f"JDPatent polling timeout - task_id={task_id}, elapsed={elapsed}s"
    )
