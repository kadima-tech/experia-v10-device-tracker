"""
Support for ZTE H369A router.
"""
import logging
import re
from datetime import datetime, timezone
from hashlib import sha256
import xml.etree.ElementTree as ET
from collections import namedtuple

import requests
from requests.exceptions import ConnectionError
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

Device = namedtuple('Device', ['mac', 'name', 'ip'])

def parse_xml(data):
    result_root = ET.fromstring(data)
    device_list = result_root.find('OBJ_ACCESSDEV_ID')

    if device_list is None:
        return False

    results = []
    for device in device_list:
        keys = device.findall('ParaName')
        values = device.findall('ParaValue')

        result = {}
        for index, key in enumerate(keys):
            value = values[index]

            if key.text in ['HostName', 'MACAddress', 'IPAddress']:
                result[key.text] = value.text or ''

        results.append(Device(result['MACAddress'].upper(), result['HostName'], result['IPAddress']))

    return results

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

        self.host = host
        self.username = username
        self.password = password

        self.last_results = []
        self.success_init = self._update_info()

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()
        return [device.mac for device in self.last_results]

    # pylint: disable=no-self-use
    def get_device_name(self, device):
        """Return the name of the given device or None if we don't know."""
        filter_named = [result.name for result in self.last_results
                        if result.mac == device]

        if filter_named:
            return filter_named[0]
        return None

    def get_extra_attributes(self, device):
        """Return the extra attibutes of the given device."""
        filter_device = next((
            result for result in self.last_results
            if result.mac == device), None)

        if filter_device:
            return {'ip': filter_device.ip}
        return None

    def _update_info(self):
        """Ensure the information from the ZTE router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("Loading wireless clients...")
        session = requests.Session()

        token_url = 'http://{}/function_module/login_module/login_page/logintoken_lua.lua'.format(self.host)

        login_url = 'http://{}'.format(self.host)

        # Generate a timestamp for the request
        ts = round(datetime.now(timezone.utc).timestamp() * 1000)
        data_url = 'http://{}/common_page/home_AssociateDevs_lua.lua?AccessMode=WLAN&_={}'.format(self.host, ts)
        logout_url = 'http://{}'.format(self.host)

        # Login to get the required cookies
        session.get(login_url)
        result = session.get(token_url)

        login_payload = {
            "Username": self.username,
            "Password": sha256((self.password + re.findall(r'\d+', result.text)[0]).encode('utf-8')).hexdigest(),
            "action": "login"
        }

        logout_payload = {
            "IF_LogOff": 1,
            "IF_LanguageSwitch": "",
            "IF_ModeSwitch": ""
        }

        # If the router is on a older version, the token_url does not exist
        # Use the 'old' way of getting the data (Tested on V1.01.01T01.9)
        if result.status_code == 404:
            login_payload["Password"] = self.password
            login_payload["Frm_Logintoken"] = ""

        session.post(login_url, data=login_payload)

        # Get the data
        data_page = session.get(data_url)

        # And log back out
        session.post(logout_url, data=logout_payload)

        data = data_page.text
        if result or result.status_code == 404:
            self.last_results = parse_xml(data)
            return True

        return False
