"""Polling coordinator for the Experia Box V10."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .router import CannotConnect, InvalidAuth, fetch_clients

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TrackedClient:
    """A client, including the last time the router reported it."""

    mac: str
    name: str
    ip: str
    last_seen: datetime


type ExperiaConfigEntry = ConfigEntry[ExperiaCoordinator]


class ExperiaCoordinator(DataUpdateCoordinator[dict[str, TrackedClient]]):
    """Fetch associated clients without blocking the event loop."""

    config_entry: ExperiaConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ExperiaConfigEntry) -> None:
        """Initialize the coordinator."""
        scan_interval = _option_seconds(
            config_entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=max(scan_interval, MIN_SCAN_INTERVAL)),
        )
        self._tracked: dict[str, TrackedClient] = {}

    async def _async_update_data(self) -> dict[str, TrackedClient]:
        """Fetch clients and keep recently seen ones through consider_home."""
        host = self.config_entry.data["host"]
        try:
            clients = await self.hass.async_add_executor_job(
                fetch_clients,
                host,
                self.config_entry.data["username"],
                self.config_entry.data["password"],
            )
        except CannotConnect as err:
            raise UpdateFailed(
                f"Error communicating with Experia Box at {host}"
            ) from err
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(
                f"Experia Box at {host} rejected the login"
            ) from err

        now = dt_util.utcnow()
        updated = {
            client.mac: TrackedClient(client.mac, client.name, client.ip, now)
            for client in clients
        }
        grace = timedelta(
            seconds=_option_seconds(
                self.config_entry, CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME
            )
        )
        for mac, previous in self._tracked.items():
            if mac in updated:
                continue
            if now - previous.last_seen < grace:
                updated[mac] = previous
        self._tracked = updated
        _LOGGER.debug("Tracking %s client(s) from %s", len(updated), host)
        return updated


def _option_seconds(config_entry: ConfigEntry, key: str, default: int) -> int:
    """Return an integer option, falling back when it is missing or invalid."""
    raw = config_entry.options.get(key, default)
    try:
        return int(raw)
    except TypeError, ValueError:
        return default
