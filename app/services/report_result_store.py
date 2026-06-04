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
    payload = {
        "task_id": safe_task_id,
        "saved_at": saved_at,
        "original_filename": original_filename,
        "country": country,
        "summary": build_report_summary(
            safe_task_id,
            result,
            original_filename=original_filename,
            country=country,
            saved_at=saved_at,
        ),
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
        items.append(summary)

    items.sort(key=lambda item: str(item.get("saved_at") or ""), reverse=True)
    return items[:limit]
