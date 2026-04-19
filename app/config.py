from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    DATABASE_URL: str
    ANTHROPIC_API_KEY: str
    OPENAI_API_KEY: str
    GOOGLE_API_KEY: str
    XAI_API_KEY: str
    JWT_SECRET: str
    RESEND_API_KEY: str = ""
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "development"
    SENTRY_RELEASE: str = ""  # set by CI to commit SHA on deploy
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    SENTRY_PROFILES_SAMPLE_RATE: float = 0.1
    DEBUG_SENTRY: bool = False  # gates /debug-sentry — never set True in prod
    REDIS_URL: str = "redis://localhost:6379"


settings = Settings()
