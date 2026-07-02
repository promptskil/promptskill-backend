from uuid import UUID

from sqlalchemy import select

from app.models import EmailVerificationToken
from app.services import auth_service


async def login_verified_user(
    client, db_session, email: str, user_id, password: str = "password123"
) -> str:
    user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    result = await db_session.execute(
        select(EmailVerificationToken.token).where(
            EmailVerificationToken.user_id == user_uuid
        )
    )
    await auth_service.verify_email(result.scalar_one(), db_session)

    response = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def signup_verify_login(
    client, db_session, email: str, password: str = "password123"
) -> tuple[str, str]:
    response = await client.post(
        "/auth/signup",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    token = await login_verified_user(
        client, db_session, email, body["user_id"], password
    )
    return token, body["user_id"]
