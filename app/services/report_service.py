"""최종 보고서 포맷팅 서비스.

5개 모델의 최종 결과를 메인 서버가 기대하는 JSON 포맷으로 변환한다.
"""

from datetime import datetime, timezone

from loguru import logger


def format_report(model_result: dict) -> dict:
    """모델 파이프라인 최종 결과를 보고서 JSON으로 포맷팅.

    Args:
        model_result: Model 5의 최종 출력 dict

    Returns:
        메인 서버에 반환할 보고서 JSON dict
    """
    logger.info("보고서 포맷팅 시작")

    report = {
        "report": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "0.1.0",
            "sections": {
                "bibliographic": _extract_section(model_result, "output"),
                "claims_analysis": _extract_section(model_result, "model_2"),
                "technical_field": _extract_section(model_result, "model_3"),
                "figures_and_embodiments": _extract_section(
                    model_result, "model_4"
                ),
                "overall_evaluation": _extract_section(
                    model_result, "model_5"
                ),
            },
        }
    }

    logger.info("보고서 포맷팅 완료")
    return report


def _extract_section(data: dict, key: str) -> dict:
    """안전하게 섹션 데이터를 추출.

    키가 없을 경우 빈 dict를 반환하여 전체 보고서가 깨지지 않도록 한다.
    """
    section = data.get(key, {})
    if not isinstance(section, dict):
        logger.warning(f"섹션 '{key}'의 타입이 dict가 아님: {type(section)}")
        return {}
    return section
