import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

from celery.result import AsyncResult
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from app.services.s3_service import upload_pdf
from app.services.temp_pdf_service import resolve_temp_pdf_path, save_temp_pdf
from app.worker.celery_app import celery_app
from app.worker.tasks import process_patent

router = APIRouter()

# 최대 업로드 크기: 100MB (200페이지 PDF 대비)
_MAX_PDF_SIZE = 100 * 1024 * 1024
_MOCK_OUTPUT_PATH = Path(__file__).resolve().parents[2] / "mock_output.json"
_JDPATENT_ERROR_MESSAGES = {
    "not_a_patent_document": "평가 대상 특허가 아닙니다",
    "runpod_pdf_too_large": "OCR 처리 가능한 파일 크기를 초과했습니다.",
    "runpod_bad_request": "OCR 요청 형식이 올바르지 않습니다.",
    "runpod_timeout": "OCR 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
    "runpod_http_400": "OCR 요청이 거부되었습니다.",
    "runpod_http_413": "OCR 처리 가능한 파일 크기를 초과했습니다.",
    "runpod_http_unknown": "OCR 요청 처리 중 오류가 발생했습니다.",
    "jdpatent_timeout": "특허 분석 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
}
_DEFAULT_JDPATENT_ERROR_MESSAGE = "특허 공보 문서 처리 도중 에러가 발생했습니다."


def _is_valid_pdf_header(pdf_bytes: bytes) -> bool:
    """PDF 파일 헤더(%PDF-)가 포함되어 있는지 확인."""
    if not pdf_bytes:
        return False
    # 일부 생성기는 시작부에 개행/공백을 둘 수 있어 처음 1KB 범위에서 탐색.
    return b"%PDF-" in pdf_bytes[:1024]


@lru_cache(maxsize=1)
def _load_mock_output() -> dict:
    with _MOCK_OUTPUT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


_RESULT_COMPLETED_EXAMPLE = {
    "success": True,
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
    "status": "completed",
    "result": {"basic_info": {}, "naics": {}, "evaluation": {}, "recommend_companies": {}},
}


def _extract_jdpatent_error_code(raw_error: str) -> str | None:
    """JDPatent 에러 문자열에서 표준 error code를 추출한다."""
    text = (raw_error or "").strip()
    if not text:
        return None
    text_lc = text.lower()

    # 0) known timeout patterns
    if "jdpatent polling timeout" in text_lc:
        return "jdpatent_timeout"
    if "runpod timeout" in text_lc or "runpod 타임아웃" in text_lc:
        return "runpod_timeout"

    # 1) pure code 형태 (예: "not_a_patent_document")
    if re.fullmatch(r"[a-z0-9_]+", text):
        return text

    # 2) JSON dict 문자열 형태 (예: '{"error":"..."}')
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            return parsed["error"]
    except Exception:
        pass

    # 3) 문자열 내부에 JSON 조각이 섞인 형태
    match = re.search(r'"error"\s*:\s*"([a-z0-9_]+)"', text)
    if match:
        return match.group(1)

    return None


@router.post(
    "/analyze",
    status_code=202,
    responses={
        202: {
            "description": "분석 요청 접수",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                        "status": "queued",
                        "msg": "분석 요청이 접수되었습니다. GET /api/v1/result/{task_id}로 결과를 확인하세요.",
                    }
                }
            },
        },
        400: {
            "description": "잘못된 요청 (파일 형식/빈 파일/country 값 오류 등)",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_file_type": {
                            "summary": "PDF가 아닌 파일",
                            "value": {
                                "success": False,
                                "msg": "PDF 파일만 허용됩니다. (받은 파일: sample.txt)",
                                "request_id": "abcd1234",
                            },
                        },
                        "invalid_country": {
                            "summary": "허용되지 않는 country 값",
                            "value": {
                                "detail": "country는 'KR' 또는 'US'만 허용됩니다.",
                            },
                        },
                    }
                }
            },
        },
        413: {
            "description": "파일 크기 초과",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "파일 크기가 너무 큽니다. (최대 100MB)",
                        "request_id": "abcd1234",
                    }
                }
            },
        },
        422: {
            "description": "요청 데이터 검증 실패",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "요청 데이터 검증에 실패했습니다.",
                        "errors": [],
                        "request_id": "abcd1234",
                    }
                }
            },
        },
        500: {
            "description": "서버 내부 오류",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "서버 내부 오류가 발생했습니다.",
                        "request_id": "abcd1234",
                    }
                }
            },
        },
    },
)
async def analyze_patent(
    request: Request,
    file: UploadFile = File(...),
    country: str = Form("KR", description="특허 국가 코드. 'KR'(한국) 또는 'US'(미국)", enum=["KR", "US"]),
):
    """특허 PDF를 업로드하여 분석을 시작한다.

    - 즉시 task_id를 반환(202 Accepted)
    - GET /result/{task_id} 로 결과를 폴링

    **Request body (multipart/form-data)**
    - `file`: 분석할 특허 PDF 파일
    - `country`: 특허 국가 코드 (`KR` 또는 `US`)
    """
    request_id: str = getattr(request.state, "request_id", "unknown")

    if country not in ("KR", "US"):
        raise HTTPException(status_code=400, detail="country는 'KR' 또는 'US'만 허용됩니다.")

    # --- 파일 검증 ---
    if not file.filename:
        raise HTTPException(status_code=400, detail="파일명이 비어있습니다.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"PDF 파일만 허용됩니다. (받은 파일: {file.filename})",
        )

    # PDF 바이트 읽기
    pdf_bytes = await file.read()

    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    if len(pdf_bytes) > _MAX_PDF_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"파일 크기가 너무 큽니다. (최대 {_MAX_PDF_SIZE // (1024*1024)}MB)",
        )
    if not _is_valid_pdf_header(pdf_bytes):
        raise HTTPException(
            status_code=400,
            detail="유효한 PDF 파일이 아닙니다. 파일 형식을 확인해 주세요.",
        )

    s3_key = f"uploads/{uuid.uuid4().hex}.pdf"
    pdf_url = upload_pdf(pdf_bytes, s3_key)
    task = process_patent.delay(None, request_id, file.filename, pdf_url, country, s3_key)
    logger.info(
        f"PDF 업로드 완료 - filename={file.filename}, "
        f"size={len(pdf_bytes)} bytes, "
        f"s3_key={s3_key}"
    )

    logger.info(f"Task 큐잉 완료 - task_id={task.id}")

    return {
        "success": True,
        "task_id": task.id,
        "status": "queued",
        "msg": "분석 요청이 접수되었습니다. GET /api/v1/result/{task_id}로 결과를 확인하세요.",
    }


@router.get("/temp-pdf/{file_id}")
async def get_temp_pdf(file_id: str, expires: int, sig: str):
    """RunPod worker가 접근할 임시 PDF 다운로드 엔드포인트."""
    try:
        path = resolve_temp_pdf_path(file_id, expires_at=expires, signature=sig)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="임시 파일을 찾을 수 없습니다.") from exc

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=f"{file_id}.pdf",
    )


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
                                "success": True,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "queued",
                            },
                        },
                        "processing": {
                            "summary": "처리 중",
                            "value": {
                                "success": True,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "MODEL_2",
                                "msg": "Model 2/5 실행 중",
                            },
                        },
                        "completed_mock": {
                            "summary": "완료 (mock_output.json 반환)",
                            "value": _RESULT_COMPLETED_EXAMPLE,
                        },
                        "failed": {
                            "summary": "실패",
                            "value": {
                                "success": False,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "failed",
                                "msg": "에러 메시지",
                            },
                        },
                        "failed_not_patent": {
                            "summary": "실패 - 특허 문서 아님",
                            "value": {
                                "success": False,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "not_a_patent_document",
                                "msg": "평가 대상 특허가 아닙니다",
                            },
                        },
                        "failed_runpod_large_pdf": {
                            "summary": "실패 - OCR 입력 용량 초과",
                            "value": {
                                "success": False,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "runpod_pdf_too_large",
                                "msg": "OCR 처리 가능한 파일 크기를 초과했습니다.",
                            },
                        },
                        "failed_runpod_timeout": {
                            "summary": "실패 - OCR 타임아웃",
                            "value": {
                                "success": False,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "runpod_timeout",
                                "msg": "OCR 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
                            },
                        },
                        "failed_jdpatent_timeout": {
                            "summary": "실패 - JDPatent 타임아웃",
                            "value": {
                                "success": False,
                                "task_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                                "status": "jdpatent_timeout",
                                "msg": "특허 분석 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
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
                        "success": False,
                        "msg": "유효하지 않은 task_id 형식입니다: invalid-id",
                        "request_id": "abcd1234",
                    }
                }
            },
        },
        404: {
            "description": "존재하지 않는 task_id",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "존재하지 않는 task_id입니다: a1b2c3d4-e5f6-7890-abcd-ef0123456789",
                        "request_id": "abcd1234",
                    }
                }
            },
        },
        422: {
            "description": "요청 데이터 검증 실패",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "요청 데이터 검증에 실패했습니다.",
                        "errors": [],
                        "request_id": "abcd1234",
                    }
                }
            },
        },
        500: {
            "description": "서버 내부 오류",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "msg": "서버 내부 오류가 발생했습니다.",
                        "request_id": "abcd1234",
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
        
        return {"success": True, "task_id": task_id, "status": "queued"}

    elif task.state == "SUCCESS":
        return {
            "success": True,
            "task_id": task_id,
            "status": "completed",
            "result": task.result,
        }

    elif task.state == "FAILURE":
        raw_error = str(task.info)
        error_code = _extract_jdpatent_error_code(raw_error)
        if error_code:
            return {
                "success": False,
                "task_id": task_id,
                "status": error_code,
                "msg": _JDPATENT_ERROR_MESSAGES.get(
                    error_code, _DEFAULT_JDPATENT_ERROR_MESSAGE
                ),
            }

        return {
            "success": False,
            "task_id": task_id,
            "status": "failed",
            "msg": _DEFAULT_JDPATENT_ERROR_MESSAGE,
        }

    else:
        # 커스텀 상태: PARSING, MODEL_1, MODEL_2, ... FORMATTING
        meta = task.info if isinstance(task.info, dict) else {}
        return {
            "success": True,
            "task_id": task_id,
            "status": task.state,
            "msg": meta.get("msg", ""),
        }
