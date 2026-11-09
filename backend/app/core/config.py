from pydantic import field_validator
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

    @field_validator("database_url", mode="after")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """
        Render and various cloud providers supply DATABASE_URL starting with
        postgres:// or postgresql:// without the driver specification.
        Normalize to postgresql+psycopg:// so SQLAlchemy uses psycopg (v3).
        """
        if v.startswith("postgres://"):
            v = "postgresql+psycopg://" + v[len("postgres://"):]
        elif v.startswith("postgresql://") and not v.startswith("postgresql+psycopg://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v


settings = Settings()
