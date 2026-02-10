"""모델 2: 청구항 분석.

Model 1의 결과를 입력으로 받아, 청구항 관련 정보를 분석한다.

TODO: 실제 모델 로직으로 교체
"""

from loguru import logger


def run(prev_result: dict) -> dict:
    """이전 모델 결과를 입력받아 청구항을 분석한다.

    Args:
        prev_result: Model 1의 출력 dict

    Returns:
        청구항 분석 결과를 포함한 dict
    """
    logger.info("Model 2 실행 - 청구항 분석")

    # TODO: 실제 모델 로직 구현
    result = {
        **prev_result,
        "model_2": {
            "description": "청구항 분석",
            "claims_count": 0,
            "independent_claims": [],
            "dependent_claims": [],
        },
    }

    logger.info("Model 2 완료")
    return result
