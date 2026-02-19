"""RunPod Serverless를 통한 PDF OCR 파싱 서비스."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.config import settings

# RunPod serverless는 비동기 실행 후 polling 하는 패턴
_RUNPOD_RUN_URL = settings.RUNPOD_RUN_URL or f"{settings.RUNPOD_API_URL.rstrip('/')}/run"
_RUNPOD_STATUS_URL = settings.RUNPOD_STATUS_URL or f"{settings.RUNPOD_API_URL.rstrip('/')}/status"
_HEADERS = {
    "Authorization": f"Bearer {settings.RUNPOD_API_KEY}",
    "Content-Type": "application/json",
}

# 최대 대기 시간 (초)
_MAX_WAIT_SECONDS = 120
_POLL_INTERVAL = 2


def _dump_ocr_json(dump_file_path: str | None, payload: dict[str, Any]) -> None:
    if not dump_file_path:
        return
    path = Path(dump_file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"OCR 결과 JSON 저장 완료 - {path}")


def _extract_text_from_output(output: Any) -> str:
    """DeepSeek-OCR2 응답 규격(output.mmd_text)에서만 텍스트 추출."""
    if not isinstance(output, dict):
        raise RuntimeError(f"Unexpected RunPod output type: {type(output).__name__}")

    mmd_text = output.get("mmd_text")
    if isinstance(mmd_text, str) and mmd_text.strip():
        return mmd_text

    raise RuntimeError(
        "RunPod output.mmd_text is missing or empty. "
        f"available_keys={list(output.keys())}"
    )


def parse_pdf_via_runpod(
    pdf_bytes_b64: str | None = None,
    *,
    pdf_url: str | None = None,
    filename: str | None = None,
    dump_file_path: str | None = None,
) -> str:
    """RunPod serverless에 PDF를 전송하고 OCR 텍스트를 반환.

    Args:
        pdf_bytes_b64: base64 인코딩 PDF 문자열 (pdf_url와 둘 중 하나)
        pdf_url: 접근 가능한 PDF URL (pdf_bytes_b64와 둘 중 하나)
        filename: 선택 파일명 힌트

    Returns:
        파싱된 텍스트

    Raises:
        RuntimeError: RunPod 요청 실패 또는 타임아웃
    """
    if not pdf_bytes_b64 and not pdf_url:
        raise ValueError("Either pdf_bytes_b64 or pdf_url is required")

    payload_input: dict[str, Any] = {}
    if filename:
        payload_input["filename"] = filename
    if pdf_bytes_b64:
        payload_input["pdf_base64"] = pdf_bytes_b64
    if pdf_url:
        payload_input["pdf_url"] = pdf_url

    logger.info("RunPod OCR 요청 전송 시작")
    with httpx.Client(timeout=30.0) as client:
        run_response = client.post(
            _RUNPOD_RUN_URL,
            headers=_HEADERS,
            json={"input": payload_input},
        )
        run_response.raise_for_status()
        run_data = run_response.json()

    job_id = run_data.get("id")
    if not job_id:
        raise RuntimeError(f"RunPod 작업 제출 실패: {run_data}")

    logger.info(f"RunPod job 제출 완료 - job_id={job_id}")

    elapsed = 0
    with httpx.Client(timeout=20.0) as client:
        while elapsed < _MAX_WAIT_SECONDS:
            status_response = client.get(
                f"{_RUNPOD_STATUS_URL}/{job_id}",
                headers=_HEADERS,
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            status = str(status_data.get("status", "")).upper()

            if status == "COMPLETED":
                output = status_data.get("output", {})
                text = _extract_text_from_output(output)
                _dump_ocr_json(
                    dump_file_path,
                    {
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "run_request": {"input": payload_input},
                        "run_response": run_data,
                        "status_response": status_data,
                        "ocr_text_length": len(text),
                    },
                )
                logger.info(
                    f"RunPod OCR 완료 - job_id={job_id}, text_length={len(text)} chars"
                )
                return text

            if status in ("FAILED", "CANCELLED"):
                error_msg = status_data.get("error") or status_data.get("output") or "unknown error"
                _dump_ocr_json(
                    dump_file_path,
                    {
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "run_request": {"input": payload_input},
                        "run_response": run_data,
                        "status_response": status_data,
                        "error": str(error_msg),
                    },
                )
                raise RuntimeError(
                    f"RunPod 작업 실패 - job_id={job_id}, status={status}, error={error_msg}"
                )

            time.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    raise RuntimeError(
        f"RunPod 타임아웃 - job_id={job_id}, elapsed={elapsed}s (max={_MAX_WAIT_SECONDS}s)"
    )
