"""HTTP client for the ZTE H369A (KPN Experia Box V10).

The box has two known login styles. Issue #14 and pull request #15 cover both:

* Current firmware returns a page from ``logintoken_lua.lua``. The password is
  sent as ``sha256(password + first integer in that page)``.
* Firmware V1.01.01T01.9 returns HTTP 404 for that URL. The password is sent
  in plaintext with an empty ``Frm_Logintoken``.

The first integer is what the previous integration used. A page such as
``build 9 token 1234`` therefore hashes with ``9``, not ``1234``.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import requests

from .const import DEVICES_PATH, REQUEST_TIMEOUT, TOKEN_PATH

_LOGGER = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class RouterError(Exception):
    """Base error for the Experia Box client."""


class CannotConnect(RouterError):
    """The router could not be reached."""


class InvalidAuth(RouterError):
    """The router rejected the login."""


class UnexpectedResponse(RouterError):
    """The router returned something this integration does not understand."""


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
    """Log in, read associated Wi-Fi clients, and log out.

    This performs blocking HTTP and must run outside the event loop.
    """
    normalized = normalize_host(host)
    if not normalized:
        raise CannotConnect("Host is empty")

    root = f"http://{normalized}/"
    try:
        with requests.Session() as session:
            try:
                _get(session, root)
                token = _get(session, f"http://{normalized}{TOKEN_PATH}")
                _post(session, root, _login_payload(username, password, token))
                stamp = round(datetime.now(UTC).timestamp() * 1000)
                data_url = (
                    f"http://{normalized}{DEVICES_PATH}?AccessMode=WLAN&_={stamp}"
                )
                data_page = _get(session, data_url)
                return parse_clients(data_page.text)
            finally:
                _logout(session, root)
    except requests.Timeout as err:
        raise CannotConnect("Timed out talking to the router") from err
    except requests.ConnectionError as err:
        raise CannotConnect("Could not connect to the router") from err
    except requests.RequestException as err:
        raise CannotConnect("Router request failed") from err


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
