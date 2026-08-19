"""Independent JWT validation (invariants 5/6).

The agent forwards the ORIGINAL delegated JWT as the Authorization header;
this server validates it itself (same JWT_SECRET in demo; Keycloak JWKS in
prod) and binds the claims — identity is never accepted as plain fields
relayed in the request body. Mirrors integration-api/app/auth.py; local copy
because services never share code across container boundaries.
"""
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class UserContext:
    """Claims from the validated delegated JWT. Deliberately excludes the
    raw token: nothing downstream of this server needs it (WDP calls use
    the server's own WDP_AUTH_TOKEN), so it can never leak into logs."""

    sub: str
    name: str
    component: str
    roles: tuple[str, ...]


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
    )
