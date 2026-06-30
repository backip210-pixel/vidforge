from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "VidForge"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_data_dir: Path = Path("/data")
    temp_max_age_hours: int = 12
    app_username: str | None = None
    app_password: str | None = None
    app_cors_origins: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def jobs_dir(self) -> Path:
        return self.app_data_dir / "jobs"

    @property
    def outputs_dir(self) -> Path:
        return self.app_data_dir / "outputs"

    @property
    def temp_dir(self) -> Path:
        return self.app_data_dir / "tmp"

    @property
    def state_file(self) -> Path:
        return self.app_data_dir / "jobs.json"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for path in (settings.app_data_dir, settings.jobs_dir, settings.outputs_dir, settings.temp_dir):
        path.mkdir(parents=True, exist_ok=True)
    return settings
