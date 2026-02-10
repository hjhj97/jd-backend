"""모델 4: 도면 및 실시예 분석.

Model 3의 결과를 입력으로 받아, 도면 설명 및 실시예를 분석한다.

TODO: 실제 모델 로직으로 교체
"""

from loguru import logger


def run(prev_result: dict) -> dict:
    """이전 모델 결과를 입력받아 도면 및 실시예를 분석한다.

    Args:
        prev_result: Model 3의 출력 dict

    Returns:
        도면 및 실시예 분석 결과를 포함한 dict
    """
    logger.info("Model 4 실행 - 도면 및 실시예 분석")

    # TODO: 실제 모델 로직 구현
    result = {
        **prev_result,
        "model_4": {
            "description": "도면 및 실시예 분석",
            "figures_count": 0,
            "embodiments_summary": "extracted_from_text",
        },
    }

    logger.info("Model 4 완료")
    return result
