"""HTTP client for the ZTE H369A (KPN Experia Box V10).

The box has two known login styles. Issue #14 and pull request #15 cover both:

* Current firmware returns a page from ``logintoken_lua.lua``. The password is
  sent as ``sha256(password + first integer in that page)``.
* Firmware V1.01.01T01.9 returns HTTP 404 for that URL. The password is sent
  in plaintext with an empty ``Frm_Logintoken``.

The first integer is what the previous integration used. A page such as
``build 9 token 1234`` therefore hashes with ``9``, not ``1234``.

Newer firmware replaces those pages with a Flutter "KPN WebUI" and a
SoftAtHome JSON API. Every call is a POST to ``WS_PATH`` with a JSON body that
names a ``service`` and ``method``:

* ``sah.Device.Information.createContext`` with ``Authorization: X-Sah-Login``
  logs in with the plaintext password and returns ``data.contextID`` plus a
  session cookie.
* Later calls send ``Authorization: X-Sah <contextID>``, ``X-Context`` and the
  cookie. ``Devices.get`` with one expression for active Wi-Fi devices and one
  for active Ethernet devices lists the clients.
* ``sah.Device.Information.releaseContext`` with
  ``Authorization: X-Sah-Logout <contextID>`` ends the session.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import requests

from .const import (
    DEVICES_PATH,
    REQUEST_TIMEOUT,
    TOKEN_PATH,
    WS_APPLICATION_NAME,
    WS_PATH,
    WS_USERNAME,
)

_LOGGER = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")

# Strings in the Flutter shell served at ``/`` by the KPN WebUI firmware.
_WEBUI_MARKERS = ("kpn webui", "main.dart.js", "flutter")
# The web app sends ``+js`` on login and ``+json`` on every other call.
_WS_LOGIN_CONTENT_TYPE = "application/x-sah-ws-4-call+js"
_WS_CONTENT_TYPE = "application/x-sah-ws-4-call+json"
# Same expressions as the "Lokaal netwerk" screen of the web app.
_WS_ACTIVE_DEVICES = {
    "wifi": "not interface and not self and wifi and .Active==true",
    "ethernet": "not interface and not self and eth and .Active==true",
}


class RouterError(Exception):
    """Base error for the Experia Box client."""


class CannotConnect(RouterError):
    """The router could not be reached."""


class InvalidAuth(RouterError):
    """The router rejected the login."""


class UnexpectedResponse(RouterError):
    """The router returned something this integration does not understand."""


class _NoLegacyLogin(RouterError):
    """Both legacy login URLs returned HTTP 404."""


@dataclass(frozen=True, slots=True)
class _WsContext:
    """Session returned by ``createContext``. Kept out of ``repr`` and logs."""

    context_id: str = field(repr=False)
    cookie: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Client:
    """A client the router currently reports as associated."""

    mac: str
    name: str
    ip: str


def normalize_host(host: str) -> str:
    """Return a host without a scheme or path."""
    value = host.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.split("/", 1)[0].strip()


def normalize_mac(mac: str) -> str:
    """Return a lowercase colon-separated MAC, or raise ValueError."""
    compact = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(compact) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    formatted = ":".join(compact[index : index + 2] for index in range(0, 12, 2))
    formatted = formatted.lower()
    if not _MAC_RE.fullmatch(formatted):
        raise ValueError(f"Invalid MAC address: {mac}")
    return formatted


def fetch_clients(host: str, username: str, password: str) -> list[Client]:
    """Log in, read the connected clients, and log out.

    The KPN WebUI firmware is detected from the page at ``/``. Anything else
    uses the legacy login, unless that firmware turns out to have no legacy
    login page at all.

    This performs blocking HTTP and must run outside the event loop.
    """
    normalized = normalize_host(host)
    if not normalized:
        raise CannotConnect("Host is empty")

    base = f"http://{normalized}"
    try:
        with requests.Session() as session:
            home = _get(session, f"{base}/")
            if _is_kpn_webui(home):
                return _fetch_ws_clients(session, base, username, password)
            try:
                return _fetch_legacy_clients(session, base, username, password)
            except _NoLegacyLogin:
                _LOGGER.debug("No legacy login page, trying the KPN WebUI API")
            return _fetch_ws_clients(session, base, username, password)
    except requests.Timeout as err:
        raise CannotConnect("Timed out talking to the router") from err
    except requests.ConnectionError as err:
        raise CannotConnect("Could not connect to the router") from err
    except requests.RequestException as err:
        raise CannotConnect("Router request failed") from err


def _is_kpn_webui(response: requests.Response) -> bool:
    """Return True when ``/`` serves the Flutter KPN WebUI."""
    if response.status_code != 200:
        return False
    lowered = response.text.lower()
    return any(marker in lowered for marker in _WEBUI_MARKERS)


def _fetch_legacy_clients(
    session: requests.Session, base: str, username: str, password: str
) -> list[Client]:
    """Read clients through the ``logintoken_lua.lua`` era pages."""
    root = f"{base}/"
    try:
        token = _get(session, f"{base}{TOKEN_PATH}")
        login = _post(session, root, _login_payload(username, password, token))
        if token.status_code == 404 and login.status_code == 404:
            raise _NoLegacyLogin("Legacy login pages are missing")
        stamp = round(datetime.now(UTC).timestamp() * 1000)
        data_url = f"{base}{DEVICES_PATH}?AccessMode=WLAN&_={stamp}"
        data_page = _get(session, data_url)
        return parse_clients(data_page.text)
    finally:
        _logout(session, root)


def parse_clients(payload: str) -> list[Client]:
    """Parse the ``OBJ_ACCESSDEV_ID`` list from a router response."""
    stripped = payload.strip()
    if not stripped:
        raise UnexpectedResponse("Router returned an empty device list")

    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as err:
        lowered = stripped.lower()
        if "login" in lowered and "password" in lowered:
            raise InvalidAuth("Router returned the login page") from err
        raise UnexpectedResponse("Router did not return device XML") from err

    device_list = root.find("OBJ_ACCESSDEV_ID")
    if device_list is None:
        error_text = (root.findtext("IF_ERRORSTR") or "").lower()
        body_text = " ".join(root.itertext()).lower()
        # A rejected login is often a well-formed HTML page, which still parses.
        if (
            root.tag.lower() in {"html", "form"}
            or ("login" in body_text and "password" in body_text)
            or any(token in error_text for token in ("login", "auth", "session"))
        ):
            raise InvalidAuth(error_text or "Router returned the login page")
        raise UnexpectedResponse("Device list missing from router response")

    clients: list[Client] = []
    for device in device_list:
        values: dict[str, str] = {}
        names = device.findall("ParaName")
        raw_values = device.findall("ParaValue")
        for key, raw_value in zip(names, raw_values, strict=False):
            if key.text in {"HostName", "MACAddress", "IPAddress"}:
                values[key.text] = raw_value.text or ""
        raw_mac = values.get("MACAddress", "")
        if not raw_mac:
            continue
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            _LOGGER.debug("Skipping client with unusable MAC %r", raw_mac)
            continue
        clients.append(
            Client(
                mac=mac, name=values.get("HostName", ""), ip=values.get("IPAddress", "")
            )
        )
    return clients


def parse_ws_clients(payload: Any) -> list[Client]:
    """Parse a ``Devices.get`` response from the KPN WebUI API.

    The body is ``{"status": {"wifi": [...], "ethernet": [...]}}`` for the
    expressions in ``_WS_ACTIVE_DEVICES``. A plain list under ``status`` is
    accepted as well. Duplicate MACs are reported once.
    """
    if not isinstance(payload, dict):
        raise UnexpectedResponse("Device list is not a JSON object")
    _raise_for_ws_errors(payload)
    status = payload.get("status")
    if isinstance(status, dict):
        devices = [
            device
            for group in status.values()
            if isinstance(group, list)
            for device in group
        ]
    elif isinstance(status, list):
        devices = status
    else:
        raise UnexpectedResponse("Device list missing from router response")

    clients: dict[str, Client] = {}
    for device in devices:
        if not isinstance(device, dict) or device.get("Active") is False:
            continue
        # The web app hides entries without an upstream link ("ghost" devices).
        links = device.get("ULinks")
        if isinstance(links, list) and not links:
            continue
        raw_mac = str(device.get("PhysAddress") or device.get("Key") or "")
        try:
            mac = normalize_mac(raw_mac)
        except ValueError:
            _LOGGER.debug("Skipping device with unusable MAC %r", raw_mac)
            continue
        if mac in clients:
            continue
        clients[mac] = Client(
            mac=mac,
            name=str(device.get("Name") or ""),
            ip=str(device.get("IPAddress") or ""),
        )
    return list(clients.values())


def _fetch_ws_clients(
    session: requests.Session, base: str, username: str, password: str
) -> list[Client]:
    """Read clients through the KPN WebUI JSON API."""
    url = f"{base}{WS_PATH}"
    context = _ws_login(session, url, username, password)
    try:
        response = _ws_call(
            session,
            url,
            _ws_headers(context, "X-Sah"),
            {
                "service": "Devices",
                "method": "get",
                "parameters": {
                    "expression": _WS_ACTIVE_DEVICES,
                    "flags": "full_links",
                },
            },
        )
        return parse_ws_clients(response)
    finally:
        _ws_logout(session, url, context)


def _ws_login(
    session: requests.Session, url: str, username: str, password: str
) -> _WsContext:
    """Create a session context. A rejected password raises InvalidAuth."""
    # The web app always logs in as "admin". Accept the legacy "Admin" default.
    name = username.strip()
    if name.lower() == WS_USERNAME:
        name = WS_USERNAME
    response = session.post(
        url,
        json={
            "service": "sah.Device.Information",
            "method": "createContext",
            "parameters": {
                "applicationName": WS_APPLICATION_NAME,
                "username": name,
                "password": password,
            },
        },
        headers={
            "Content-Type": _WS_LOGIN_CONTENT_TYPE,
            "Authorization": "X-Sah-Login",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code in (401, 403):
        raise InvalidAuth("Router rejected the username or password")
    body = _ws_json(response)
    data = body.get("data")
    context_id = data.get("contextID") if isinstance(data, dict) else None
    if not isinstance(context_id, str) or not context_id:
        if body.get("errors") or body.get("status") not in (0, None):
            raise InvalidAuth("Router rejected the username or password")
        raise UnexpectedResponse("Login response has no session context")
    # The web app forwards the first ``name=value`` pair of Set-Cookie.
    cookie = (response.headers.get("set-cookie") or "").split(";", 1)[0].strip()
    return _WsContext(context_id, cookie)


def _ws_headers(context: _WsContext, scheme: str) -> dict[str, str]:
    headers = {
        "Content-Type": _WS_CONTENT_TYPE,
        "Authorization": f"{scheme} {context.context_id}",
        "X-Context": context.context_id,
    }
    if context.cookie:
        headers["Cookie"] = context.cookie
    return headers


def _ws_call(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
    if response.status_code in (401, 403):
        raise InvalidAuth("Router rejected the session")
    return _ws_json(response)


def _ws_json(response: requests.Response) -> dict[str, Any]:
    """Return the JSON object of a ``/ws`` response, or raise."""
    if response.status_code >= 500:
        raise CannotConnect(f"Router API returned HTTP {response.status_code}")
    if response.status_code != 200:
        raise UnexpectedResponse(f"Router API returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as err:
        raise UnexpectedResponse("Router API did not return JSON") from err
    if not isinstance(body, dict):
        raise UnexpectedResponse("Router API did not return a JSON object")
    return body


def _raise_for_ws_errors(body: dict[str, Any]) -> None:
    """Map a SoftAtHome ``errors`` list onto this module's exceptions."""
    errors = body.get("errors")
    if not errors:
        return
    first = errors[0] if isinstance(errors, list) and errors else {}
    code = first.get("error") if isinstance(first, dict) else None
    description = str(first.get("description", "")) if isinstance(first, dict) else ""
    # 13 is "Permission denied": the context expired or was never valid.
    if code == 13 or "permission" in description.lower():
        raise InvalidAuth("Router rejected the session")
    raise UnexpectedResponse(f"Router API error {code}: {description}".strip())


def _ws_logout(session: requests.Session, url: str, context: _WsContext) -> None:
    """Best-effort ``releaseContext``. A failure must not hide the result."""
    try:
        session.post(
            url,
            json={
                "service": "sah.Device.Information",
                "method": "releaseContext",
                "parameters": {"applicationName": WS_APPLICATION_NAME},
            },
            headers=_ws_headers(context, "X-Sah-Logout"),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        _LOGGER.debug("Releasing the router session failed", exc_info=True)


def _login_payload(
    username: str, password: str, token_response: requests.Response
) -> dict[str, str]:
    """Build the login form for the firmware that answered the token URL."""
    payload = {"Username": username, "Password": password, "action": "login"}
    if token_response.status_code == 404:
        # V1.01.01T01.9 and similar: the token URL does not exist.
        payload["Frm_Logintoken"] = ""
        return payload
    if token_response.status_code >= 500:
        raise CannotConnect(f"Token page returned HTTP {token_response.status_code}")
    if token_response.status_code != 200:
        raise UnexpectedResponse(
            f"Token page returned HTTP {token_response.status_code}"
        )
    match = re.search(r"\d+", token_response.text)
    if not match:
        raise UnexpectedResponse("Login page did not contain a token")
    digest = sha256((password + match.group(0)).encode()).hexdigest()
    payload["Password"] = digest
    return payload


def _get(session: requests.Session, url: str) -> requests.Response:
    return session.get(url, timeout=REQUEST_TIMEOUT)


def _post(
    session: requests.Session, url: str, data: dict[str, str]
) -> requests.Response:
    return session.post(url, data=data, timeout=REQUEST_TIMEOUT)


def _logout(session: requests.Session, url: str) -> None:
    """Best-effort logout. A failed logout must not hide the fetch result."""
    try:
        session.post(
            url,
            data={"IF_LogOff": "1", "IF_LanguageSwitch": "", "IF_ModeSwitch": ""},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        _LOGGER.debug("Logout from %s failed", url, exc_info=True)
