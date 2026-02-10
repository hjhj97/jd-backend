"""RunPod Serverless를 통한 PDF 파싱 서비스.

RunPod serverless endpoint에 PDF(base64)를 전송하고,
파싱된 텍스트를 반환받는다.
"""

import time

import httpx
from loguru import logger

from app.config import settings

# RunPod serverless는 비동기 실행 후 polling 하는 패턴
_RUNPOD_RUN_URL = f"{settings.RUNPOD_API_URL}/run"
_RUNPOD_STATUS_URL = f"{settings.RUNPOD_API_URL}/status"
_HEADERS = {
    "Authorization": f"Bearer {settings.RUNPOD_API_KEY}",
    "Content-Type": "application/json",
}

# 최대 대기 시간 (초)
_MAX_WAIT_SECONDS = 120
_POLL_INTERVAL = 2


def parse_pdf_via_runpod(pdf_bytes_b64: str) -> str:
    """RunPod serverless에 PDF를 전송하고 파싱 결과 텍스트를 반환.

    Args:
        pdf_bytes_b64: base64로 인코딩된 PDF 바이트 문자열

    Returns:
        파싱된 텍스트

    Raises:
        RuntimeError: RunPod 요청 실패 또는 타임아웃
    """
    # 1) 작업 제출
    logger.info("RunPod에 PDF 파싱 요청 전송")
    with httpx.Client(timeout=30.0) as client:
        run_response = client.post(
            _RUNPOD_RUN_URL,
            headers=_HEADERS,
            json={"input": {"pdf_base64": pdf_bytes_b64}},
        )
        run_response.raise_for_status()
        run_data = run_response.json()

    job_id = run_data.get("id")
    if not job_id:
        raise RuntimeError(f"RunPod 작업 제출 실패: {run_data}")

    logger.info(f"RunPod job 제출 완료 - job_id={job_id}")

    # 2) Polling으로 결과 대기
    elapsed = 0
    with httpx.Client(timeout=15.0) as client:
        while elapsed < _MAX_WAIT_SECONDS:
            status_response = client.get(
                f"{_RUNPOD_STATUS_URL}/{job_id}",
                headers=_HEADERS,
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            status = status_data.get("status")

            if status == "COMPLETED":
                output = status_data.get("output", {})
                text = output.get("text", "")
                logger.info(
                    f"RunPod 파싱 완료 - job_id={job_id}, "
                    f"text_length={len(text)} chars"
                )
                return text

            if status in ("FAILED", "CANCELLED"):
                error_msg = status_data.get("error", "unknown error")
                raise RuntimeError(
                    f"RunPod 작업 실패 - job_id={job_id}, "
                    f"status={status}, error={error_msg}"
                )

            # IN_QUEUE 또는 IN_PROGRESS → 대기
            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    raise RuntimeError(
        f"RunPod 타임아웃 - job_id={job_id}, "
        f"elapsed={elapsed}s (max={_MAX_WAIT_SECONDS}s)"
    )
