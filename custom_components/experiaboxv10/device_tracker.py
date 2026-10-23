"""Device tracker platform for the Experia Box V10."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import override

import homeassistant.helpers.config_validation as cv
import probatio
from homeassistant.components.device_tracker import (
    PLATFORM_SCHEMA as DEVICE_TRACKER_PLATFORM_SCHEMA,
)
from homeassistant.components.device_tracker import (
    ScannerEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONSIDER_HOME, CONF_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL
from .coordinator import ExperiaConfigEntry, ExperiaCoordinator, TrackedClient

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

PLATFORM_SCHEMA = DEVICE_TRACKER_PLATFORM_SCHEMA.extend(
    {
        probatio.Required(CONF_HOST): cv.string,
        probatio.Required(CONF_USERNAME): cv.string,
        probatio.Required(CONF_PASSWORD): cv.string,
    }
)

_IMPORT_FAILURES = ("cannot_connect", "invalid_auth", "unknown")


async def async_setup_scanner(
    hass: HomeAssistant,
    config: ConfigType,
    async_see: object,
    discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """Import a legacy YAML ``device_tracker`` platform into a config entry."""
    host = str(config[CONF_HOST])
    data: dict[str, object] = {
        CONF_HOST: host,
        CONF_USERNAME: config[CONF_USERNAME],
        CONF_PASSWORD: config[CONF_PASSWORD],
    }
    if CONF_CONSIDER_HOME in config:
        data[CONF_CONSIDER_HOME] = _seconds(config[CONF_CONSIDER_HOME])
    if CONF_SCAN_INTERVAL in config:
        data[CONF_SCAN_INTERVAL] = max(
            _seconds(config[CONF_SCAN_INTERVAL]), MIN_SCAN_INTERVAL
        )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data=data,
    )
    if result["type"] is FlowResultType.CREATE_ENTRY or (
        result["type"] is FlowResultType.ABORT
        and result["reason"] == "already_configured"
    ):
        _clear_import_issues(hass, host)
        ir.async_create_issue(
            hass,
            DOMAIN,
            "yaml_imported",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="yaml_imported",
            translation_placeholders={"host": host},
        )
        return True

    reason = result.get("reason", "unknown")
    if reason not in _IMPORT_FAILURES:
        _LOGGER.error("Could not import Experia Box YAML for %s: %s", host, result)
        reason = "unknown"
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"yaml_import_{reason}_{_issue_suffix(host)}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=f"yaml_import_{reason}",
        translation_placeholders={"host": host},
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ExperiaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up tracker entities for clients of one router."""
    coordinator = entry.runtime_data
    tracked: set[str] = set()
    prefix = f"{entry.entry_id}_"
    restored: list[ExperiaScannerEntity] = []
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity_entry.unique_id or ""
        if not unique_id.startswith(prefix):
            continue
        mac = unique_id.removeprefix(prefix)
        if mac in tracked:
            continue
        tracked.add(mac)
        restored.append(
            ExperiaScannerEntity(coordinator, mac, coordinator.data.get(mac))
        )
    if restored:
        async_add_entities(restored)

    @callback
    def _async_add_new_devices() -> None:
        """Add entities for clients seen for the first time."""
        additions = []
        for mac, client in coordinator.data.items():
            if mac in tracked:
                continue
            tracked.add(mac)
            additions.append(ExperiaScannerEntity(coordinator, mac, client))
        if additions:
            async_add_entities(additions)

    _async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))


class ExperiaScannerEntity(CoordinatorEntity[ExperiaCoordinator], ScannerEntity):
    """A device the Experia Box has reported."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: ExperiaCoordinator,
        mac: str,
        client: TrackedClient | None,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{mac}"
        self._attr_mac_address = mac
        self._apply_client(client)

    @property
    @override
    def unique_id(self) -> str | None:
        """Return an entry-scoped unique id.

        ``ScannerEntity.unique_id`` is the bare MAC. Scoping it avoids a clash
        when two routers see the same client.
        """
        return self._attr_unique_id

    @property
    @override
    def is_connected(self) -> bool:
        """Return true while the client is associated or inside consider_home."""
        return self._mac in self.coordinator.data

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Update the last known name and address."""
        client = self.coordinator.data.get(self._mac)
        if client is not None:
            self._apply_client(client)
        super()._handle_coordinator_update()

    def _apply_client(self, client: TrackedClient | None) -> None:
        name = client.name if client and client.name else ""
        self._attr_hostname = name or None
        self._attr_ip_address = client.ip if client and client.ip else None
        self._attr_name = name or self._mac


def _seconds(value: object) -> int:
    """Return a YAML time period as whole seconds."""
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"Unsupported time period: {value!r}")


def _issue_suffix(host: str) -> str:
    return host.replace(".", "_").replace(":", "_")


def _clear_import_issues(hass: HomeAssistant, host: str) -> None:
    suffix = _issue_suffix(host)
    for reason in _IMPORT_FAILURES:
        ir.async_delete_issue(hass, DOMAIN, f"yaml_import_{reason}_{suffix}")
