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
    APP_BASE_URL: str = ""  # e.g. https://your-app.railway.app — set in Railway env
    WEB_BASE_URL: str = "https://www.vaineai.com"  # frontend domain for email links
    APPLE_SHARED_SECRET: str = ""  # App-specific shared secret from App Store Connect
    # Apple notification verification (SignedDataVerifier):
    APPLE_BUNDLE_ID: str = ""  # e.g. com.airpromptskill.app
    APPLE_APP_APPLE_ID: int = 0  # numeric App Store id (Production verifier)
    STRIPE_SECRET_KEY: str = ""  # sk_... — set in Railway env, never in code
    STRIPE_WEBHOOK_SECRET: str = ""  # whsec_... — webhook signing secret
    STRIPE_PRICE_ID: str = ""  # price_... — individual plan
    STRIPE_TRIAL_DAYS: int = 7  # free-trial length (Decision 6a)
    PAYWALL_ENABLED: bool = False  # gate /generate on individual subscription

    # --- Vaine engine (Phase 2.1); flags default-safe ---
    TOGETHER_API_KEY: str = ""  # Together AI; set in .env / Railway
    VAINE_BASE_URL: str = "https://api.together.xyz/v1"  # serving endpoint
    VAINE_MODEL: str = ""  # fine-tune id; set after Phase 4.2
    VAINE_ENABLED: bool = False  # master flag, default OFF (D-3)
    VAINE_LEARNER_ENABLED: bool = False  # flywheel (Phase 8); off
    MODAL_KEY: str = ""  # Modal proxy-auth token id (wk-...); set in .env / Railway
    MODAL_SECRET: str = ""  # Modal proxy-auth token secret (ws-...); .env / Railway


settings = Settings()
