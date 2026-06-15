"""Stripe SDK config — sets the API key from settings.

Centralized so billing + webhook services import a single configured
module. The key comes from the environment (settings); never hardcoded.
"""
import stripe

from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

__all__ = ["stripe"]
