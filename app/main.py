import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.api.routes import router
from app.logging_config import setup_logging

# 로깅 초기화
setup_logging()

app = FastAPI(
    title="Patent PDF Analyzer",
    description="특허 공보 PDF를 분석하여 보고서를 생성하는 API",
    version="0.1.0",
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
            "error": "Internal Server Error",
            "detail": "서버 내부 오류가 발생했습니다.",
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
            "error": "Bad Request",
            "detail": str(exc),
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


@app.get("/health")
async def health_check():
    return {"status": "ok"}
