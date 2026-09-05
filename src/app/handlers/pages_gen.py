# Code generated from api.json by bb-builder. DO NOT EDIT.
from __future__ import annotations

from fastapi import FastAPI

from handlers.pages import page_file


def register_pages(app: FastAPI) -> None:
    """Mount every page in api.json at its human-facing URL. main.py calls this
    once and is never hand-edited for page routes."""
    app.add_api_route("/login", page_file("login"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/register", page_file("register"), methods=["GET"], include_in_schema=False)
