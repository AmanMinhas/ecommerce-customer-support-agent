from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ecommerce API"
    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_expiry_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_issuer: str = "ecommerce-api"
    jwt_audience: str = "ecommerce-clients"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
