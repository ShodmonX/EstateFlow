from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
WorkerStage = Literal["all", "listener", "pre_ai", "ai", "dedup", "notification"]
QueueBackend = Literal["redis", "rabbitmq"]
DeploymentRole = Literal[
    "full",
    "applications",
    "ingestion",
    "ingestion-listener",
    "ingestion-pre-ai",
    "ingestion-ai",
    "ingestion-dedup",
    "ingestion-bootstrap",
    "migrations",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        # Aliased fields must not also read generic environment names (DEBUG).
        populate_by_name=False,
    )

    environment: Environment = "development"
    deployment_role: DeploymentRole = "full"
    service_name: str = "estateflow-api"
    log_level: str = "INFO"
    debug: bool = Field(default=False, validation_alias="ESTATEFLOW_DEBUG")

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    worker_stage: WorkerStage = "all"
    queue_backend: QueueBackend = "redis"
    rabbitmq_url: str = "amqp://estateflow:estateflow@localhost:5672/"
    rabbitmq_exchange: str = "estateflow.events"
    rabbitmq_prefetch_count: int = Field(default=10, ge=1, le=1000)
    rabbitmq_max_retries: int = Field(default=3, ge=0, le=20)

    app_secret_key: SecretStr | None = None
    admin_api_token: SecretStr | None = None
    source_suggestion_ingress_token: SecretStr | None = None
    admin_actor_user_id: int = Field(default=1, gt=0)

    db_driver: str = "postgresql+asyncpg"
    db_sync_driver: str = "postgresql+psycopg2"
    db_host: str = "localhost"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "estateflow"
    db_user: str = "estateflow"
    db_password: SecretStr | None = None

    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_mini_app_url: str | None = None
    ops_bot_token: SecretStr | None = None
    ops_chat_id: str | None = None
    content_channel_bot_token: SecretStr | None = None
    content_channel_chat_id: str | None = None
    content_daily_post_limit: int = Field(default=2, ge=1, le=2)
    adapter_plugin_dir: str | None = None
    ingestion_sources_json: str = "[]"
    website_adapter_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_session_dir: str = "data/sessions"
    telegram_media_session_name: str = "media-acc_9889"
    telegram_album_debounce_seconds: float = Field(default=1.5, gt=0, le=30)
    pre_ai_near_text_threshold: float = Field(default=0.88, ge=0, le=1)
    pre_ai_phash_hamming_threshold: int = Field(default=8, ge=0, le=64)
    dedup_exact_threshold: int = Field(default=100, ge=1)
    dedup_high_confidence_threshold: int = Field(default=80, ge=1)
    dedup_possible_threshold: int = Field(default=60, ge=1)
    dedup_candidate_limit: int = Field(default=50, ge=1, le=500)
    dedup_source_url_weight: int = Field(default=100, ge=0)
    dedup_same_image_weight: int = Field(default=70, ge=0)
    dedup_district_weight: int = Field(default=10, ge=0)
    dedup_rooms_weight: int = Field(default=10, ge=0)
    dedup_area_weight: int = Field(default=10, ge=0)
    dedup_price_weight: int = Field(default=10, ge=0)
    dedup_floor_weight: int = Field(default=5, ge=0)
    dedup_address_weight: int = Field(default=15, ge=0)
    dedup_phone_weight: int = Field(default=15, ge=0)
    dedup_area_tolerance_sqm: float = Field(default=3.0, ge=0)
    dedup_price_tolerance_ratio: float = Field(default=0.05, ge=0, le=1)
    dedup_address_similarity_threshold: float = Field(default=0.75, ge=0, le=1)
    dedup_description_similarity_threshold: float = Field(default=0.82, ge=0, le=1)
    dedup_config_version: str = "estateflow.dedup.v2"
    dedup_parent_selection_policy: Literal["completeness_trust_first_seen"] = (
        "completeness_trust_first_seen"
    )

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model_sequence: str = (
        "google/gemini-3.1-flash-lite,google/gemini-3.1-flash-lite-preview"
    )
    openrouter_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ai_min_confidence: float = Field(default=0.72, ge=0, le=1)
    rental_min_confidence: float = Field(default=0.60, ge=0, le=1)
    ai_prompt_version: str = "estateflow.listing.v1"
    default_telegram_listener_account_key: str = "acc_9889"

    r2_endpoint_url: str | None = None
    r2_region: str = "auto"
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str | None = None
    r2_public_base_url: str | None = None
    media_max_input_bytes: int = Field(default=10_000_000, ge=10_000)
    media_max_bytes: int = Field(default=600_000, ge=10_000)
    media_target_max_width: int = Field(default=800, ge=1)
    media_target_max_height: int = Field(default=600, ge=1)
    media_jpeg_quality: int = Field(default=82, ge=1, le=100)
    media_phash_hamming_threshold: int = Field(default=8, ge=0, le=64)

    feature_bot_access_enabled: bool = False
    feature_listener_sources_enabled: bool = False
    feature_ai_processing_enabled: bool = False
    feature_notifications_enabled: bool = False
    feature_nlp_enabled: bool = False
    feature_content_publishing_enabled: bool = False
    feature_ai_vision_enabled: bool = True
    beta_allowlist_user_ids: str = ""

    @property
    def database_url(self) -> str:
        return self._build_database_url(self.db_driver)

    @property
    def postgres_url(self) -> str:
        return self._build_database_url(self.db_sync_driver)

    @property
    def sync_database_url(self) -> str:
        return self._build_database_url(self.db_sync_driver)

    def _build_database_url(self, driver: str) -> str:
        password = self.db_password.get_secret_value() if self.db_password else ""
        credentials = self.db_user if not password else f"{self.db_user}:{password}"
        return f"{driver}://{credentials}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def redis_url(self) -> str:
        password = self.redis_password.get_secret_value() if self.redis_password else ""
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def openrouter_models(self) -> tuple[str, ...]:
        return tuple(
            model.strip() for model in self.openrouter_model_sequence.split(",") if model.strip()
        )

    @property
    def beta_allowlist_ids(self) -> frozenset[int]:
        values: set[int] = set()
        for item in self.beta_allowlist_user_ids.split(","):
            stripped = item.strip()
            if not stripped:
                continue
            values.add(int(stripped))
        return frozenset(values)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment != "production":
            return self
        required_secrets = {"DB_PASSWORD": self.db_password}
        queue_roles = {
            "full",
            "applications",
            "ingestion",
            "ingestion-listener",
            "ingestion-pre-ai",
            "ingestion-ai",
            "ingestion-dedup",
        }
        if self.deployment_role in queue_roles:
            required_secrets["REDIS_PASSWORD"] = self.redis_password
        if self.deployment_role in {"full", "applications"}:
            required_secrets.update(
                {
                    "APP_SECRET_KEY": self.app_secret_key,
                    "ADMIN_API_TOKEN": self.admin_api_token,
                    "SOURCE_SUGGESTION_INGRESS_TOKEN": self.source_suggestion_ingress_token,
                    "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
                }
            )
        if self.deployment_role in {"full", "ingestion", "ingestion-listener", "ingestion-ai"}:
            required_secrets.update(
                {
                    "TELEGRAM_API_HASH": self.telegram_api_hash,
                }
            )
        if self.deployment_role in {"full", "ingestion", "ingestion-ai"}:
            required_secrets.update(
                {
                    "OPENROUTER_API_KEY": self.openrouter_api_key,
                    "R2_ACCESS_KEY_ID": self.r2_access_key_id,
                    "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
                }
            )
        missing = [
            name
            for name, value in required_secrets.items()
            if value is None or not value.get_secret_value().strip()
        ]
        if missing:
            raise ValueError(f"Production secrets are required: {', '.join(missing)}.")
        if self.deployment_role in {"full", "ingestion", "ingestion-listener", "ingestion-ai"}:
            required_values: dict[str, object | None] = {
                "TELEGRAM_API_ID": self.telegram_api_id
            }
            if self.deployment_role in {"full", "ingestion", "ingestion-ai"}:
                required_values.update(
                    {
                        "R2_ENDPOINT_URL": self.r2_endpoint_url,
                        "R2_BUCKET": self.r2_bucket,
                    }
                )
            missing_values = [name for name, value in required_values.items() if not value]
            if missing_values:
                raise ValueError(
                    f"Production ingestion values are required: {', '.join(missing_values)}."
                )
        if self.debug:
            raise ValueError("ESTATEFLOW_DEBUG must be false in production.")
        if self.deployment_role in queue_roles and self.queue_backend != "rabbitmq":
            raise ValueError("QUEUE_BACKEND must be rabbitmq in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
