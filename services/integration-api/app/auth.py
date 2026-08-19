import jwt
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from shared.types import UserContext

_bearer = HTTPBearer(auto_error=False)


class UnauthorizedError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


async def unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": {"code": "unauthorized", "message": exc.message}},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserContext:
    if credentials is None:
        raise UnauthorizedError("missing bearer token")
    try:
        claims = jwt.decode(
            credentials.credentials,
            request.app.state.settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        raise UnauthorizedError("invalid or expired token")
    return UserContext(
        sub=claims["sub"],
        name=claims.get("name", ""),
        component=claims.get("component", ""),
        roles=tuple(claims.get("roles", ())),
        token=credentials.credentials,
    )
