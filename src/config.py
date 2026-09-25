from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Confluence
    confluence_base_url: str
    confluence_pat: str

    # OpenWebUI
    owui_base_url: str
    owui_pat: str

    # Database
    database_url: str

    # MDM (Master Data Management)
    mdm_client_id: str
    mdm_client_secret: str
    mdm_endpoint_organisation: str
    mdm_endpoint_user: str
    mdm_endpoint_user_egov: str
    mdm_bulk_size: int = 100

    # OpenWebUI DB Details (if needed separately from database_url)
    owui_db_host: str | None = None
    owui_db_port: int | None = None
    owui_db_user: str | None = None
    owui_db_password: str | None = None
    owui_db_name_analytics: str | None = None
    owui_db_name_doc: str | None = None
    owui_db_name_conf: str | None = None

    log_level: str = "INFO"

    # Converter engine: "markdownify" (default) or "docling"
    converter_engine: str = "markdownify"

    # HTTP client settings
    http_timeout: int = 60
    http_retries: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()  # type: ignore[call-arg]
