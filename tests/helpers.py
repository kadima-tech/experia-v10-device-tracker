"""Router stand-ins for tests that cannot reach a real Experia Box."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import requests

HOST = "192.168.2.254"
USERNAME = "Admin"
PASSWORD = "secret"
SAMSUNG_MAC = "40:5a:a4:6b:33:4b"
LAPTOP_MAC = "aa:bb:cc:dd:ee:ff"

Handler = Callable[[str, str, dict[str, str] | None], "FakeResponse"]


class FakeResponse:
    """Minimal response object used by the router client."""

    def __init__(self, status_code: int, text: str) -> None:
        """Store the status and body."""
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Session that records calls and delegates to a handler."""

    def __init__(self, handler: Handler) -> None:
        """Initialize the session."""
        self.handler = handler
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def __enter__(self) -> FakeSession:
        """Enter the context manager."""
        return self

    def __exit__(self, *args: object) -> bool:
        """Leave the context manager."""
        return False

    def get(self, url: str, timeout: int | None = None) -> FakeResponse:
        """Record a GET and return the scripted response."""
        self.calls.append(("GET", url, None))
        return self.handler("GET", url, None)

    def post(
        self, url: str, data: dict[str, str] | None = None, timeout: int | None = None
    ) -> FakeResponse:
        """Record a POST and return the scripted response."""
        self.calls.append(("POST", url, data))
        return self.handler("POST", url, data)


def install_router(
    monkeypatch: pytest.MonkeyPatch, handler: Handler
) -> list[FakeSession]:
    """Replace ``requests.Session`` with a scripted session."""
    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        session = FakeSession(handler)
        sessions.append(session)
        return session

    monkeypatch.setattr(
        "custom_components.experiaboxv10.router.requests.Session",
        factory,
    )
    return sessions


def token_firmware(xml: str, token_page: str = "token=1234;") -> Handler:
    """Firmware that publishes a login token."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        if "logintoken" in url:
            return FakeResponse(200, token_page)
        if "AssociateDevs" in url:
            return FakeResponse(200, xml)
        return FakeResponse(200, "ok")

    return handler


def legacy_firmware(xml: str, token_page: str = "") -> Handler:
    """Firmware V1.01.01T01.9: the token URL does not exist."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        if "logintoken" in url:
            return FakeResponse(404, token_page)
        if "AssociateDevs" in url:
            return FakeResponse(200, xml)
        return FakeResponse(200, "ok")

    return handler


def connection_error() -> Handler:
    """Handler that fails before any page is returned."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        raise requests.ConnectionError("no route to host")

    return handler


def login_page(xml: str = "") -> Handler:
    """Token page succeeds, but the device page is the login form."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        if "logintoken" in url:
            return FakeResponse(200, "token=1234;")
        if "AssociateDevs" in url:
            return FakeResponse(200, "<html><form>login password</form></html>")
        return FakeResponse(200, "ok")

    return handler


def login_post(session: FakeSession) -> dict[str, str]:
    """Return the login form body from a session."""
    for method, _url, data in session.calls:
        if method == "POST" and data and data.get("action") == "login":
            return data
    raise AssertionError(f"No login POST in {session.calls}")
