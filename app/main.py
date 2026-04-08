import json
import uuid
from asyncio import Task, create_task, sleep

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from app.api.routes import get_result as get_v1_result, router
from app.config import settings
from app.logging_config import setup_logging
from app.services.temp_pdf_service import cleanup_expired_temp_pdfs

# 로깅 초기화
setup_logging()

app = FastAPI(
    title="Patent PDF Analyzer",
    description="특허 공보 PDF를 분석하여 보고서를 생성하는 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 글로벌 예외 핸들러
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """처리되지 않은 모든 예외를 잡아 500 응답을 반환."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.opt(exception=exc).error(
        f"Unhandled exception - request_id={request_id}"
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal Server Error",
            "msg": "서버 내부 오류가 발생했습니다.",
            "request_id": request_id,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException 응답 포맷 통일."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning(
        f"HTTPException - request_id={request_id}, status={exc.status_code}, detail={exc.detail}"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "msg": str(exc.detail),
            "request_id": request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """요청 바디/파라미터 유효성 검증 실패 응답 포맷 통일."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning(f"ValidationError - request_id={request_id}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "msg": "요청 데이터 검증에 실패했습니다.",
            "errors": exc.errors(),
            "request_id": request_id,
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """잘못된 입력값 예외 처리."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning(f"ValueError - request_id={request_id}: {exc}")
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error": "Bad Request",
            "msg": str(exc),
            "request_id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# Middleware: request_id 추적
# ---------------------------------------------------------------------------
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """모든 요청에 고유 request_id를 부여하여 API → Celery Task까지 추적."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])

    # request 객체에 request_id 저장 (route에서 사용)
    request.state.request_id = request_id

    with logger.contextualize(request_id=request_id):
        logger.info(f">> {request.method} {request.url.path}")
        try:
            response = await call_next(request)
            logger.info(f"<< {response.status_code}")
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as e:
            logger.exception(f"Unhandled error: {e}")
            raise


# ---------------------------------------------------------------------------
# Router 등록
# ---------------------------------------------------------------------------
app.include_router(router, prefix="/api/v1")


async def _temp_pdf_cleanup_loop() -> None:
    interval_seconds = max(int(settings.TEMP_PDF_CLEANUP_INTERVAL_SECONDS), 5)
    while True:
        try:
            removed = cleanup_expired_temp_pdfs()
            if removed:
                logger.info(f"임시 PDF 정리 완료 - removed={removed}")
        except Exception as exc:
            logger.warning(f"임시 PDF 정리 루프 오류: {exc}")
        await sleep(interval_seconds)


@app.on_event("startup")
async def startup_temp_pdf_cleanup_task() -> None:
    app.state.temp_pdf_cleanup_task = create_task(_temp_pdf_cleanup_loop())


@app.on_event("shutdown")
async def shutdown_temp_pdf_cleanup_task() -> None:
    cleanup_task: Task | None = getattr(app.state, "temp_pdf_cleanup_task", None)
    if cleanup_task is not None:
        cleanup_task.cancel()


@app.get("/health")
async def health_check():
    return {"success": True, "status": "ok"}


_STATIC_DIR = Path(__file__).parent / "static"
_V3_RESULT_PATH = _STATIC_DIR / "output_v3.json"


@app.get(
    "/api/v3/result/{task_id}",
    summary="v3 결과 조회",
    description=(
        "`GET /api/v1/result/{task_id}`와 동일한 상태 조회 로직으로 동작하며, "
        "상태가 `completed`일 때만 실제 결과 대신 `app/static/output_v3.json`을 반환합니다."
    ),
    responses={
        200: {
            "description": "상태 조회 결과 또는 completed 시 output_v3.json",
            "content": {
                "application/json": {
                    "examples": {
                        "queued": {
                            "summary": "대기 중",
                            "value": {
                                "success": True,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "queued",
                            },
                        },
                        "completed_mock": {
                            "summary": "완료 시 mock 결과 반환",
                            "value": {
                                "success": True,
                                "task_id": "sample-task-id",
                                "status": "completed",
                                "result": {},
                            },
                        },
                    }
                }
            },
        },
        404: {
            "description": "존재하지 않는 task_id 또는 output_v3.json 파일 없음",
        },
        500: {
            "description": "output_v3.json 파싱 실패",
        },
    },
)
async def get_v3_result(task_id: str):
    v1_response = await get_v1_result(task_id)

    if isinstance(v1_response, dict) and v1_response.get("status") == "completed":
        if not _V3_RESULT_PATH.exists():
            raise HTTPException(status_code=404, detail="output_v3.json 파일을 찾을 수 없습니다.")

        try:
            mock_payload = json.loads(_V3_RESULT_PATH.read_text(encoding="utf-8"))
            return mock_payload
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="output_v3.json JSON 파싱에 실패했습니다.") from exc

    return v1_response


@app.get("/sample")
async def sample_report():
    return FileResponse(_STATIC_DIR / "sample.html", media_type="text/html")
