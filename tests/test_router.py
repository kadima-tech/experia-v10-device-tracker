"""Tests for the router client. No traffic leaves the process."""

from __future__ import annotations

import json
from hashlib import sha256

import pytest
from tests.helpers import (
    HOST,
    LAPTOP_MAC,
    PASSWORD,
    SAMSUNG_MAC,
    USERNAME,
    WS_CONTEXT_ID,
    WS_COOKIE,
    FakeResponse,
    FakeSession,
    connection_error,
    install_router,
    legacy_firmware,
    login_post,
    token_firmware,
    unknown_home,
    webui_home,
    ws_router,
)

from custom_components.experiaboxv10.router import (
    CannotConnect,
    InvalidAuth,
    UnexpectedResponse,
    fetch_clients,
    normalize_host,
    normalize_mac,
    parse_clients,
    parse_ws_clients,
)


def test_normalize_host_strips_scheme_and_path() -> None:
    """Users sometimes paste the address from the browser."""
    assert normalize_host(" http://192.168.2.254/login ") == "192.168.2.254"
    assert normalize_host("192.168.2.254") == "192.168.2.254"


def test_normalize_mac_accepts_the_formats_the_router_uses() -> None:
    """The box has returned both colon and dash separators."""
    assert normalize_mac("40:5A:A4:6B:33:4B") == "40:5a:a4:6b:33:4b"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_garbage() -> None:
    """A short value must not be turned into a tracker."""
    with pytest.raises(ValueError):
        normalize_mac("not-a-mac")


def test_parse_clients_reads_the_sample_and_skips_a_missing_mac(
    devices_xml: str,
) -> None:
    """The sample response is the one the old tests used, plus a second client."""
    clients = parse_clients(devices_xml)

    assert [(client.mac, client.name, client.ip) for client in clients] == [
        (SAMSUNG_MAC, "Samsung", "192.168.2.16"),
        ("aa:bb:cc:dd:ee:ff", "", "192.168.2.20"),
    ]


def test_parse_clients_treats_a_login_form_as_rejected_credentials() -> None:
    """A wrong password comes back as the HTML login page."""
    with pytest.raises(InvalidAuth):
        parse_clients("<html><form>login password</form></html>")


def test_parse_clients_rejects_a_non_xml_body() -> None:
    """Anything else is an unknown firmware response."""
    with pytest.raises(UnexpectedResponse):
        parse_clients("not xml and not a login form")


def test_parse_clients_rejects_xml_without_the_device_list() -> None:
    """A successful page always contains OBJ_ACCESSDEV_ID."""
    with pytest.raises(UnexpectedResponse):
        parse_clients(
            "<ajax_response_xml_root><IF_ERRORSTR>SUCC</IF_ERRORSTR></ajax_response_xml_root>"
        )


def test_fetch_uses_the_first_integer_as_the_token(
    monkeypatch: pytest.MonkeyPatch, devices_xml: str
) -> None:
    """Current firmware hashes password plus the first integer on the token page."""
    sessions = install_router(
        monkeypatch, token_firmware(devices_xml, token_page="build 9 token 1234")
    )

    clients = fetch_clients(HOST, USERNAME, PASSWORD)

    assert [client.mac for client in clients] == [SAMSUNG_MAC, "aa:bb:cc:dd:ee:ff"]
    posted = login_post(sessions[0])
    assert posted["Username"] == USERNAME
    assert posted["Password"] == sha256(b"secret9").hexdigest()
    assert posted["action"] == "login"
    assert any(
        method == "GET" and "AccessMode=WLAN" in url
        for method, url, _data in sessions[0].calls
    )
    assert any(
        method == "POST" and data and data.get("IF_LogOff") == "1"
        for method, _url, data in sessions[0].calls
    )


def test_legacy_firmware_sends_the_plaintext_password_when_the_token_page_is_missing(
    monkeypatch: pytest.MonkeyPatch, devices_xml: str
) -> None:
    """V1.01.01T01.9 returns 404. Digits in that body must not become a token."""
    sessions = install_router(
        monkeypatch, legacy_firmware(devices_xml, token_page="Error 404")
    )

    fetch_clients(HOST, USERNAME, PASSWORD)

    posted = login_post(sessions[0])
    assert posted["Password"] == PASSWORD
    assert posted["Frm_Logintoken"] == ""


def test_legacy_firmware_without_digits_in_the_404_body(
    monkeypatch: pytest.MonkeyPatch, devices_xml: str
) -> None:
    """The old client hashed the body before checking for 404 and crashed here."""
    sessions = install_router(
        monkeypatch, legacy_firmware(devices_xml, token_page="missing")
    )

    fetch_clients("http://192.168.2.254/ui", USERNAME, PASSWORD)

    posted = login_post(sessions[0])
    assert posted["Password"] == PASSWORD
    assert any(
        url.startswith("http://192.168.2.254/")
        for _method, url, _data in sessions[0].calls
    )


def test_connection_error_is_reported_without_a_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A down router is a connection failure, not an empty home."""
    install_router(monkeypatch, connection_error())

    with pytest.raises(CannotConnect):
        fetch_clients(HOST, USERNAME, PASSWORD)


# KPN WebUI firmware


def _ws_methods(session: FakeSession) -> list[str]:
    return [call.service_method for call in session.ws_calls]


def test_webui_firmware_logs_in_lists_devices_and_releases_the_context(
    monkeypatch: pytest.MonkeyPatch, ws_devices_json: str
) -> None:
    """The Flutter UI at ``/`` selects the ``/ws`` API, not the legacy pages."""
    sessions = install_router(monkeypatch, webui_home(), ws_router(ws_devices_json))

    clients = fetch_clients(HOST, USERNAME, PASSWORD)

    assert [(client.mac, client.name, client.ip) for client in clients] == [
        (SAMSUNG_MAC, "Samsung", "192.168.2.16"),
        ("c0:d7:aa:89:7a:a8", "SW2-897AA8", "192.168.2.7"),
        (LAPTOP_MAC, "Laptop", "192.168.2.20"),
        ("68:54:ed:46:58:ee", "Device", "192.168.2.201"),
        ("c4:eb:42:06:90:77", "DIW7022-69077", "192.168.2.15"),
    ]
    session = sessions[0]
    assert not any("logintoken" in url for _method, url, _data in session.calls)
    assert _ws_methods(session) == [
        "sah.Device.Information.createContext",
        "Devices.get",
        "sah.Device.Information.releaseContext",
    ]
    assert all(
        call.url == "http://192.168.2.254/ws/NeMo/Intf/lan:getMIBs"
        for call in session.ws_calls
    )

    login, devices, logout = session.ws_calls
    # The web app always logs in as lowercase "admin" with the plain password.
    assert login.body["parameters"] == {
        "applicationName": "webui",
        "username": "admin",
        "password": PASSWORD,
    }
    assert login.headers["Authorization"] == "X-Sah-Login"
    assert login.headers["Content-Type"] == "application/x-sah-ws-4-call+js"

    assert devices.headers == {
        "Content-Type": "application/x-sah-ws-4-call+json",
        "Authorization": f"X-Sah {WS_CONTEXT_ID}",
        "X-Context": WS_CONTEXT_ID,
        "Cookie": WS_COOKIE,
    }
    assert devices.body["parameters"]["flags"] == "full_links"
    assert set(devices.body["parameters"]["expression"]) == {"wifi", "ethernet"}

    assert logout.headers["Authorization"] == f"X-Sah-Logout {WS_CONTEXT_ID}"
    assert logout.headers["X-Context"] == WS_CONTEXT_ID
    assert logout.body["parameters"] == {"applicationName": "webui"}


def test_webui_firmware_rejects_a_wrong_password_as_invalid_auth(
    monkeypatch: pytest.MonkeyPatch, ws_devices_json: str
) -> None:
    """A 401 ``Permission denied`` from createContext is a bad password."""
    sessions = install_router(monkeypatch, webui_home(), ws_router(ws_devices_json))

    with pytest.raises(InvalidAuth):
        fetch_clients(HOST, USERNAME, "wrong")

    # Nothing to release: no context was created.
    assert _ws_methods(sessions[0]) == ["sah.Device.Information.createContext"]


def test_webui_firmware_login_errors_in_a_200_body_are_invalid_auth(
    monkeypatch: pytest.MonkeyPatch, ws_devices_json: str
) -> None:
    """Some SoftAtHome builds answer 200 with an ``errors`` list instead."""
    denied = FakeResponse(
        200,
        json.dumps(
            {
                "status": None,
                "errors": [{"error": 13, "description": "Permission denied"}],
            }
        ),
    )
    install_router(monkeypatch, webui_home(), ws_router(ws_devices_json, login=denied))

    with pytest.raises(InvalidAuth):
        fetch_clients(HOST, USERNAME, PASSWORD)


def test_webui_firmware_releases_the_context_when_the_device_list_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout runs in ``finally`` even when the list is unusable."""
    sessions = install_router(monkeypatch, webui_home(), ws_router("not json"))

    with pytest.raises(UnexpectedResponse):
        fetch_clients(HOST, USERNAME, PASSWORD)

    assert _ws_methods(sessions[0])[-1] == "sah.Device.Information.releaseContext"


def test_unrecognised_home_page_without_legacy_pages_tries_the_ws_api(
    monkeypatch: pytest.MonkeyPatch, ws_devices_json: str
) -> None:
    """Both legacy login URLs returning 404 means the box only has ``/ws``."""
    sessions = install_router(monkeypatch, unknown_home(), ws_router(ws_devices_json))

    clients = fetch_clients(HOST, USERNAME, PASSWORD)

    assert SAMSUNG_MAC in [client.mac for client in clients]
    assert any("logintoken" in url for _method, url, _data in sessions[0].calls)
    assert _ws_methods(sessions[0])[-1] == "sah.Device.Information.releaseContext"


def test_legacy_firmware_never_touches_the_ws_api(
    monkeypatch: pytest.MonkeyPatch, devices_xml: str
) -> None:
    """Old firmware keeps the old flow, with no extra requests."""
    sessions = install_router(monkeypatch, token_firmware(devices_xml))

    fetch_clients(HOST, USERNAME, PASSWORD)

    assert sessions[0].ws_calls == []


def test_parse_ws_clients_skips_ghosts_duplicates_and_non_mac_keys(
    ws_devices_json: str,
) -> None:
    """Entries without an upstream link or a MAC are not trackers."""
    clients = parse_ws_clients(json.loads(ws_devices_json))

    macs = [client.mac for client in clients]
    assert "38:94:ed:a6:be:6c" not in macs
    assert macs.count(LAPTOP_MAC) == 1
    assert len(macs) == 5


def test_parse_ws_clients_maps_a_permission_error_to_invalid_auth() -> None:
    """An expired context comes back as error 13."""
    with pytest.raises(InvalidAuth):
        parse_ws_clients(
            {
                "status": None,
                "errors": [{"error": 13, "description": "Permission denied"}],
            }
        )


def test_parse_ws_clients_rejects_a_body_without_status() -> None:
    """Anything else is an unknown firmware response."""
    with pytest.raises(UnexpectedResponse):
        parse_ws_clients({"result": {}})
