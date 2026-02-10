"""모델 1: 특허 텍스트에서 기본 서지사항(출원번호, 발명의 명칭 등)을 추출.

TODO: 실제 모델 로직으로 교체
"""

from loguru import logger


def run(text: str) -> dict:
    """텍스트를 입력받아 서지사항을 추출한다.

    Args:
        text: PDF에서 파싱된 전체 텍스트

    Returns:
        추출된 서지사항 dict
    """
    logger.info(f"Model 1 실행 - input_length={len(text)} chars")

    # TODO: 실제 모델 로직 구현
    result = {
        "model": "model_1",
        "description": "서지사항 추출",
        "input_length": len(text),
        "output": {
            "application_number": "extracted_from_text",
            "title": "extracted_from_text",
            "applicant": "extracted_from_text",
        },
    }

    logger.info("Model 1 완료")
    return result
