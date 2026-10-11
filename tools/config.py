"""Application settings, loaded from environment variables and `.env`.

Every value has a safe default except OPENROUTER_API_KEY. Relative paths are
resolved against the project root, so the app works from any working directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # --- LLM (OpenRouter, OpenAI-compatible API) ---
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    classifier_model: str = "openai/gpt-4o-mini"
    reasoning_model: str = "openai/gpt-4o"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4000
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    # --- Storage (relative to the project root) ---
    database_path: str = "data/incident_suite.db"
    chroma_persist_dir: str = "data/chroma"
    upload_dir: str = "data/uploads"
    runbook_dir: str = "data/runbooks"
    sample_log_dir: str = "data/sample_logs"
    app_log_file: str = "data/logs/app.jsonl"

    # --- Retrieval ---
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_top_k: int = 4
    # Cosine distance (0 = identical, 2 = opposite). Chunks farther than this are dropped.
    retrieval_max_distance: float = 0.6
    runbook_chunk_chars: int = 1500

    # --- Limits ---
    max_upload_mb: int = 20
    max_parsed_events: int = 20_000
    max_events_for_classification: int = 200
    max_jira_tickets: int = 5

    # --- Notifications: "simulation" never calls Slack/JIRA; "live" does ---
    slack_mode: Literal["simulation", "live"] = "simulation"
    slack_webhook_url: SecretStr = SecretStr("")
    slack_channel: str = "#incident-response"
    jira_mode: Literal["simulation", "live"] = "simulation"
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_project_key: str = "OPS"
    jira_issue_type: str = "Task"

    # --- API ---
    cors_origins: list[str] = ["http://localhost:8501", "http://127.0.0.1:8501", "http://localhost:8600", "http://127.0.0.1:8600"]
    # When set, every /api request must send it in the X-API-Key header (the UI does this automatically).
    api_key: SecretStr = SecretStr("")
    rate_limit_uploads_per_minute: int = 10

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_webhook_url.get_secret_value())

    @property
    def jira_configured(self) -> bool:
        return bool(self.jira_base_url and self.jira_email and self.jira_api_token.get_secret_value())

    @model_validator(mode="after")
    def _check_live_integrations(self) -> "Settings":
        if self.slack_mode == "live" and not self.slack_webhook_url.get_secret_value():
            raise ValueError("SLACK_MODE=live requires SLACK_WEBHOOK_URL.")
        if self.jira_mode == "live" and not (
            self.jira_base_url and self.jira_email and self.jira_api_token.get_secret_value()
        ):
            raise ValueError("JIRA_MODE=live requires JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN.")
        return self

    @staticmethod
    def _resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def database_file(self) -> Path:
        return self._resolve(self.database_path)

    @property
    def chroma_path(self) -> Path:
        return self._resolve(self.chroma_persist_dir)

    @property
    def upload_path(self) -> Path:
        return self._resolve(self.upload_dir)

    @property
    def runbook_path(self) -> Path:
        return self._resolve(self.runbook_dir)

    @property
    def sample_log_path(self) -> Path:
        return self._resolve(self.sample_log_dir)

    @property
    def app_log_path(self) -> Path:
        return self._resolve(self.app_log_file)


settings = Settings()
