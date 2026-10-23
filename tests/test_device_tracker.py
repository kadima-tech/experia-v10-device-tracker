"""Device tracker tests against scripted router responses."""

from __future__ import annotations

import requests
from homeassistant.components.device_tracker import DOMAIN as DEVICE_TRACKER_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.helpers import (
    HOST,
    LAPTOP_MAC,
    PASSWORD,
    SAMSUNG_MAC,
    USERNAME,
    connection_error,
    install_router,
    token_firmware,
)

from custom_components.experiaboxv10.const import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)


async def _register_phone(hass) -> None:
    """Register the Samsung MAC so Home Assistant enables its tracker."""
    other = MockConfigEntry(domain="phone", title="Phone")
    other.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, SAMSUNG_MAC)},
        name="Phone",
    )


def _entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST,
        data={CONF_HOST: HOST, CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        options=options,
    )


async def test_known_mac_is_home_and_unknown_mac_is_disabled(
    hass, devices_xml, monkeypatch
) -> None:
    """A MAC already known to Home Assistant is tracked. Others start disabled."""
    install_router(monkeypatch, token_firmware(devices_xml))
    await _register_phone(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("device_tracker.samsung")
    assert state is not None
    assert state.state == STATE_HOME
    assert state.attributes["mac"] == SAMSUNG_MAC
    assert state.attributes["ip"] == "192.168.2.16"
    assert state.attributes["host_name"] == "Samsung"
    assert state.attributes["source_type"] == "router"

    registry = er.async_get(hass)
    laptop = registry.async_get_entity_id(
        DEVICE_TRACKER_DOMAIN, DOMAIN, f"{entry.entry_id}_{LAPTOP_MAC}"
    )
    assert laptop is not None
    laptop_entry = registry.async_get(laptop)
    assert laptop_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(laptop) is None

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.configuration_url == f"http://{HOST}/"


async def test_consider_home_keeps_a_sleeping_client_home(
    hass, devices_xml, empty_devices_xml, monkeypatch, freezer
) -> None:
    """A client that drops off the list stays home until the grace period ends."""
    pages = {"xml": devices_xml}

    def handler(method, url, data):
        if "logintoken" in url:
            from tests.helpers import FakeResponse

            return FakeResponse(200, "token=1234;")
        if "AssociateDevs" in url:
            from tests.helpers import FakeResponse

            return FakeResponse(200, pages["xml"])
        from tests.helpers import FakeResponse

        return FakeResponse(200, "ok")

    install_router(monkeypatch, handler)
    await _register_phone(hass)
    freezer.move_to("2026-10-23T12:00:00+00:00")
    entry = _entry(**{CONF_CONSIDER_HOME: 180})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.samsung").state == STATE_HOME

    pages["xml"] = empty_devices_xml
    freezer.move_to("2026-10-23T12:02:00+00:00")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.samsung").state == STATE_HOME

    freezer.move_to("2026-10-23T12:03:01+00:00")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("device_tracker.samsung").state == STATE_NOT_HOME


async def test_client_seen_after_startup_is_added(
    hass, devices_xml, empty_devices_xml, monkeypatch
) -> None:
    """A client that associates later gets an entity on the next poll."""
    pages = {"xml": empty_devices_xml}

    def handler(method, url, data):
        from tests.helpers import FakeResponse

        if "logintoken" in url:
            return FakeResponse(200, "token=1234;")
        if "AssociateDevs" in url:
            return FakeResponse(200, pages["xml"])
        return FakeResponse(200, "ok")

    install_router(monkeypatch, handler)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []

    pages["xml"] = devices_xml
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert unique_ids == {
        f"{entry.entry_id}_{SAMSUNG_MAC}",
        f"{entry.entry_id}_{LAPTOP_MAC}",
    }


async def test_unreachable_router_does_not_mark_clients_away(
    hass, devices_xml, monkeypatch
) -> None:
    """A failed poll marks the tracker unavailable instead of not_home."""
    pages = {"fail": False}

    def handler(method, url, data):
        from tests.helpers import FakeResponse

        if pages["fail"]:
            raise requests.ConnectionError("router down")
        if "logintoken" in url:
            return FakeResponse(200, "token=1234;")
        if "AssociateDevs" in url:
            return FakeResponse(200, devices_xml)
        return FakeResponse(200, "ok")

    install_router(monkeypatch, handler)
    await _register_phone(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    pages["fail"] = True
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("device_tracker.samsung")
    assert state.state == "unavailable"


async def test_unload(hass, devices_xml, monkeypatch) -> None:
    """Unloading removes the tracker state."""
    install_router(monkeypatch, token_firmware(devices_xml))
    await _register_phone(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Entities with a unique id stay in the state machine as restored until
    # the entry is set up again.
    restored = hass.states.get("device_tracker.samsung")
    assert restored is not None
    assert restored.state == STATE_UNAVAILABLE
    assert restored.attributes["restored"] is True


async def test_yaml_import_creates_an_entry_and_a_repair(
    hass, devices_xml, monkeypatch
) -> None:
    """Legacy YAML is imported and the user is told to remove it."""
    install_router(monkeypatch, token_firmware(devices_xml))

    assert await async_setup_component(
        hass,
        DEVICE_TRACKER_DOMAIN,
        {
            DEVICE_TRACKER_DOMAIN: [
                {
                    "platform": DOMAIN,
                    "host": HOST,
                    "username": USERNAME,
                    "password": PASSWORD,
                    CONF_CONSIDER_HOME: 90,
                    CONF_SCAN_INTERVAL: 5,
                }
            ]
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_HOST] == HOST
    assert entries[0].options[CONF_CONSIDER_HOME] == 90
    assert entries[0].options[CONF_SCAN_INTERVAL] == 10
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "yaml_imported")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


async def test_yaml_import_reports_a_down_router(hass, monkeypatch) -> None:
    """A router that cannot be reached during import becomes a repair."""
    install_router(monkeypatch, connection_error())

    assert await async_setup_component(
        hass,
        DEVICE_TRACKER_DOMAIN,
        {
            DEVICE_TRACKER_DOMAIN: [
                {
                    "platform": DOMAIN,
                    "host": HOST,
                    "username": USERNAME,
                    "password": PASSWORD,
                }
            ]
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.config_entries.async_entries(DOMAIN) == []
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, "yaml_import_cannot_connect_192_168_2_254"
    )
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.ERROR
