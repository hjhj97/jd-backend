"""Celery Task 정의 - PDF 파싱 후 JDPatent 내부 서비스 연동."""

from celery.exceptions import SoftTimeLimitExceeded
from loguru import logger

from app.config import settings
from app.services.jdpatent_service import poll_jdpatent_result, submit_jdpatent_job
from app.services.patent_type_service import detect_patent_type
from app.services.pdf_service import parse_pdf_via_runpod
from app.services.s3_service import delete_pdf
from app.worker.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_patent",
    time_limit=1800,
    soft_time_limit=1500,
)
def process_patent(
    self,
    pdf_bytes_b64: str | None,
    request_id: str = "no-id",
    original_filename: str | None = None,
    pdf_url: str | None = None,
    country: str | None = None,
    s3_key: str | None = None,
):
    """특허 PDF 분석 전체 파이프라인.

    Args:
        pdf_bytes_b64: 미사용 (S3 전환 시 제거 예정)
        request_id: API에서 전달받은 요청 추적 ID
        original_filename: 사용자가 업로드한 원본 파일명
        pdf_url: S3 presigned URL
        country: 특허 국가 코드 ('KR' 또는 'US')
        s3_key: OCR 완료 후 삭제할 S3 오브젝트 키

    Returns:
        최종 보고서 JSON dict
    """
    with logger.contextualize(request_id=request_id, task_id=self.request.id):
        try:
            return _run_pipeline(
                self,
                pdf_bytes_b64,
                original_filename=original_filename,
                pdf_url=pdf_url,
                country=country,
                s3_key=s3_key,
            )
        except SoftTimeLimitExceeded:
            logger.error("소프트 타임아웃 초과 (240s)")
            raise
        except Exception as e:
            logger.exception(f"파이프라인 예외 발생: {e}")
            raise


def _run_pipeline(
    task,
    pdf_bytes_b64: str | None,
    original_filename: str | None = None,
    pdf_url: str | None = None,
    country: str | None = None,
    s3_key: str | None = None,
) -> dict:
    """RunPod 텍스트 추출 후 JDPatent 비동기 작업을 위임."""

    # --- Step 1: RunPod PDF 파싱 ---
    task.update_state(state="PARSING", meta={"msg": "PDF 파싱 중"})
    logger.info("Step 1/3 - RunPod PDF 파싱 시작")

    dump_file_path = f"{settings.RUNPOD_OCR_DUMP_DIR.rstrip('/')}/{task.request.id}.json"
    try:
        text = parse_pdf_via_runpod(
            pdf_bytes_b64,
            pdf_url=pdf_url,
            filename=original_filename,
            dump_file_path=dump_file_path,
            patent_origin=country,
        )
    finally:
        # OCR 성공/실패 무관하게 S3 파일 즉시 삭제
        if s3_key:
            delete_pdf(s3_key)
    logger.info(f"[OCR_JSON_DUMP_FILE] path={dump_file_path}")

    text_length = len(text)
    logger.info(f"[OCR_RAW_TEXT_LENGTH] chars={text_length}")
    logger.info(f"PDF 파싱 완료 - {text_length} chars")

    # --- Step 1.5: 특허 타입 감지 ---
    patent_type_info = detect_patent_type(text)
    logger.info(
        f"[PATENT_TYPE] type={patent_type_info['patent_type']}, "
        f"kind_code={patent_type_info['patent_kind_code']}"
    )

    # --- Step 2: JDPatent 작업 등록 ---
    task.update_state(state="JDPATENT_SUBMIT", meta={"msg": "JDPatent 작업 등록 중"})
    logger.info("Step 2/3 - JDPatent 작업 등록")
    submit_jdpatent_job(
        task_id=task.request.id,
        raw_text=text,
        user_id=original_filename or task.request.id,
        patent_type=patent_type_info["patent_type"],
        patent_kind_code=patent_type_info["patent_kind_code"],
    )

    # --- Step 3: JDPatent 결과 대기 ---
    task.update_state(state="JDPATENT_PROCESSING", meta={"msg": "JDPatent 결과 대기 중"})
    logger.info("Step 3/3 - JDPatent 결과 polling")
    result = poll_jdpatent_result(task.request.id)
    logger.info("JDPatent 결과 수신 완료")

    # --- Step 4: 특허 타입 정보 주입 ---
    if isinstance(result, dict):
        basic_info = result.get("basic_info")
        if isinstance(basic_info, dict):
            basic_info["patent_type"] = patent_type_info["patent_type"]
            basic_info["patent_kind_code"] = patent_type_info["patent_kind_code"]
        else:
            result["patent_type"] = patent_type_info["patent_type"]
            result["patent_kind_code"] = patent_type_info["patent_kind_code"]

    return result
