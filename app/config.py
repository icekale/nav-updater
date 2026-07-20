from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./nav-updater.db"
    data_dir: Path = Path("./data")
    session_secret: str = "development-only-change-me"
    initial_admin_username: str = "admin"
    initial_admin_password: str = "change-me"
    public_fund_timeout_seconds: float = 15.0
    ocr_backend: Literal["rapid", "paddle"] = "rapid"
    paddle_ocr_token: str = ""
    paddle_ocr_timeout_seconds: float = 120.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def ensure_data_dir(settings: Settings | None = None) -> Path:
    path = (settings or get_settings()).data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path
