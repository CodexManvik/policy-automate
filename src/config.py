"""
Centralized runtime configuration for the Claims Auto-Adjudication Engine.

All environment variables are declared here with type annotations and defaults.
Load from a .env file automatically via pydantic-settings BaseSettings.

Usage:
    from config import settings
    print(settings.llm_provider)
"""

from __future__ import annotations

from typing import List, Optional, Annotated, Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode


class Settings(BaseSettings):
    """
    Application settings resolved from environment variables and optional .env file.

    Priority (highest to lowest):
      1. OS environment variables
      2. .env file (project root)
      3. Field defaults defined below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM / Semantic Agent
    # ------------------------------------------------------------------
    llm_provider: str = Field(
        default="default",
        description="LLM backend to use: 'local' | 'openai' | 'default'",
    )
    llm_url: str = Field(
        default="http://127.0.0.1:8080",
        description="Base URL of the local llama.cpp server (used when llm_provider='local')",
    )
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key (used when llm_provider='openai')",
    )
    reasoning_on: bool = Field(
        default=True,
        description="Enable extended chain-of-thought reasoning in local LLM completions",
    )
    llm_connect_timeout_s: int = Field(
        default=60,
        ge=1,
        description="Connect timeout in seconds for local LLM requests",
    )
    llm_read_timeout_s: int = Field(
        default=300,
        ge=1,
        description="Read timeout in seconds for local LLM requests",
    )

    # ------------------------------------------------------------------
    # Adjudication Thresholds
    # ------------------------------------------------------------------
    auto_approve_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Confidence >= this value triggers automatic claim approval",
    )
    assisted_review_threshold: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Confidence in [assisted_review_threshold, auto_approve_threshold) routes to ASSISTED_REVIEW",
    )
    medical_review_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Confidence in [medical_review_threshold, assisted_review_threshold) routes to MEDICAL_REVIEW",
    )

    # ------------------------------------------------------------------
    # Context Staleness Guard (Gap 11)
    # ------------------------------------------------------------------
    context_max_age_minutes: int = Field(
        default=120,
        ge=1,
        description=(
            "Maximum age in minutes for a ClaimContext.context_assembled_at timestamp. "
            "Contexts older than this are rejected before adjudication begins to prevent "
            "stale SI balances or policy state from producing incorrect decisions."
        ),
    )


    # ------------------------------------------------------------------
    # API Server
    # ------------------------------------------------------------------
    cors_origins: Annotated[List[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed CORS origins. Use ['*'] ONLY for internal/development deployments.",
    )
    log_level: str = Field(
        default="INFO",
        description="Python logging level: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )
    app_version: str = Field(
        default="2.0.0",
        description="API version string embedded in health response",
    )
    product_json_version: str = Field(
        default="R3_v2.1_2025-01-15",
        description="Default product rules version to load at startup",
    )

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    telemetry_file: str = Field(
        default="metrics_telemetry.jsonl",
        description="Path to the JSONL telemetry log file",
    )

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return upper

    @field_validator("llm_provider")
    @classmethod
    def _validate_llm_provider(cls, v: str) -> str:
        allowed = {"local", "openai", "default"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(f"llm_provider must be one of {allowed}, got '{v}'")
        return lower

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _validate_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed]
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v]
        return v



# Module-level singleton — import this everywhere rather than constructing Settings() repeatedly.
settings = Settings()
