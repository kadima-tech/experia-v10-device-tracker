"""Router stand-ins for tests that cannot reach a real Experia Box."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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

    def __init__(
        self, status_code: int, text: str, headers: dict[str, str] | None = None
    ) -> None:
        """Store the status, body, and headers."""
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        """Decode the body like ``requests`` does."""
        return json.loads(self.text)


class WsCall:
    """One JSON POST recorded by :class:`FakeSession`."""

    def __init__(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> None:
        """Store the request."""
        self.url = url
        self.body = body
        self.headers = headers

    @property
    def service_method(self) -> str:
        """Return ``service.method``, e.g. ``Devices.get``."""
        return f"{self.body.get('service')}.{self.body.get('method')}"


WsHandler = Callable[[WsCall], FakeResponse]


class FakeSession:
    """Session that records calls and delegates to a handler."""

    def __init__(self, handler: Handler, ws_handler: WsHandler | None = None) -> None:
        """Initialize the session."""
        self.handler = handler
        self.ws_handler = ws_handler
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.ws_calls: list[WsCall] = []

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
        self,
        url: str,
        data: dict[str, str] | None = None,
        timeout: int | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        """Record a POST and return the scripted response."""
        self.calls.append(("POST", url, data))
        if json is not None:
            call = WsCall(url, json, dict(headers or {}))
            self.ws_calls.append(call)
            if self.ws_handler is None:
                return FakeResponse(404, "")
            return self.ws_handler(call)
        return self.handler("POST", url, data)


def install_router(
    monkeypatch: pytest.MonkeyPatch,
    handler: Handler,
    ws_handler: WsHandler | None = None,
) -> list[FakeSession]:
    """Replace ``requests.Session`` with a scripted session."""
    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        session = FakeSession(handler, ws_handler)
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


# KPN WebUI firmware (SoftAtHome JSON API on /ws).

WS_CONTEXT_ID = "ctx-0123456789abcdef"
WS_COOKIE = "4a1b2c3d/sessid=session-cookie-value"
WEBUI_HOME = (
    "<!DOCTYPE html><html><head><title>KPN WebUI</title>"
    '<script src="flutter_bootstrap.js" async></script></head><body></body></html>'
)


def webui_home() -> Handler:
    """Plain HTTP side of the KPN WebUI firmware: only ``/`` exists."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        if method == "GET" and url.endswith("/"):
            return FakeResponse(200, WEBUI_HOME)
        return FakeResponse(404, "")

    return handler


def unknown_home() -> Handler:
    """A firmware whose ``/`` is not recognised and has no legacy pages."""

    def handler(method: str, url: str, data: dict[str, str] | None) -> FakeResponse:
        if method == "GET" and url.endswith("/"):
            return FakeResponse(200, "<html><body>Loading</body></html>")
        return FakeResponse(404, "")

    return handler


def ws_router(
    devices: str,
    password: str = "secret",
    login: FakeResponse | None = None,
) -> WsHandler:
    """``/ws`` API that accepts ``admin`` with ``password``."""

    def handler(call: WsCall) -> FakeResponse:
        if call.service_method == "sah.Device.Information.createContext":
            if login is not None:
                return login
            params = call.body["parameters"]
            if params["username"] != "admin" or params["password"] != password:
                return FakeResponse(
                    401,
                    json.dumps(
                        {
                            "status": None,
                            "errors": [
                                {
                                    "error": 13,
                                    "description": "Permission denied",
                                    "info": "",
                                }
                            ],
                        }
                    ),
                )
            return FakeResponse(
                200,
                json.dumps(
                    {
                        "status": 0,
                        "data": {
                            "contextID": WS_CONTEXT_ID,
                            "username": "admin",
                            "groups": "http,admin",
                        },
                    }
                ),
                headers={"set-cookie": f"{WS_COOKIE}; path=/; HttpOnly"},
            )
        if call.headers.get("X-Context") != WS_CONTEXT_ID:
            return FakeResponse(
                200,
                json.dumps(
                    {
                        "status": None,
                        "errors": [{"error": 13, "description": "Permission denied"}],
                    }
                ),
            )
        if call.service_method == "Devices.get":
            return FakeResponse(200, devices)
        if call.service_method == "sah.Device.Information.releaseContext":
            return FakeResponse(200, json.dumps({"status": 0}))
        return FakeResponse(200, json.dumps({"status": None, "errors": []}))

    return handler
