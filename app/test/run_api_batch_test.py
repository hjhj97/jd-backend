#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class TestCase:
    pdf_path: Path
    expected_success: bool
    country: str
    country_source: str


@dataclass
class CaseRun:
    case: TestCase
    task_id: str | None = None
    submit_status_code: int | None = None
    submit_response: dict[str, Any] | None = None
    final_status: str | None = None
    final_response: dict[str, Any] | None = None
    actual_success: bool | None = None
    passed: bool = False
    note: str = ""
    status_history: list[str] = field(default_factory=list)


def _build_multipart_body(file_path: Path, country: str) -> tuple[bytes, str]:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    file_bytes = file_path.read_bytes()
    file_ct = mimetypes.guess_type(file_path.name)[0] or "application/pdf"

    parts: list[bytes] = []

    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="country"\r\n\r\n'
            f"{country}\r\n"
        ).encode("utf-8")
    )

    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: {file_ct}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _request_json(
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    req = Request(url=url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    try:
        with urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        status_code = e.code
        raw = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
    except URLError as e:
        return 0, {"success": False, "msg": f"network_error: {e}"}
    except Exception as e:  # pragma: no cover
        return 0, {"success": False, "msg": f"request_exception: {e}"}

    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            return status_code, payload
        return status_code, {"raw": payload}
    except json.JSONDecodeError:
        return status_code, {"raw": raw}


def _detect_country(pdf_path: Path) -> tuple[str, str]:
    lowered_parts = [p.lower() for p in pdf_path.parts]
    if "kr" in lowered_parts:
        return "KR", "dir:kr"
    if "us" in lowered_parts:
        return "US", "dir:us"

    stem = pdf_path.stem.lower()
    if stem.startswith("us"):
        return "US", "filename:us*"
    if stem.startswith("kr") or stem.startswith("10"):
        return "KR", "filename:kr*|10*"

    return "KR", "fallback:KR"


def _discover_cases(test_root: Path) -> list[TestCase]:
    cases: list[TestCase] = []
    for bucket, expected_success in (("valid", True), ("invalid", False)):
        base = test_root / bucket
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() != ".pdf":
                continue
            country, source = _detect_country(p)
            cases.append(
                TestCase(
                    pdf_path=p,
                    expected_success=expected_success,
                    country=country,
                    country_source=source,
                )
            )
    return cases


def _status_of(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    return str(status) if status is not None else "unknown"


def _is_terminal(payload: dict[str, Any]) -> bool:
    """
    종료 판정은 보수적으로 처리한다.
    - success=false: 실패 종료
    - success=true + status=completed: 성공 종료
    그 외 상태(예: JDPATENT_PROCESSING)는 진행 중으로 간주한다.
    """
    success = payload.get("success")
    status = _status_of(payload)
    if success is False:
        return True
    if success is True and str(status).lower() == "completed":
        return True
    return False


def _evaluate_result(run: CaseRun) -> None:
    expected = run.case.expected_success
    actual = bool(run.actual_success)
    run.passed = expected == actual
    if run.passed:
        return

    if run.task_id is None:
        run.note = "submit failed"
        return
    if run.final_status is None:
        run.note = "timed out"
        return
    run.note = f"expected={'success' if expected else 'failure'}, actual_status={run.final_status}"


def run_tests(
    *,
    base_url: str,
    test_root: Path,
    poll_interval: float,
    max_wait_seconds: int,
    request_timeout_seconds: int,
) -> int:
    cases = _discover_cases(test_root)
    if not cases:
        print(f"[ERROR] PDF not found under: {test_root}")
        return 2

    print(f"Discovered {len(cases)} PDF(s)")
    runs: list[CaseRun] = [CaseRun(case=c) for c in cases]

    analyze_url = f"{base_url.rstrip('/')}/api/v1/analyze"
    result_base = f"{base_url.rstrip('/')}/api/v1/result"

    for run in runs:
        body, content_type = _build_multipart_body(run.case.pdf_path, run.case.country)
        status_code, payload = _request_json(
            "POST",
            analyze_url,
            timeout=request_timeout_seconds,
            headers={"Content-Type": content_type, "Accept": "application/json"},
            data=body,
        )
        run.submit_status_code = status_code
        run.submit_response = payload

        if status_code == 202 and payload.get("success") is True and payload.get("task_id"):
            run.task_id = str(payload["task_id"])
            print(
                f"[SUBMIT] {run.case.pdf_path} -> task_id={run.task_id} "
                f"(country={run.case.country}, by={run.case.country_source})"
            )
        else:
            run.actual_success = False
            run.final_status = _status_of(payload)
            run.final_response = payload
            run.note = f"analyze failed: http={status_code}"
            print(
                f"[SUBMIT-FAIL] {run.case.pdf_path} http={status_code} payload={json.dumps(payload, ensure_ascii=False)}"
            )

    pending = [r for r in runs if r.task_id]
    deadline = time.time() + max_wait_seconds

    while pending and time.time() < deadline:
        next_pending: list[CaseRun] = []
        for run in pending:
            result_url = f"{result_base}/{run.task_id}"
            status_code, payload = _request_json(
                "GET",
                result_url,
                timeout=request_timeout_seconds,
                headers={"Accept": "application/json"},
            )

            status = _status_of(payload)
            if not run.status_history or run.status_history[-1] != status:
                run.status_history.append(status)
                print(f"[POLL] {run.task_id} -> {status}")

            if status_code == 200 and _is_terminal(payload):
                run.final_response = payload
                run.final_status = status
                run.actual_success = payload.get("success") is True and status == "completed"
            else:
                next_pending.append(run)

        pending = next_pending
        if pending:
            time.sleep(poll_interval)

    for run in pending:
        run.actual_success = False
        run.final_status = "timeout"
        run.note = f"timeout after {max_wait_seconds}s"

    pass_count = 0
    fail_count = 0

    print("\n=== CASE RESULT ===")
    for run in runs:
        _evaluate_result(run)
        rel = run.case.pdf_path.relative_to(test_root)
        expected = "success" if run.case.expected_success else "failure"
        actual = run.final_status or "unknown"
        verdict = "PASS" if run.passed else "FAIL"
        msg = (
            f"[{verdict}] {rel} expected={expected} actual={actual} "
            f"country={run.case.country} task_id={run.task_id or '-'}"
        )
        if run.note:
            msg += f" note={run.note}"
        print(msg)
        if run.passed:
            pass_count += 1
        else:
            fail_count += 1

    print("\n=== SUMMARY ===")
    print(f"total={len(runs)} pass={pass_count} fail={fail_count}")

    return 0 if fail_count == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch test runner: app/test/{valid,invalid} PDF를 /api/v1/analyze에 업로드하고 "
            "/api/v1/result/{task_id}를 폴링해 기대 결과를 검증합니다."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1",
        help="API base URL (default: http://127.0.0.1)",
    )
    parser.add_argument(
        "--test-root",
        default=str(Path(__file__).resolve().parent),
        help="테스트 루트 디렉토리 (default: app/test)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="result 폴링 간격(초), default=2.0",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=1800,
        help="각 배치 전체 최대 대기 시간(초), default=1800",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=60,
        help="HTTP 요청 타임아웃(초), default=60",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_tests(
        base_url=args.base_url,
        test_root=Path(args.test_root).resolve(),
        poll_interval=args.poll_interval,
        max_wait_seconds=args.max_wait_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
