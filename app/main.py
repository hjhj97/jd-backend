import json
import uuid
from asyncio import Task, create_task, sleep
from collections import Counter
from datetime import datetime
from typing import Any

from pathlib import Path

from celery.result import AsyncResult
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from app.api.routes import get_result as get_v1_result, router
from app.config import settings
from app.logging_config import setup_logging
from app.services.temp_pdf_service import cleanup_expired_temp_pdfs
from app.worker.celery_app import celery_app

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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


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
_LOG_VIEWER_PATH = _STATIC_DIR / "log_viewer.html"
_APP_LOG_PATH = Path("logs/app.log")
_ERROR_LOG_PATH = Path("logs/error.log")


def _safe_sortable_timestamp(ts: str | None) -> str:
    """정렬 가능한 timestamp 문자열을 반환한다.

    datetime aware/naive 혼합 비교 예외를 피하기 위해 문자열 키를 사용한다.
    """
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).isoformat()
    except ValueError:
        return str(ts)


def _parse_json_log_line(raw_line: str, source: str, offset: int) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None

    base = {
        "id": f"{source}:{offset}",
        "source": source,
        "offset": offset,
        "timestamp": None,
        "level": "RAW",
        "message": line,
        "event": None,
        "request_id": None,
        "task_id": None,
        "meta": {},
    }

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return base

    record = payload.get("record")
    if not isinstance(record, dict):
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            base["message"] = text.strip()
        return base

    level_data = record.get("level")
    if isinstance(level_data, dict):
        base["level"] = str(level_data.get("name", "RAW"))

    time_data = record.get("time")
    if isinstance(time_data, dict):
        timestamp = time_data.get("repr")
        if isinstance(timestamp, str):
            base["timestamp"] = timestamp

    message = record.get("message")
    if isinstance(message, str) and message.strip():
        base["message"] = message.strip()

    extra_data = record.get("extra")
    if isinstance(extra_data, dict):
        base["event"] = extra_data.get("event")
        base["request_id"] = extra_data.get("request_id")
        base["task_id"] = extra_data.get("task_id")
        base["meta"] = {
            k: v
            for k, v in extra_data.items()
            if k not in {"event", "request_id", "task_id"}
        }

    return base


def _read_log_updates(path: Path, cursor: int, source: str) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0

    file_size = path.stat().st_size
    if cursor < 0:
        cursor = 0
    elif cursor > file_size:
        # 파일이 truncate/rotate 된 경우: 전체 재스캔 대신 현재 EOF로 점프
        return [], file_size

    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(cursor)
        while True:
            offset = f.tell()
            raw_line = f.readline()
            if not raw_line:
                break
            parsed = _parse_json_log_line(raw_line, source=source, offset=offset)
            if parsed:
                entries.append(parsed)
        next_cursor = f.tell()

    return entries, next_cursor


def _tail_log_entries(path: Path, source: str, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        raw_lines = f.readlines()

    if limit > 0:
        raw_lines = raw_lines[-limit:]

    entries: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(raw_lines):
        parsed = _parse_json_log_line(raw_line, source=source, offset=idx)
        if parsed:
            parsed["id"] = f"{source}:tail:{idx}"
            entries.append(parsed)
    return entries


def _sort_log_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda e: (
            _safe_sortable_timestamp(e.get("timestamp")),
            str(e.get("source", "")),
            int(e.get("offset", 0)),
        ),
    )


def _extract_task_id_from_inspect_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None

    for key in ("id", "uuid", "task_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value

    request_data = item.get("request")
    if isinstance(request_data, dict):
        for key in ("id", "uuid", "task_id"):
            value = request_data.get(key)
            if isinstance(value, str) and value:
                return value

    return None


def _collect_task_ids_from_inspect_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    task_ids: set[str] = set()
    for items in payload.values():
        if not isinstance(items, list):
            continue
        for item in items:
            task_id = _extract_task_id_from_inspect_item(item)
            if task_id:
                task_ids.add(task_id)

    return sorted(task_ids)


def _state_to_stage(state: str) -> str:
    normalized = (state or "").upper()
    if normalized == "PARSING":
        return "runpod_parsing"
    if normalized == "JDPATENT_SUBMIT":
        return "jdpatent_submit"
    if normalized == "JDPATENT_PROCESSING":
        return "jdpatent_processing"
    if normalized in {"STARTED", "RETRY"}:
        return "worker_processing"
    return "other_active"


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


@app.get("/log")
async def log_dashboard():
    if not _LOG_VIEWER_PATH.exists():
        raise HTTPException(status_code=404, detail="log viewer page not found")
    return FileResponse(_LOG_VIEWER_PATH, media_type="text/html")


@app.get("/log/snapshot")
async def log_snapshot(limit: int = Query(default=400, ge=100, le=5000)):
    app_entries = _tail_log_entries(_APP_LOG_PATH, source="app.log", limit=limit)
    error_entries = _tail_log_entries(_ERROR_LOG_PATH, source="error.log", limit=limit)
    merged = _sort_log_entries(app_entries + error_entries)
    if len(merged) > limit:
        merged = merged[-limit:]

    app_pos = _APP_LOG_PATH.stat().st_size if _APP_LOG_PATH.exists() else 0
    error_pos = _ERROR_LOG_PATH.stat().st_size if _ERROR_LOG_PATH.exists() else 0

    return {
        "entries": merged,
        "cursor": {
            "app_pos": app_pos,
            "error_pos": error_pos,
        },
    }


@app.get("/log/updates")
async def log_updates(
    app_pos: int = Query(default=0, ge=0),
    error_pos: int = Query(default=0, ge=0),
    max_entries: int = Query(default=1000, ge=100, le=10000),
):
    app_entries, next_app_pos = _read_log_updates(_APP_LOG_PATH, cursor=app_pos, source="app.log")
    error_entries, next_error_pos = _read_log_updates(
        _ERROR_LOG_PATH,
        cursor=error_pos,
        source="error.log",
    )
    merged = _sort_log_entries(app_entries + error_entries)

    dropped = 0
    if len(merged) > max_entries:
        dropped = len(merged) - max_entries
        merged = merged[-max_entries:]

    return {
        "entries": merged,
        "dropped": dropped,
        "cursor": {
            "app_pos": next_app_pos,
            "error_pos": next_error_pos,
        },
    }


@app.get("/log/queue")
async def log_queue_snapshot():
    inspect = celery_app.control.inspect(timeout=0.5)
    active_payload = inspect.active() if inspect else {}
    reserved_payload = inspect.reserved() if inspect else {}
    scheduled_payload = inspect.scheduled() if inspect else {}

    active_ids = _collect_task_ids_from_inspect_payload(active_payload)
    reserved_ids = _collect_task_ids_from_inspect_payload(reserved_payload)
    scheduled_ids = _collect_task_ids_from_inspect_payload(scheduled_payload)

    stage_counts: Counter[str] = Counter()
    stage_task_ids: dict[str, list[str]] = {
        "runpod_parsing": [],
        "jdpatent_submit": [],
        "jdpatent_processing": [],
        "worker_processing": [],
        "other_active": [],
    }

    for task_id in active_ids:
        state = AsyncResult(task_id, app=celery_app).state
        stage = _state_to_stage(state)
        stage_counts[stage] += 1
        stage_task_ids.setdefault(stage, []).append(task_id)

    broker_ready_count = 0
    try:
        backend_client = getattr(celery_app.backend, "client", None)
        if backend_client is not None:
            broker_ready_count = int(backend_client.llen("celery") or 0)
    except Exception as exc:
        logger.warning(f"큐 길이 조회 실패: {exc}")

    queued_estimate = broker_ready_count + len(reserved_ids) + len(scheduled_ids)
    queued_known_ids = sorted(set(reserved_ids + scheduled_ids))

    return {
        "snapshot_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "workers": {
            "active_count": len(active_ids),
            "reserved_count": len(reserved_ids),
            "scheduled_count": len(scheduled_ids),
        },
        "queue": {
            "broker_ready_count": broker_ready_count,
            "reserved_count": len(reserved_ids),
            "scheduled_count": len(scheduled_ids),
            "queued_estimate": queued_estimate,
            "queued_known_ids_count": len(queued_known_ids),
            "queued_unknown_ids_count": broker_ready_count,
        },
        "stages": {
            "queued": queued_estimate,
            "runpod_parsing": stage_counts["runpod_parsing"],
            "jdpatent_submit": stage_counts["jdpatent_submit"],
            "jdpatent_processing": stage_counts["jdpatent_processing"],
            "worker_processing": stage_counts["worker_processing"],
            "other_active": stage_counts["other_active"],
        },
        "task_ids": {
            "queued_known": queued_known_ids,
            "reserved": reserved_ids,
            "scheduled": scheduled_ids,
            "runpod_parsing": stage_task_ids["runpod_parsing"],
            "jdpatent_submit": stage_task_ids["jdpatent_submit"],
            "jdpatent_processing": stage_task_ids["jdpatent_processing"],
            "worker_processing": stage_task_ids["worker_processing"],
            "other_active": stage_task_ids["other_active"],
        },
    }


@app.get("/sample")
async def sample_report():
    return FileResponse(_STATIC_DIR / "sample.html", media_type="text/html")
