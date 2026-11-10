from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    RALLY_ENV: str = "development"
    RALLY_API_HOST: str = "127.0.0.1"
    RALLY_API_PORT: int = 8765
    RALLY_DATABASE_URL: str = "sqlite:///./data/rally.db"

    RALLY_SPEND_MODE: Literal["zero_cost_only", "user_authorized_paid"] = "zero_cost_only"
    RALLY_ALLOW_PAID_PROVIDERS: bool = False
    RALLY_ALLOW_AUTOMATIC_TOP_UP: bool = False

    RALLY_MODEL_PROVIDER: str = "deterministic_local"
    RALLY_MODEL_NAME: str = "qwen2.5-coder:3b"
    RALLY_MODEL_BASE_URL: str = "http://127.0.0.1:11434"
    RALLY_MODEL_API_KEY: str = ""

    RALLY_META_ENABLED: bool = False
    RALLY_META_MODEL_NAME: str = ""
    RALLY_META_API_KEY: str = ""

    RALLY_CODING_AGENT_MODE: str = "manual_selection"
    RALLY_CODING_AGENT_ALLOWLIST: str = "ollama_cli,aider_local,custom_cli"
    RALLY_MAX_ACTIVE_CODING_AGENTS: int = 1
    RALLY_ONE_WRITER_PER_WORKSPACE: bool = True

    RALLY_BROWSER_HEADLESS: bool = True
    RALLY_BROWSER_DATA_DIR: str = "./data/browser-profile"
    RALLY_REQUIRE_APPROVAL_FOR_EXTERNAL_ACTIONS: bool = True
    RALLY_ALLOW_ADMIN_COMMANDS: bool = False
    RALLY_MAX_BUILD_CORRECTION_CYCLES: int = 3
    RALLY_SCREENSHOT_RETENTION_DAYS: int = 7

    @property
    def data_dir(self) -> Path:
        base = Path(__file__).resolve().parent.parent
        path = (base / "data").resolve()
        path.mkdir(parents=True, exist_ok=True)
        (path / "evidence").mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()