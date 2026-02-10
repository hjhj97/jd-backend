from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # RunPod
    RUNPOD_API_URL: str = "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID"
    RUNPOD_API_KEY: str = "your_runpod_api_key_here"

    # App
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
