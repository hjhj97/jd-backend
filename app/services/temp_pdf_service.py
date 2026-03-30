"""임시 PDF 저장/서명 URL/만료 정리 서비스."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.config import settings


@dataclass(frozen=True)
class TempPdfInfo:
    file_id: str
    path: Path
    expires_at: int
    signed_url: str


def _temp_dir() -> Path:
    path = Path(settings.TEMP_PDF_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _signing_key() -> bytes:
    key = settings.TEMP_PDF_SIGNING_KEY or settings.RUNPOD_API_KEY
    if not key:
        # 로컬 개발 안전장치. 운영에서는 반드시 환경변수로 설정 권장.
        key = "change-me-in-production"
    return key.encode("utf-8")


def _build_signature(file_id: str, expires_at: int) -> str:
    payload = f"{file_id}:{expires_at}".encode("utf-8")
    digest = hmac.new(_signing_key(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _build_signed_url(file_id: str, expires_at: int, signature: str) -> str:
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    return (
        f"{base_url}/api/v1/temp-pdf/{file_id}"
        f"?expires={expires_at}&sig={signature}"
    )


def cleanup_expired_temp_pdfs() -> int:
    """TTL이 지난 임시 PDF 파일을 정리한다."""
    ttl_seconds = max(int(settings.TEMP_PDF_TTL_SECONDS), 1)
    now = int(time.time())
    removed = 0

    for path in _temp_dir().glob("*.pdf"):
        try:
            age = now - int(path.stat().st_mtime)
            if age > ttl_seconds:
                path.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning(f"임시 PDF 정리 실패 - path={path}, error={exc}")

    return removed


def save_temp_pdf(pdf_bytes: bytes) -> TempPdfInfo:
    """임시 PDF를 저장하고 서명된 다운로드 URL을 반환한다."""
    cleanup_expired_temp_pdfs()

    file_id = uuid.uuid4().hex
    path = _temp_dir() / f"{file_id}.pdf"
    path.write_bytes(pdf_bytes)

    now = int(time.time())
    os.utime(path, (now, now))

    ttl_seconds = max(int(settings.TEMP_PDF_TTL_SECONDS), 1)
    expires_at = now + ttl_seconds
    signature = _build_signature(file_id, expires_at)
    signed_url = _build_signed_url(file_id, expires_at, signature)

    return TempPdfInfo(
        file_id=file_id,
        path=path,
        expires_at=expires_at,
        signed_url=signed_url,
    )


def resolve_temp_pdf_path(file_id: str, *, expires_at: int, signature: str) -> Path:
    """서명/만료를 검증하고 파일 경로를 반환한다."""
    cleanup_expired_temp_pdfs()

    now = int(time.time())
    if expires_at < now:
        # 만료된 URL 접근 시 즉시 파일 정리 시도
        expired_path = _temp_dir() / f"{file_id}.pdf"
        expired_path.unlink(missing_ok=True)
        raise PermissionError("expired")

    expected_signature = _build_signature(file_id, expires_at)
    if not hmac.compare_digest(signature, expected_signature):
        raise PermissionError("invalid_signature")

    path = _temp_dir() / f"{file_id}.pdf"
    if not path.exists():
        raise FileNotFoundError(file_id)
    return path
