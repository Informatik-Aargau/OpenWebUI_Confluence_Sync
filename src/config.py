from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    confluence_base_url: str
    confluence_pat: str

    openwebui_base_url: str
    openwebui_api_key: str

    database_url: str

    log_level: str = "INFO"

    # Converter engine: "markdownify" (default) or "docling"
    converter_engine: str = "markdownify"

    # HTTP client settings
    http_timeout: int = 60
    http_retries: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()  # type: ignore[call-arg]
