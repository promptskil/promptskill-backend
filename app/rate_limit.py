from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def get_user_id_or_ip(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return str(user_id)
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_user_id_or_ip,
    headers_enabled=True,
    storage_uri=settings.REDIS_URL,
)
