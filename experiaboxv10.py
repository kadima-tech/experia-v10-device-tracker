"""
Support for ZTE H369A router.
"""
import base64
import hashlib
import logging
import re
from datetime import datetime, timezone

import requests
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.device_tracker import (
    DOMAIN, PLATFORM_SCHEMA, DeviceScanner)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_USERNAME): cv.string
})


def get_scanner(hass, config):
    """Validate the configuration and return a ZTE AP scanner."""
    try:
        return ZteH369ADeviceScanner(config[DOMAIN])
    except ConnectionError:
        return None


class ZteH369ADeviceScanner(DeviceScanner):
    """This class queries a wireless router running ZTE firmware."""

    def __init__(self, config):
        """Initialize the scanner."""
        host = config[CONF_HOST]
        username, password = config[CONF_USERNAME], config[CONF_PASSWORD]

        self.parse_macs = re.compile('[0-9A-Fa-f]{2}\:[0-9A-Fa-f]{2}\:[0-9A-Fa-f]{2}\:[0-9A-Fa-f]{2}\:[0-9A-Fa-f]{2}\:[0-9A-Fa-f]{2}')

        self.host = host
        self.username = username
        self.password = password

        self.last_results = {}
        self.success_init = self._update_info()

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()
        return self.last_results

    # pylint: disable=no-self-use
    def get_device_name(self, device):
        """Get firmware doesn't save the name of the wireless device."""
        return None

    def _update_info(self):
        """Ensure the information from the ZTE router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("Loading wireless clients...")
        session = requests.Session()

        login_payload = {
            "Username": self.username,
            "Password": self.password,
            "action": "login",
            "Frm_Logintoken": ""
        }

        logout_payload = {
            "IF_LogOff": 1,
            "IF_LanguageSwitch": "",
            "IF_ModeSwitch": ""
        }

        # Generate a timestamp for the request
        ts = round(datetime.now(timezone.utc).timestamp() * 1000)

        login_url = 'http://{}'.format(self.host)
        data_url = 'http://{}/common_page/home_AssociateDevs_lua.lua?AccessMode=WLAN&_={}'.format(self.host, ts)

        # Login to get the required cookies
        session.get(login_url)
        session.post(login_url, data = login_payload)

        # Get the data
        data_page = session.get(data_url)
        result = self.parse_macs.findall(data_page.text)

        # And log back out
        logout_url = 'http://{}/'.format(self.host)
        log_out_page = session.post(logout_url)

        if result:
            self.last_results = [mac.replace("-", ":") for mac in result]
            return True

        return False
