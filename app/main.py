import json
import uuid
from hmac import compare_digest
from asyncio import Task, create_task, sleep
from collections import Counter
from datetime import datetime, timezone
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
from app.services.report_result_store import (
    build_report_summary,
    get_archived_report,
    list_archived_reports,
)
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
_UPLOAD_PATH = _STATIC_DIR / "upload.html"
_REPORT_PATH = _STATIC_DIR / "report.html"
_ADMIN_PATH = _STATIC_DIR / "admin.html"
_APP_LOG_PATH = Path("logs/app.log")
_ERROR_LOG_PATH = Path("logs/error.log")
_LOG_STAGE_LOOKBACK_LIMIT = 3000
_LOG_STAGE_ACTIVE_WINDOW_SECONDS = 3600
_LOG_STAGE_TERMINAL_EVENTS = {
    "analysis_pipeline_succeeded",
    "analysis_pipeline_failed",
}


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


def _parse_log_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    normalized = str(ts).strip().replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_recent_log_entry(entry: dict[str, Any], now: datetime) -> bool:
    dt = _parse_log_timestamp(entry.get("timestamp"))
    if dt is None:
        return True
    return (now - dt).total_seconds() <= _LOG_STAGE_ACTIVE_WINDOW_SECONDS


def _stage_from_log_event(entry: dict[str, Any]) -> str | None:
    event = str(entry.get("event") or "").strip().lower()
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}

    if event == "analysis_stage_changed":
        return _state_to_stage(str(meta.get("stage") or ""))
    if event in {"analysis_task_enqueued", "pdf_upload_received"}:
        return "queued"
    if event in {"runpod_job_enqueued", "runpod_ocr_succeeded"}:
        return "runpod_parsing"
    if event == "report_generation_enqueued":
        return "jdpatent_processing"
    return None


def _infer_recent_task_stages_from_logs() -> dict[str, str]:
    entries = _sort_log_entries(
        _tail_log_entries(_APP_LOG_PATH, source="app.log", limit=_LOG_STAGE_LOOKBACK_LIMIT)
        + _tail_log_entries(_ERROR_LOG_PATH, source="error.log", limit=_LOG_STAGE_LOOKBACK_LIMIT)
    )
    now = datetime.now(timezone.utc)
    task_stages: dict[str, str] = {}

    for entry in entries:
        if not _is_recent_log_entry(entry, now):
            continue

        task_id = entry.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue

        event = str(entry.get("event") or "").strip().lower()
        if event in _LOG_STAGE_TERMINAL_EVENTS:
            task_stages.pop(task_id, None)
            continue

        stage = _stage_from_log_event(entry)
        if stage:
            task_stages[task_id] = stage

    return task_stages


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _collect_report_task_metadata_from_logs(limit: int = 10000) -> dict[str, dict[str, Any]]:
    entries = _sort_log_entries(
        _tail_log_entries(_APP_LOG_PATH, source="app.log", limit=limit)
        + _tail_log_entries(_ERROR_LOG_PATH, source="error.log", limit=limit)
    )
    tasks: dict[str, dict[str, Any]] = {}

    for entry in entries:
        task_id = entry.get("task_id")
        if not isinstance(task_id, str) or not _is_uuid(task_id):
            continue

        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        event = str(entry.get("event") or "")
        item = tasks.setdefault(
            task_id,
            {
                "task_id": task_id,
                "status": "queued",
                "has_result": False,
                "archived": False,
            },
        )

        ts = entry.get("timestamp")
        if ts and not item.get("requested_at"):
            item["requested_at"] = ts

        if event == "pdf_upload_received":
            item["original_filename"] = meta.get("filename")
            item["country"] = meta.get("country")
            item["file_size_bytes"] = meta.get("file_size_bytes")
            item["requested_at"] = ts or item.get("requested_at")
        elif event == "report_generation_enqueued":
            item["pdf_name"] = meta.get("user_id") or item.get("pdf_name")
        elif event == "analysis_pipeline_succeeded":
            item["status"] = "completed"
            item["completed_at"] = ts
        elif event == "analysis_pipeline_failed":
            item["status"] = "failed"
            item["failed_at"] = ts
            item["failure_reason"] = meta.get("failure_reason")

    return tasks


def _admin_item_from_celery(task_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    item = dict(meta)
    task = AsyncResult(task_id, app=celery_app)
    state = str(task.state or "PENDING")

    if state == "SUCCESS" and isinstance(task.result, dict):
        summary = build_report_summary(
            task_id,
            task.result,
            original_filename=item.get("original_filename"),
            country=item.get("country"),
            saved_at=item.get("completed_at"),
        )
        item.update(summary)
        item.update(
            {
                "status": "completed",
                "has_result": True,
                "archived": False,
                "result_url": f"/result/{task_id}",
            }
        )
    elif state == "FAILURE":
        item["status"] = "failed"
        item["error"] = str(task.info)
    elif item.get("status") != "failed":
        item["status"] = _state_to_stage(state) if state != "PENDING" else "queued"

    return item


def _admin_sort_key(item: dict[str, Any]) -> str:
    return str(
        item.get("saved_at")
        or item.get("completed_at")
        or item.get("failed_at")
        or item.get("requested_at")
        or ""
    )


def _require_admin_token(request: Request) -> None:
    expected = (settings.ADMIN_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN is not configured.")

    supplied = (
        request.headers.get("X-Admin-Token")
        or request.query_params.get("token")
        or ""
    ).strip()
    if not supplied or not compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token.")


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


@app.get("/admin")
async def admin_dashboard():
    if not _ADMIN_PATH.exists():
        raise HTTPException(status_code=404, detail="admin page not found")
    return FileResponse(_ADMIN_PATH, media_type="text/html")


@app.get("/admin/reports")
async def admin_reports(
    request: Request,
    limit: int = Query(default=500, ge=1, le=2000),
):
    _require_admin_token(request)

    archived = list_archived_reports(limit=limit)
    by_task_id = {item["task_id"]: item for item in archived if item.get("task_id")}

    log_items = _collect_report_task_metadata_from_logs()
    for task_id, meta in log_items.items():
        if task_id in by_task_id:
            by_task_id[task_id] = {
                **meta,
                **by_task_id[task_id],
                "archived": True,
                "has_result": True,
                "status": "completed",
            }
            continue
        by_task_id[task_id] = _admin_item_from_celery(task_id, meta)

    reports = sorted(by_task_id.values(), key=_admin_sort_key, reverse=True)[:limit]
    return {
        "success": True,
        "count": len(reports),
        "reports": reports,
    }


@app.get("/admin/reports/{task_id}")
async def admin_report_detail(request: Request, task_id: str):
    _require_admin_token(request)
    if not _is_uuid(task_id):
        raise HTTPException(status_code=400, detail=f"유효하지 않은 task_id 형식입니다: {task_id}")

    archived = get_archived_report(task_id)
    if archived and isinstance(archived.get("result"), dict):
        return {
            "success": True,
            "task_id": task_id,
            "status": "completed",
            "archived": True,
            "summary": archived.get("summary"),
            "result": archived["result"],
        }

    task = AsyncResult(task_id, app=celery_app)
    if task.state == "SUCCESS" and isinstance(task.result, dict):
        return {
            "success": True,
            "task_id": task_id,
            "status": "completed",
            "archived": False,
            "summary": build_report_summary(task_id, task.result),
            "result": task.result,
        }

    if task.state == "FAILURE":
        return {
            "success": False,
            "task_id": task_id,
            "status": "failed",
            "msg": str(task.info),
        }

    return {
        "success": True,
        "task_id": task_id,
        "status": "queued" if task.state == "PENDING" else task.state,
        "msg": "완료된 리포트 결과가 아직 없습니다.",
    }


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
    log_task_stages = _infer_recent_task_stages_from_logs()

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
        log_stage = log_task_stages.get(task_id)
        if stage in {"other_active", "worker_processing"} and log_stage and log_stage != "queued":
            stage = log_stage
        stage_counts[stage] += 1
        stage_task_ids.setdefault(stage, []).append(task_id)

    known_stage_ids = {
        task_id
        for ids in stage_task_ids.values()
        for task_id in ids
    }
    queued_or_pending_ids = set(reserved_ids + scheduled_ids)
    for task_id, stage in log_task_stages.items():
        if stage == "queued":
            continue
        if task_id in known_stage_ids or task_id in queued_or_pending_ids:
            continue
        stage_counts[stage] += 1
        stage_task_ids.setdefault(stage, []).append(task_id)
        known_stage_ids.add(task_id)

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
            "log_inferred_active_count": sum(
                1 for stage in log_task_stages.values() if stage != "queued"
            ),
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
    """PDF 업로드 화면. 업로드 → 분석 완료 시 /result/{task_id}로 이동한다."""
    return FileResponse(_UPLOAD_PATH, media_type="text/html")


@app.get("/result/{task_id}")
async def result_page(task_id: str):
    """분석 결과 리포트 페이지.

    페이지가 로드되면 GET /api/v1/result/{task_id}로 결과를 받아 동적 렌더링한다.
    (task_id 검증/조회는 API 엔드포인트에서 수행)
    """
    return FileResponse(_REPORT_PATH, media_type="text/html")
