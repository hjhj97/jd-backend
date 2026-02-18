"""Celery Task 정의 - PDF 파싱 후 JDPatent 내부 서비스 연동."""

from celery.exceptions import SoftTimeLimitExceeded
from loguru import logger

from app.services.jdpatent_service import poll_jdpatent_result, submit_jdpatent_job
from app.services.pdf_service import parse_pdf_via_runpod
from app.worker.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_patent",
    max_retries=2,
    default_retry_delay=10,
    time_limit=1800,
    soft_time_limit=1500,
)
def process_patent(self, pdf_bytes_b64: str, request_id: str = "no-id"):
    """특허 PDF 분석 전체 파이프라인.

    Args:
        pdf_bytes_b64: base64 인코딩된 PDF 바이트
        request_id: API에서 전달받은 요청 추적 ID

    Returns:
        최종 보고서 JSON dict
    """
    with logger.contextualize(request_id=request_id, task_id=self.request.id):
        try:
            return _run_pipeline(self, pdf_bytes_b64)
        except SoftTimeLimitExceeded:
            logger.error("소프트 타임아웃 초과 (240s)")
            raise
        except Exception as e:
            logger.exception(f"파이프라인 예외 발생: {e}")
            raise


def _run_pipeline(task, pdf_bytes_b64: str) -> dict:
    """RunPod 텍스트 추출 후 JDPatent 비동기 작업을 위임."""

    # --- Step 1: RunPod PDF 파싱 ---
    task.update_state(state="PARSING", meta={"detail": "PDF 파싱 중"})
    logger.info("Step 1/3 - RunPod PDF 파싱 시작")

    try:
        text = parse_pdf_via_runpod(pdf_bytes_b64)
    except Exception as e:
        logger.error(f"RunPod 호출 실패: {e}")
        raise task.retry(exc=e, countdown=10)

    logger.info(f"PDF 파싱 완료 - {len(text)} chars")

    # --- Step 2: JDPatent 작업 등록 ---
    task.update_state(state="JDPATENT_SUBMIT", meta={"detail": "JDPatent 작업 등록 중"})
    logger.info("Step 2/3 - JDPatent 작업 등록")
    submit_jdpatent_job(
        task_id=task.request.id,
        raw_text=text,
        user_id=task.request.id,
    )

    # --- Step 3: JDPatent 결과 대기 ---
    task.update_state(state="JDPATENT_PROCESSING", meta={"detail": "JDPatent 결과 대기 중"})
    logger.info("Step 3/3 - JDPatent 결과 polling")
    result = poll_jdpatent_result(task.request.id)
    logger.info("JDPatent 결과 수신 완료")
    return result
