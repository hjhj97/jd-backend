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
    # =========================================================================
    # TODO: RunPod 연동 - 아래 주석 해제하고 mock 부분 삭제
    # =========================================================================
    # # 1) 작업 제출
    # logger.info("RunPod에 PDF 파싱 요청 전송")
    # with httpx.Client(timeout=30.0) as client:
    #     run_response = client.post(
    #         _RUNPOD_RUN_URL,
    #         headers=_HEADERS,
    #         json={"input": {"pdf_base64": pdf_bytes_b64}},
    #     )
    #     run_response.raise_for_status()
    #     run_data = run_response.json()
    #
    # job_id = run_data.get("id")
    # if not job_id:
    #     raise RuntimeError(f"RunPod 작업 제출 실패: {run_data}")
    #
    # logger.info(f"RunPod job 제출 완료 - job_id={job_id}")
    #
    # # 2) Polling으로 결과 대기
    # elapsed = 0
    # with httpx.Client(timeout=15.0) as client:
    #     while elapsed < _MAX_WAIT_SECONDS:
    #         status_response = client.get(
    #             f"{_RUNPOD_STATUS_URL}/{job_id}",
    #             headers=_HEADERS,
    #         )
    #         status_response.raise_for_status()
    #         status_data = status_response.json()
    #         status = status_data.get("status")
    #
    #         if status == "COMPLETED":
    #             output = status_data.get("output", {})
    #             text = output.get("text", "")
    #             logger.info(
    #                 f"RunPod 파싱 완료 - job_id={job_id}, "
    #                 f"text_length={len(text)} chars"
    #             )
    #             return text
    #
    #         if status in ("FAILED", "CANCELLED"):
    #             error_msg = status_data.get("error", "unknown error")
    #             raise RuntimeError(
    #                 f"RunPod 작업 실패 - job_id={job_id}, "
    #                 f"status={status}, error={error_msg}"
    #             )
    #
    #         # IN_QUEUE 또는 IN_PROGRESS → 대기
    #         time.sleep(_POLL_INTERVAL)
    #         elapsed += _POLL_INTERVAL
    #
    # raise RuntimeError(
    #     f"RunPod 타임아웃 - job_id={job_id}, "
    #     f"elapsed={elapsed}s (max={_MAX_WAIT_SECONDS}s)"
    # )

    # =========================================================================
    # MOCK: 실제 RunPod 대신 테스트용 텍스트 반환
    # =========================================================================
    logger.info("PDF 파싱 시작 (MOCK - RunPod 연동 전)")
    time.sleep(10)  # 파싱 시간 시뮬레이션

    mock_text = """
    [특허 공보 파싱 결과 - MOCK DATA]
    
    【서지사항】
    출원번호: 10-2023-0001234
    발명의 명칭: AI 기반 문서 분석 시스템 및 방법
    출원인: (주)테크놀로지
    발명자: 홍길동, 김철수
    
    【기술분야】
    본 발명은 인공지능 기반 문서 분석에 관한 것으로, 
    특히 대용량 PDF 문서를 자동으로 분석하여 핵심 정보를 추출하는 시스템에 관한 것이다.
    
    【청구항】
    [청구항 1] 사용자 단말로부터 PDF 문서를 수신하는 단계;
    상기 PDF 문서를 텍스트로 변환하는 단계;
    변환된 텍스트를 복수의 AI 모델로 분석하는 단계; 및
    분석 결과를 JSON 형태로 반환하는 단계를 포함하는 문서 분석 방법.
    
    [청구항 2] 제1항에 있어서, 상기 AI 모델은 서지사항 추출 모델, 
    청구항 분석 모델, 기술 분야 분석 모델, 도면 분석 모델, 
    종합 평가 모델을 포함하는 것을 특징으로 하는 문서 분석 방법.
    
    【발명의 설명】
    본 발명은 특허 문서의 자동 분석을 통해 심사관 및 변리사의 
    업무 효율을 극대화할 수 있는 시스템을 제공한다.
    """

    logger.info(f"PDF 파싱 완료 (MOCK) - text_length={len(mock_text)} chars")
    return mock_text.strip()
