# Code generated from api.json by bb-builder. DO NOT EDIT.
from __future__ import annotations

from fastapi import Depends, FastAPI

from handlers import auth, auth_mobile, register
from handlers.auth import Status
from handlers.auth_mobile import TokenGrant
from handlers.envelope import Envelope
from middleware.auth import require_auth
from models.user import PublicUser


def register_generated(app: FastAPI) -> None:
    """Mount every API route in api.json. main.py calls this once and is never
    hand-edited for routes."""
    app.add_api_route(
        "/api/v1/auth/login",
        auth.login,
        methods=["POST"],
        response_model=Envelope[PublicUser],
    )
    app.add_api_route(
        "/api/v1/auth/login_token",
        auth_mobile.login_token,
        methods=["POST"],
        response_model=Envelope[TokenGrant],
    )
    app.add_api_route(
        "/api/v1/auth/logout",
        auth.logout,
        methods=["POST"],
        response_model=Envelope[Status],
    )
    app.add_api_route(
        "/api/v1/auth/logout_all",
        auth.logout_all,
        methods=["POST"],
        response_model=Envelope[Status],
        dependencies=[Depends(require_auth)],
    )
    app.add_api_route(
        "/api/v1/auth/logout_token",
        auth_mobile.logout_token,
        methods=["DELETE"],
        response_model=Envelope[Status],
    )
    app.add_api_route(
        "/api/v1/auth/me",
        auth.me,
        methods=["GET"],
        response_model=Envelope[PublicUser],
        dependencies=[Depends(require_auth)],
    )
    app.add_api_route(
        "/api/v1/auth/me_token",
        auth_mobile.me_token,
        methods=["GET"],
        response_model=Envelope[PublicUser],
    )
    app.add_api_route(
        "/api/v1/auth/register",
        register.register,
        methods=["POST"],
        response_model=Envelope[PublicUser],
    )
