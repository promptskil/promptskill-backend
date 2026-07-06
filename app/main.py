from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.exceptions import validation_exception_handler
from app.rate_limit import limiter
from app.routers import auth as auth_router
from app.routers import billing as billing_router
from app.routers import business as business_router
from app.routers import generate as generate_router
from app.routers import history as history_router
from app.routers import invite as invite_router
from app.routers import stripe_webhook as stripe_webhook_router
from app.routers import user as user_router
from app.routers import webhooks as webhooks_router
from app.services.generate_service import load_model_registry, load_vaine_data

# Optional Sentry — guarded against placeholder DSN.
# Integration imports are best-effort: if extras aren't installed or
# the SDK version lacks them, fall back to bare init so boot never
# crashes on observability wiring (per Phase 9 — Step 9.2 risk note).
if settings.SENTRY_DSN and settings.SENTRY_DSN.startswith("https://"):
    import sentry_sdk

    _integrations = []
    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        _integrations.append(FastApiIntegration())
    except Exception:  # noqa: BLE001 — never crash boot on obs wiring
        pass
    try:
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        _integrations.append(SqlalchemyIntegration())
    except Exception:  # noqa: BLE001
        pass

    _init_kwargs = {
        "dsn": settings.SENTRY_DSN,
        "environment": settings.SENTRY_ENVIRONMENT,
        "traces_sample_rate": settings.SENTRY_TRACES_SAMPLE_RATE,
        "profiles_sample_rate": settings.SENTRY_PROFILES_SAMPLE_RATE,
        "integrations": _integrations,
        "send_default_pii": False,
    }
    if settings.SENTRY_RELEASE:
        _init_kwargs["release"] = settings.SENTRY_RELEASE

    sentry_sdk.init(**_init_kwargs)

app = FastAPI(title="PromptSkill API", version="0.1.0")

# CORS — locked down Phase 16
_ALLOWED_ORIGINS = [
    "chrome-extension://kgjcnldjmhbploedmijadigchnociecg",  # Vaine extension
    "https://www.vaineai.com",                               # Web app (www)
    "https://vaineai.com",                                   # Web app (apex)
    "https://business.vaineai.com",                          # Business subdomain
    "http://localhost:5173",                                 # Local dev — Vite
    "http://localhost:3000",                                 # Local dev — alt
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter (Option A — decorator-based, no SlowAPIMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 422 → 400 handler
app.add_exception_handler(RequestValidationError, validation_exception_handler)


# Startup — populate MODEL_REGISTRY before first request (Phase 5 — Step 5.3)
@app.on_event("startup")
async def _load_registry_on_startup() -> None:
    load_model_registry()
    load_vaine_data()


# Routers
app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(generate_router.router, tags=["generate"])
app.include_router(history_router.router, tags=["history"])
app.include_router(user_router.router, tags=["user"])

# Billing / Stripe subscriptions (individual web)
app.include_router(billing_router.router, prefix="/billing", tags=["billing"])

# Stripe webhook (carries its own /webhooks prefix; separate from Apple webhook)
app.include_router(stripe_webhook_router.router)

# Apple webhook (App Store Server Notifications v2; carries its own /webhooks prefix)
app.include_router(webhooks_router.router)

# Business / organization layer
app.include_router(
    business_router.router,
    prefix="/business",
    tags=["business"],
)

# Invite acceptance (top-level, no prefix — matches email URL path)
app.include_router(invite_router.router, tags=["invite"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# ─────────────────────── /debug-sentry — flag-gated verification ────────
#
# One-shot endpoint for confirming the Sentry DSN + integrations actually
# route events. Gate: settings.DEBUG_SENTRY (default False). Register the
# route only when the flag is on — absent entirely in prod. Phase 9
# Step 9.2 ops verification uses this; production Railway env MUST NOT
# set DEBUG_SENTRY=true.
if settings.DEBUG_SENTRY:
    @app.get("/debug-sentry")
    async def _debug_sentry():
        raise RuntimeError("sentry_debug_probe")
