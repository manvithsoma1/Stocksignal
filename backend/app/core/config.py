from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stocksignal"
    app_env: str = "development"

    # PostgreSQL / Supabase connection pooling & SSL
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_sslmode: str = "prefer"  # Use 'require' for remote Supabase connections

    # Organization-level access control & multi-tenancy
    enable_tenant_isolation: bool = True
    auth_header_name: str = "X-Organization-ID"

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")


settings = Settings()
