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
) -> None:
    url = f"{settings.JDPATENT_API_URL}/api/v1/jobs"
    payload = {
        "task_id": task_id,
        "raw_text": raw_text,
        "user_id": user_id,
        "user_prefer": user_prefer,
        "user_prefer_nation": user_prefer_nation,
        "user_prefer_area": user_prefer_area,
    }
    with httpx.Client(timeout=settings.JDPATENT_SUBMIT_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
    logger.info(f"JDPatent job submitted - task_id={task_id}")


def poll_jdpatent_result(task_id: str) -> dict[str, Any]:
    url = f"{settings.JDPATENT_API_URL}/api/v1/jobs/{task_id}"
    elapsed = 0.0

    while elapsed <= settings.JDPATENT_POLL_TIMEOUT_SECONDS:
        with httpx.Client(timeout=settings.JDPATENT_SUBMIT_TIMEOUT_SECONDS) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

        status = data.get("status")
        if status == "SUCCESS":
            return data.get("result", {})
        if status == "FAILURE":
            raise RuntimeError(data.get("error") or "JDPatent task failed")

        time.sleep(settings.JDPATENT_POLL_INTERVAL_SECONDS)
        elapsed += settings.JDPATENT_POLL_INTERVAL_SECONDS

    raise RuntimeError(
        f"JDPatent polling timeout - task_id={task_id}, elapsed={elapsed}s"
    )

