from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RISOTO API"
    app_env: str = "development"
    database_url: str
    cors_origins: str = "http://localhost:5173"
    naver_api_hub_client_id: str = ""
    naver_api_hub_client_secret: str = ""
    tavily_api_key: str = ""
    serpapi_api_key: str = ""
    youtube_api_key: str = ""
    sentiment_model_name: str = "daekeun-ml/koelectra-small-v3-nsmc"
    realtime_interval_seconds: int = 900
    realtime_overlap_minutes: int = 60
    baseline_min_articles: int = 20
    baseline_min_days: int = 3

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
