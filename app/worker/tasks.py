"""Celery Task 정의 - 특허 PDF 분석 파이프라인.

단일 Task 방식: RunPod PDF 파싱 → 5개 모델 순차 실행 → 보고서 포맷팅
프로토타입에서는 디버깅과 상태 추적이 간편한 단일 Task 방식을 사용.
"""

from celery.exceptions import SoftTimeLimitExceeded
from loguru import logger

from app.models import model_1, model_2, model_3, model_4, model_5
from app.services.pdf_service import parse_pdf_via_runpod
from app.services.report_service import format_report
from app.worker.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_patent",
    max_retries=2,
    default_retry_delay=10,
    time_limit=300,
    soft_time_limit=240,
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
    """실제 파이프라인 실행 로직."""
    models = [
        ("MODEL_1", model_1),
        ("MODEL_2", model_2),
        ("MODEL_3", model_3),
        ("MODEL_4", model_4),
        ("MODEL_5", model_5),
    ]

    # --- Step 1: RunPod PDF 파싱 ---
    task.update_state(state="PARSING", meta={"detail": "PDF 파싱 중"})
    logger.info("Step 1/6 - RunPod PDF 파싱 시작")

    try:
        text = parse_pdf_via_runpod(pdf_bytes_b64)
    except Exception as e:
        logger.error(f"RunPod 호출 실패: {e}")
        raise task.retry(exc=e, countdown=10)

    logger.info(f"PDF 파싱 완료 - {len(text)} chars")

    # --- Step 2~6: 모델 순차 실행 ---
    result = text  # 첫 번째 모델은 텍스트를 입력으로 받음

    for i, (state_name, model) in enumerate(models, start=1):
        task.update_state(
            state=state_name,
            meta={"detail": f"Model {i}/5 실행 중"},
        )
        logger.info(f"Step {i + 1}/6 - {state_name} 실행 시작")

        try:
            result = model.run(result)
        except Exception as e:
            logger.error(f"{state_name} 실패: {e}")
            raise

        logger.info(f"{state_name} 완료")

    # --- Step 7: 보고서 포맷팅 ---
    task.update_state(state="FORMATTING", meta={"detail": "보고서 포맷팅 중"})
    logger.info("Step 7/7 - 보고서 포맷팅")

    report = format_report(result)
    logger.info("파이프라인 완료 - 보고서 생성 성공")
    return report
