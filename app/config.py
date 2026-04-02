from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # RunPod
    # Never hardcode real endpoint IDs or API keys in source.
    RUNPOD_API_URL: str = "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID"
    RUNPOD_API_KEY: str = ""
    RUNPOD_RUN_URL: str | None = None
    RUNPOD_STATUS_URL: str | None = None
    RUNPOD_OCR_DUMP_DIR: str = "/app/logs/ocr_results"

    # 임시 PDF URL 전달용
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    TEMP_PDF_DIR: str = "/app/tmp/pdfs"
    TEMP_PDF_TTL_SECONDS: int = 600
    TEMP_PDF_CLEANUP_INTERVAL_SECONDS: int = 60
    TEMP_PDF_SIGNING_KEY: str = ""

    # AWS S3
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "ap-northeast-2"
    AWS_S3_BUCKET: str = ""
    AWS_S3_PRESIGNED_URL_EXPIRES: int = 600  # presigned URL 유효 시간 (초)

    # App
    LOG_LEVEL: str = "INFO"

    # JDPatent Internal API
    JDPATENT_API_URL: str = "http://jdpatent-api:8001"
    JDPATENT_SUBMIT_TIMEOUT_SECONDS: float = 15.0
    JDPATENT_POLL_TIMEOUT_SECONDS: float = 900.0
    JDPATENT_POLL_INTERVAL_SECONDS: float = 2.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
