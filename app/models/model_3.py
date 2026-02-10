"""모델 3: 기술 분야 및 키워드 분석.

Model 2의 결과를 입력으로 받아, 기술 분야 및 키워드를 분석한다.

TODO: 실제 모델 로직으로 교체
"""

from loguru import logger


def run(prev_result: dict) -> dict:
    """이전 모델 결과를 입력받아 기술 분야 및 키워드를 분석한다.

    Args:
        prev_result: Model 2의 출력 dict

    Returns:
        기술 분야 및 키워드 분석 결과를 포함한 dict
    """
    logger.info("Model 3 실행 - 기술 분야 및 키워드 분석")

    # TODO: 실제 모델 로직 구현
    result = {
        **prev_result,
        "model_3": {
            "description": "기술 분야 및 키워드 분석",
            "technical_field": "extracted_from_text",
            "keywords": [],
        },
    }

    logger.info("Model 3 완료")
    return result
