from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # YNAB
    ynab_access_token: str = ""
    
    # Akahu
    akahu_app_token: str = ""
    akahu_user_token: str = ""
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./data/yanb_sync.db"
    
    # Security
    secret_key: str = "change-this-in-production"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
