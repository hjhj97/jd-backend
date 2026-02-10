"""모델 5: 최종 종합 평가.

Model 4의 결과를 입력으로 받아, 전체 특허를 종합적으로 평가한다.

TODO: 실제 모델 로직으로 교체
"""

from loguru import logger


def run(prev_result: dict) -> dict:
    """이전 모델 결과를 입력받아 종합 평가를 수행한다.

    Args:
        prev_result: Model 4의 출력 dict

    Returns:
        종합 평가 결과를 포함한 dict
    """
    logger.info("Model 5 실행 - 종합 평가")

    # TODO: 실제 모델 로직 구현
    result = {
        **prev_result,
        "model_5": {
            "description": "종합 평가",
            "novelty_score": 0.0,
            "summary": "extracted_from_text",
            "recommendation": "extracted_from_text",
        },
    }

    logger.info("Model 5 완료")
    return result
