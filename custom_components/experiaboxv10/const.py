"""Constants for the Experia Box V10 integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "experiaboxv10"

DEFAULT_USERNAME = "Admin"
DEFAULT_CONSIDER_HOME = 180
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
REQUEST_TIMEOUT = 10

# Same keys the legacy device_tracker schema used, so YAML import can read them.
CONF_CONSIDER_HOME = "consider_home"
CONF_SCAN_INTERVAL = "interval_seconds"

TOKEN_PATH = "/function_module/login_module/login_page/logintoken_lua.lua"
DEVICES_PATH = "/common_page/home_AssociateDevs_lua.lua"

# Newer firmware serves the Flutter "KPN WebUI" and a SoftAtHome JSON API.
# The web app posts every call to this one URI; the body names the service.
WS_PATH = "/ws/NeMo/Intf/lan:getMIBs"
# The web app hardcodes this username in its login call.
WS_USERNAME = "admin"
WS_APPLICATION_NAME = "webui"

DEFAULT_SCAN_INTERVAL_TD = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
