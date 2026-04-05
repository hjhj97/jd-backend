"""OCR 텍스트에서 특허 타입(공개/등록)과 Kind Code를 룰 기반으로 감지."""

import re

# Kind code → patent_type 매핑
# 한국: A(공개), B1(등록), U(공개실용신안), Y1(등록실용신안), S(디자인)
# 미국: A1/A2/A9(공개), B1/B2(등록), S1(디자인), P1/P2/P3(식물), E1(재발행)
_PUBLIC_KIND_CODES = {"A", "A1", "A2", "A9"}
_REGISTRATION_KIND_CODES = {"B1", "B2"}
_OTHER_KIND_CODES = {"U", "Y1", "S", "S1", "P1", "P2", "P3", "E1"}


def _extract_kind_code_from_number(text: str) -> str | None:
    """문서번호 끝에 붙는 kind code를 추출.

    1차: INID (12) 필드 값 (가장 신뢰도 높음)
    2차: 국가코드·특허번호와 함께 등장하는 kind code

    대응 패턴 예시:
      US 2023/0123456 A1
      US 11,234,567 B2
      US RE12,345 E1
      KR 10-2023-0123456 A
      KR 10-1234567 B1
      KR 20-2023-0012345 U
      KR 20-0123456 Y1
      KR 30-2023-0012345 S
      WO 2023/123456 A1
      EP 1234567 B1
    """
    _KNOWN_KIND_CODES = {"A", "A1", "A2", "A9", "B1", "B2", "U", "Y1", "S", "S1", "P1", "P2", "P3", "E1"}

    # 1차: INID (12) 라인에서 kind code 추출
    # 실제 OCR 예시:
    #   "(12) 등록특허공보(B1)"  → 정상
    #   "(12) 동특특허공보(B1)"  → OCR 오인식, 괄호 안 B1은 정확
    #   "(12) A1"               → kind code 직접 표기
    #   "문서종별 B1"
    #   "Kind Code: A1"
    inid_pattern = re.compile(
        r"""
        (?:
            \(12\)\s*[^\n(]*\(([A-Z]\d{0,2})\)   # (12) [공보명칭](B1) — 괄호 안 kind code
            |
            \(12\)\s*([A-Z]\d?)(?:\s|$)           # (12) A1 — kind code 직접 표기
            |
            문서\s*종별\s+([A-Z]\d?)               # 문서종별 B1
            |
            kind\s*code\s*[:\-]?\s*([A-Z]\d?)     # Kind Code: A1
        )
        """,
        re.VERBOSE | re.IGNORECASE,
    )
    for m in inid_pattern.finditer(text):
        code = next((g for g in m.groups() if g), None)
        if code and code.strip().upper() in _KNOWN_KIND_CODES:
            return code.strip().upper()

    # 2차: 번호와 함께 등장하는 kind code
    # (56) 선행기술조사문헌에는 인용 특허(예: JP... A)가 많아 오탐이 빈번하므로 제외
    number_search_text = re.split(r"\(\s*56\s*\)", text, maxsplit=1)[0]

    number_pattern = re.compile(
        r"""
        (?:
            # US/EP/WO/JP/CN + 번호 + kind code
            (?:US|EP|WO|JP|CN)\s*(?:RE\s*)?[\d,/\-]+\s+([A-Z]\d?)
            |
            # KR 10-YYYY-NNNNNNN A  (특허 공개)
            10[\-\s]\d{4}[\-\s]\d{5,7}\s+([A-Z]\d?)
            |
            # KR 10-NNNNNNN B1  (특허 등록)
            10[\-\s]\d{7}\s+([A-Z]\d?)
            |
            # KR 20-YYYY-NNNNNNN U  (실용신안 공개)
            20[\-\s]\d{4}[\-\s]\d{5,7}\s+([A-Z]\d?)
            |
            # KR 20-NNNNNNN Y1  (실용신안 등록)
            20[\-\s]\d{7}\s+([A-Z]\d?)
            |
            # KR 30-YYYY-NNNNNNN S  (디자인)
            30[\-\s]\d{4}[\-\s]\d{5,7}\s+([A-Z]\d?)
            |
            30[\-\s]\d{7}\s+([A-Z]\d?)
        )
        \b
        """,
        re.VERBOSE,
    )
    for m in number_pattern.finditer(number_search_text):
        code = next((g for g in m.groups() if g), None)
        if code and code.strip().upper() in _KNOWN_KIND_CODES:
            return code.strip().upper()

    return None


def _detect_from_korean_header(text: str) -> tuple[str, str | None]:
    """한국어 공보 헤더 키워드로 감지."""
    # 등록 계열
    if "등록특허공보" in text:
        return "registration", "B1"
    if "등록실용신안공보" in text:
        return "other", "Y1"
    # 공개 계열
    if "공개특허공보" in text:
        return "public", "A"
    if "공개실용신안공보" in text:
        return "other", "U"
    # 디자인
    if "디자인공보" in text:
        return "other", "S"
    # 짧은 형태 fallback
    if "등록특허" in text:
        return "registration", "B1"
    if "공개특허" in text:
        return "public", "A"
    return "other", None


def _classify_patent_type(kind_code: str) -> str:
    """Kind code로부터 patent_type을 결정."""
    if kind_code in _PUBLIC_KIND_CODES:
        return "public"
    if kind_code in _REGISTRATION_KIND_CODES:
        return "registration"
    return "other"


def detect_patent_type(ocr_text: str) -> dict[str, str | None]:
    """OCR 텍스트에서 patent_type과 patent_kind_code를 감지.

    Returns:
        {"patent_type": "public"|"registration"|"other",
         "patent_kind_code": "A1"|"B2"|"U"|"Y1"|"S"|"S1"|"P1"|"E1"|... or None}
    """
    # 첫 페이지 범위만 검사
    header = ocr_text[:3000]

    # 1) Kind code 추출 시도 (가장 신뢰도 높음)
    kind_code = _extract_kind_code_from_number(header)

    if kind_code:
        patent_type = _classify_patent_type(kind_code)
        return {"patent_type": patent_type, "patent_kind_code": kind_code}

    # 2) 한국어 헤더 키워드 fallback
    patent_type, fallback_kind_code = _detect_from_korean_header(header)
    return {"patent_type": patent_type, "patent_kind_code": fallback_kind_code}
