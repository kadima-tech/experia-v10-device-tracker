"""Config flow tests. The router is the scripted session from tests.helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.helpers import (
    HOST,
    PASSWORD,
    USERNAME,
    connection_error,
    install_router,
    login_page,
    token_firmware,
)

from custom_components.experiaboxv10.const import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("devices_xml")


async def _start_user(hass, devices_xml: str, monkeypatch: pytest.MonkeyPatch):
    install_router(monkeypatch, token_firmware(devices_xml))
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow_creates_an_entry(hass, devices_xml, monkeypatch) -> None:
    """A successful login creates one entry and sets the router up."""
    result = await _start_user(hass, devices_xml, monkeypatch)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://192.168.2.254/",
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
    }
    assert result["result"].unique_id == HOST
    assert result["result"].state.name == "LOADED"


async def test_user_flow_reports_connection_and_auth_errors(
    hass, devices_xml, monkeypatch
) -> None:
    """The form stays open and names the failure."""
    install_router(monkeypatch, connection_error())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    install_router(monkeypatch, login_page())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_duplicate_host_is_aborted(hass, devices_xml, monkeypatch) -> None:
    """The same host cannot be added twice."""
    first = await _start_user(hass, devices_xml, monkeypatch)
    created = await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    await hass.async_block_till_done()
    assert created["type"] is FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    aborted = await hass.config_entries.flow.async_configure(
        second["flow_id"],
        {CONF_HOST: HOST, CONF_USERNAME: "other", CONF_PASSWORD: "other"},
    )
    assert aborted["type"] is FlowResultType.ABORT
    assert aborted["reason"] == "already_configured"


async def test_reconfigure_updates_the_host(hass, devices_xml, monkeypatch) -> None:
    """Reconfigure checks the new login and reloads the entry."""
    install_router(monkeypatch, token_firmware(devices_xml))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        data={CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.2.1",
            CONF_USERNAME: "root",
            CONF_PASSWORD: "new-secret",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.2.1"
    assert entry.data[CONF_USERNAME] == "root"
    assert entry.unique_id == "192.168.2.1"


async def test_reauth_replaces_the_password(hass, devices_xml, monkeypatch) -> None:
    """Reauth keeps the host and stores the new password."""
    install_router(monkeypatch, token_firmware(devices_xml))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        title=HOST,
        data={CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert entry.data[CONF_HOST] == HOST


async def test_options_flow_stores_polling_and_grace(
    hass, devices_xml, monkeypatch
) -> None:
    """Options reload the coordinator with the new interval."""
    install_router(monkeypatch, token_firmware(devices_xml))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        data={CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 20, CONF_CONSIDER_HOME: 60},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 20
    assert entry.options[CONF_CONSIDER_HOME] == 60
    assert entry.runtime_data.update_interval == timedelta(seconds=20)
