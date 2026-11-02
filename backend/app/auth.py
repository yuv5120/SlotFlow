import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, Response

from .config import settings

COOKIE = "slotflow_staff"


def authenticate(password: str, response: Response) -> None:
    if not settings.admin_password or not settings.session_secret:
        raise HTTPException(503, "Set ADMIN_PASSWORD and SESSION_SECRET")
    if not hmac.compare_digest(password, settings.admin_password):
        raise HTTPException(401, "Incorrect password")
    expires = int((datetime.now(UTC) + timedelta(hours=12)).timestamp())
    data = f"haven-health:{expires}"
    signature = hmac.new(settings.session_secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    response.set_cookie(COOKIE, f"{expires}.{signature}", httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=12 * 3600, path="/")


def require_staff(request: Request) -> None:
    cookie = request.cookies.get(COOKIE, "")
    try:
        expiry, signature = cookie.split(".", 1)
        if int(expiry) < datetime.now(UTC).timestamp():
            raise ValueError("expired")
        data = f"haven-health:{expiry}"
        expected = hmac.new(settings.session_secret.encode(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return
    except (ValueError, AttributeError):
        pass
    raise HTTPException(401, "Staff sign-in required")
