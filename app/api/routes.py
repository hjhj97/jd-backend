import base64
import json
import uuid
from functools import lru_cache
from pathlib import Path

from celery.result import AsyncResult
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from loguru import logger

from app.worker.celery_app import celery_app
from app.worker.tasks import process_patent

router = APIRouter()

# 최대 업로드 크기: 100MB (200페이지 PDF 대비)
_MAX_PDF_SIZE = 100 * 1024 * 1024
_MOCK_OUTPUT_PATH = Path(__file__).resolve().parents[2] / "mock_output.json"


@lru_cache(maxsize=1)
def _load_mock_output() -> dict:
    with _MOCK_OUTPUT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


_RESULT_COMPLETED_EXAMPLE = {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
    "status": "completed",
    "result": _load_mock_output(),
}


@router.post("/analyze", status_code=202)
async def analyze_patent(request: Request, file: UploadFile = File(...)):
    """특허 PDF를 업로드하여 분석을 시작한다.

    - 즉시 task_id를 반환(202 Accepted)
    - GET /result/{task_id} 로 결과를 폴링
    """
    request_id: str = getattr(request.state, "request_id", "unknown")

    # --- 파일 검증 ---
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일명이 비어있습니다.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"PDF 파일만 허용됩니다. (받은 파일: {file.filename})",
        )

    # PDF 바이트 읽기 → base64 인코딩 (Celery JSON 직렬화용)
    pdf_bytes = await file.read()

    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    if len(pdf_bytes) > _MAX_PDF_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"파일 크기가 너무 큽니다. (최대 {_MAX_PDF_SIZE // (1024*1024)}MB)",
        )

    pdf_b64 = base64.b64encode(pdf_bytes).decode()

    logger.info(
        f"PDF 업로드 완료 - filename={file.filename}, "
        f"size={len(pdf_bytes)} bytes"
    )

    # Celery Task 큐잉
    task = process_patent.delay(pdf_b64, request_id)

    logger.info(f"Task 큐잉 완료 - task_id={task.id}")

    return {
        "task_id": task.id,
        "status": "queued",
        "message": "분석 요청이 접수되었습니다. GET /api/v1/result/{task_id}로 결과를 확인하세요.",
    }


@router.get(
    "/result/{task_id}",
    responses={
        200: {
            "description": "분석 상태 또는 결과 반환",
            "content": {
                "application/json": {
                    "examples": {
                        "queued": {
                            "summary": "대기 중",
                            "value": {
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "queued",
                            },
                        },
                        "processing": {
                            "summary": "처리 중",
                            "value": {
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "MODEL_2",
                                "detail": "Model 2/5 실행 중",
                            },
                        },
                        "completed_mock": {
                            "summary": "완료 (mock_output.json 반환)",
                            "value": _RESULT_COMPLETED_EXAMPLE,
                        },
                        "failed": {
                            "summary": "실패",
                            "value": {
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "failed",
                                "error": "에러 메시지",
                            },
                        },
                    }
                }
            },
        },
        400: {
            "description": "유효하지 않은 task_id 형식",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "유효하지 않은 task_id 형식입니다: invalid-id"
                    }
                }
            },
        },
        404: {
            "description": "존재하지 않는 task_id",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "존재하지 않는 task_id입니다: a1b2c3d4-e5f6-7890-abcd-ef0123456789"
                    }
                }
            },
        },
    },
)
async def get_result(task_id: str):
    """task_id로 분석 결과를 조회한다.

    상태:
    - queued: 대기 중
    - PARSING / MODEL_1~5 / FORMATTING: 처리 중
    - completed: 완료 (result 포함)
    - failed: 실패 (error 포함)
    """
    # task_id UUID 형식 검증
    try:
        uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 task_id 형식입니다: {task_id}",
        )

    task = AsyncResult(task_id, app=celery_app)

    # PENDING 상태일 때 실제로 존재하는 task인지 확인
    if task.state == "PENDING":
        # Redis에서 task 키가 존재하는지 확인
        # (PENDING은 존재하지 않는 task도 PENDING으로 반환됨)
        task_key = f"celery-task-meta-{task_id}"
        backend = celery_app.backend
        
        # Redis에 task 정보가 있는지 확인
        if not backend.get(task_key):
            raise HTTPException(
                status_code=404,
                detail=f"존재하지 않는 task_id입니다: {task_id}",
            )
        
        return {"task_id": task_id, "status": "queued"}

    elif task.state == "SUCCESS":
        # 임시 mock 응답: 실제 task.result 대신 mock_output.json 반환
        mock_result = _load_mock_output()
        return {
            "task_id": task_id,
            "status": "completed",
            "result": mock_result,
        }

    elif task.state == "FAILURE":
        return {
            "task_id": task_id,
            "status": "failed",
            "error": str(task.info),
        }

    else:
        # 커스텀 상태: PARSING, MODEL_1, MODEL_2, ... FORMATTING
        meta = task.info if isinstance(task.info, dict) else {}
        return {
            "task_id": task_id,
            "status": task.state,
            "detail": meta.get("detail", ""),
        }
