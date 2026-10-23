"""Tests for the router client. No traffic leaves the process."""

from __future__ import annotations

from hashlib import sha256

import pytest
from tests.helpers import (
    HOST,
    PASSWORD,
    SAMSUNG_MAC,
    USERNAME,
    connection_error,
    install_router,
    legacy_firmware,
    login_post,
    token_firmware,
)

from custom_components.experiaboxv10.router import (
    CannotConnect,
    InvalidAuth,
    UnexpectedResponse,
    fetch_clients,
    normalize_host,
    normalize_mac,
    parse_clients,
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
