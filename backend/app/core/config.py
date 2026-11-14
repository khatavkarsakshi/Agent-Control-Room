"""Central application configuration.

All runtime configuration is supplied through environment variables (12-factor).
``.env`` files are a developer convenience only; inside Docker the variables are
injected by Compose. Values are validated by Pydantic, so a typo in an integer
setting fails at startup instead of at the first request.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        # Resolve a repo-root .env both when running from backend/ and from /code.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_name: str = "Agent Control Room"
    app_version: str = "0.4.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database --------------------------------------------------------
    database_url: str = "postgresql+psycopg://acr:acr_password@localhost:5432/acr"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    database_echo: bool = False

    # --- Redis -----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    events_enabled: bool = True

    # --- LLM providers ---------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str | None = None
    anthropic_api_key: str = ""
    local_llm_base_url: str = "http://localhost:11434/v1"
    local_llm_api_key: str = "local"
    # Comma separated prefixes that should be routed to the local OpenAI-compatible endpoint.
    local_model_prefixes: str = ""
    default_model: str = "gpt-4o-mini"

    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=4, ge=0, le=10)
    llm_retry_base_delay_seconds: float = Field(default=1.0, gt=0)
    llm_retry_max_delay_seconds: float = Field(default=30.0, gt=0)

    # --- Agent execution -------------------------------------------------
    agent_max_iterations: int = Field(default=8, ge=1, le=50)
    agent_max_tokens: int = Field(default=2048, ge=1, le=32000)
    agent_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    agent_default_timeout_seconds: int = Field(default=600, ge=5)
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    http_tool_allow_private_networks: bool = False
    http_tool_max_response_bytes: int = Field(default=100_000, ge=1024)
    run_max_input_chars: int = Field(default=100_000, ge=1)

    # --- Traces / retention ----------------------------------------------
    trace_retention_days: int = Field(default=30, ge=1)
    trace_cleanup_batch_size: int = Field(default=5000, ge=100)
    daily_cost_retention_days: int = Field(default=365, ge=1)

    # --- Evaluation / replay (Phase 4) -----------------------------------
    # Automatic evaluation of every completed run. Off by default: heuristics
    # are free, but Ragas/DeepEval metrics call a judge model and cost money.
    evaluation_auto_on_complete: bool = False
    evaluation_auto_evaluator: Literal["custom", "ragas", "deepeval"] = "custom"
    # Comma separated metric names; empty means "the evaluator's defaults".
    evaluation_auto_metrics: str = ""
    # Contexts supplied to RAG metrics: user-provided, else extracted from spans.
    evaluation_max_contexts: int = Field(default=20, ge=1, le=100)
    evaluation_max_context_chars: int = Field(default=20_000, ge=100)
    evaluation_reference_max_chars: int = Field(default=20_000, ge=100)
    # Optional judge model for DeepEval (Ragas reads its own configuration).
    evaluation_judge_model: str = ""
    # Cap the similarity work performed when comparing replay outputs.
    replay_similarity_max_chars: int = Field(default=20_000, ge=100)

    # --- Rate limiting ---------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_exempt_paths: str = "/health,/docs,/redoc,/openapi.json"
    trust_proxy_headers: bool = False

    # --- CORS ------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"
    cors_allow_credentials: bool = False

    # --- OpenTelemetry ---------------------------------------------------
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "agent-control-room"
    otel_insecure: bool = True
    otel_traces_sampler_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    otel_export_metrics: bool = True
    otel_export_logs: bool = True

    # --- Cost calculation ------------------------------------------------
    # JSON object: {"model-name": {"input": 1.0, "output": 2.0}} in USD / 1M tokens.
    custom_pricing_json: str = "{}"

    # --- Celery ----------------------------------------------------------
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_task_always_eager: bool = False
    celery_task_time_limit_seconds: int = Field(default=900, ge=30)
    celery_task_soft_time_limit_seconds: int = Field(default=840, ge=30)
    celery_result_expires_seconds: int = Field(default=3600, ge=60)

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # ---- Derived helpers ------------------------------------------------
    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def rate_limit_exempt_list(self) -> list[str]:
        return [path.strip() for path in self.rate_limit_exempt_paths.split(",") if path.strip()]

    @property
    def local_model_prefix_list(self) -> list[str]:
        return [prefix.strip().lower() for prefix in self.local_model_prefixes.split(",") if prefix.strip()]

    @property
    def evaluation_auto_metric_list(self) -> list[str]:
        return [name.strip() for name in self.evaluation_auto_metrics.split(",") if name.strip()]

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @field_validator("custom_pricing_json")
    @classmethod
    def _validate_pricing_json(cls, value: str) -> str:
        """Fail fast on malformed pricing overrides instead of at first run."""
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"custom_pricing_json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("custom_pricing_json must be a JSON object")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        normalized = value.upper()
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached for import-time use)."""
    return Settings()


settings = get_settings()
