"""Persistent storage for generated report results."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _result_dir() -> Path:
    return Path(settings.REPORT_RESULT_DIR)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_task_id(task_id: str) -> str:
    parsed = uuid.UUID(str(task_id))
    return str(parsed)


def _request_meta_dir() -> Path:
    # 리포트 결과(*.json)와 섞이지 않도록 하위 디렉토리에 분리 보관한다.
    return _result_dir() / "requests"


def _request_meta_path(task_id: str) -> Path:
    return _request_meta_dir() / f"{_safe_task_id(task_id)}.json"


def save_request_meta(task_id: str, **fields: Any) -> None:
    """요청 접수 시각 등 작업 메타를 작은 파일로 저장한다(상태 무관 조회용).

    완료 전(대기/진행) 상태에서도 요청 시각을 표시할 수 있도록,
    리포트 결과와 별도 디렉토리에 보관한다. 실패해도 본 흐름을 막지 않는다.
    """
    try:
        out_dir = _request_meta_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {"task_id": _safe_task_id(task_id), "requested_at": _utc_now_iso(), **fields}
        path = _request_meta_path(task_id)
        tmp = out_dir / f".{_safe_task_id(task_id)}.tmp"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def get_request_meta(task_id: str) -> dict[str, Any] | None:
    try:
        path = _request_meta_path(task_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def build_report_summary(
    task_id: str,
    result: dict[str, Any],
    *,
    original_filename: str | None = None,
    country: str | None = None,
    saved_at: str | None = None,
) -> dict[str, Any]:
    basic = result.get("basic_info") if isinstance(result.get("basic_info"), dict) else {}
    patent = basic.get("patent") if isinstance(basic.get("patent"), dict) else {}
    applicant = patent.get("applicant") if isinstance(patent.get("applicant"), dict) else {}
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    ma = evaluation.get("ma_market_evaluation") if isinstance(evaluation.get("ma_market_evaluation"), dict) else {}
    tech = evaluation.get("tech_evaluation") if isinstance(evaluation.get("tech_evaluation"), dict) else {}
    rights = evaluation.get("rights_evaluation") if isinstance(evaluation.get("rights_evaluation"), dict) else {}
    final = evaluation.get("final_result") if isinstance(evaluation.get("final_result"), dict) else {}

    return {
        "task_id": task_id,
        "saved_at": saved_at,
        "original_filename": original_filename,
        "pdf_name": basic.get("pdf_name"),
        "country": country or basic.get("country"),
        "field": basic.get("field"),
        "title": patent.get("title"),
        "applicant": applicant.get("name"),
        "application_number": applicant.get("number"),
        "final_score": final.get("final_score"),
        "ma_market_score": ma.get("ma_market_score"),
        "tech_score": tech.get("tech_score"),
        "rights_score": rights.get("rights_score"),
    }


def archive_report_result(
    *,
    task_id: str,
    result: dict[str, Any],
    original_filename: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    safe_task_id = _safe_task_id(task_id)
    saved_at = _utc_now_iso()
    req_meta = get_request_meta(safe_task_id)
    requested_at = req_meta.get("requested_at") if req_meta else None
    summary = build_report_summary(
        safe_task_id,
        result,
        original_filename=original_filename,
        country=country,
        saved_at=saved_at,
    )
    if requested_at:
        summary["requested_at"] = requested_at
    payload = {
        "task_id": safe_task_id,
        "saved_at": saved_at,
        "requested_at": requested_at,
        "original_filename": original_filename,
        "country": country,
        "summary": summary,
        "result": result,
    }

    out_dir = _result_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{safe_task_id}.json"
    tmp = out_dir / f".{safe_task_id}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return payload


def get_archived_report(task_id: str) -> dict[str, Any] | None:
    safe_task_id = _safe_task_id(task_id)
    path = _result_dir() / f"{safe_task_id}.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        return None
    return payload


def list_archived_reports(limit: int = 500) -> list[dict[str, Any]]:
    out_dir = _result_dir()
    if not out_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for path in out_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        task_id = payload.get("task_id") or path.stem
        result = payload.get("result")
        summary = payload.get("summary")
        if not isinstance(summary, dict) and isinstance(result, dict):
            summary = build_report_summary(
                str(task_id),
                result,
                original_filename=payload.get("original_filename"),
                country=payload.get("country"),
                saved_at=payload.get("saved_at"),
            )
        if not isinstance(summary, dict):
            summary = {"task_id": str(task_id)}
        summary = {
            **summary,
            "task_id": str(task_id),
            "status": "completed",
            "has_result": isinstance(result, dict),
            "archived": True,
            "result_url": f"/result/{task_id}",
        }
        if not summary.get("requested_at") and payload.get("requested_at"):
            summary["requested_at"] = payload["requested_at"]
        items.append(summary)

    items.sort(key=lambda item: str(item.get("saved_at") or ""), reverse=True)
    return items[:limit]
