# Experia Box V10 device tracker

Device tracker for the ZTE H369A router sold by KPN and Telfort as the Experia Box V10. It logs in to the router's web interface, reads the associated Wi-Fi clients, and creates a Home Assistant device tracker for each client.

The integration is [MIT licensed](LICENSE.md).

![Experia Box V10 icon](custom_components/experiaboxv10/brand/icon.png)

## Supported Home Assistant version

Home Assistant **2026.10** or newer.

That is the stable release this version was checked against. The device tracker code follows the 2026.10 config-entry and `ScannerEntity` APIs. Legacy `device_tracker` platforms are scheduled for removal in Home Assistant 2027.5, so setup is a config flow, with a one-time import of an existing YAML platform.

## Install

### HACS

1. In HACS, choose **Integrations** → **Custom repositories**.
2. Add `https://github.com/kadima-tech/experia-v10-device-tracker` as an integration.
3. Install **Experia Box V10** and restart Home Assistant.

`hacs.json` declares a minimum of Home Assistant 2026.10.0.

### Manual

1. Download this repository.
2. Copy `custom_components/experiaboxv10` to `config/custom_components/experiaboxv10`.
3. Restart Home Assistant.

The folder inside your Home Assistant config must be `custom_components/experiaboxv10`.

## Configuration

Add it from **Settings → Devices & services → Add integration → Experia Box V10**.

| Field | Meaning |
| --- | --- |
| Host | IP address or hostname of the router, usually `192.168.2.254`. A pasted `http://` URL is accepted. |
| Username | Web interface username. The form suggests `Admin`. |
| Password | Web interface password. |

The integration logs in over HTTP on your LAN, reads the client list, and logs out. The router does not offer this API over HTTPS. Each poll does a full login and logout, so the default interval is 30 seconds and the minimum is 10.

Options, on the integration's **Configure** page:

| Option | Default | Meaning |
| --- | --- | --- |
| Scan interval | 30 seconds | How often to ask the router. |
| Consider home | 180 seconds | How long a client stays `home` after it disappears from the router list. Use this for phones that sleep while they are still at home. `0` marks the client away on the next poll. |

### What gets tracked

The router is queried at `home_AssociateDevs_lua.lua?AccessMode=WLAN`. Every client in that response becomes a device tracker.

Home Assistant 2026.10 disables a new router tracker until that MAC address is already known to another integration, or until you enable the entity yourself. Enable the ones you want under **Settings → Devices & services → Entities**. Filter on the integration and show disabled entities.

A client that is away when Home Assistant restarts is restored as `not_home` instead of being removed.

### Firmware login

Three firmware variants are supported:

* **KPN WebUI firmware (2026).** `http://<router>/` serves the Flutter "KPN WebUI". The integration uses the box's JSON API on `/ws` instead: it logs in with `sah.Device.Information.createContext` (username `admin`; the `Admin` default is accepted), reads active Wi-Fi and Ethernet devices with `Devices.get`, and releases the session afterwards. It is also tried when both legacy login URLs return HTTP 404.
* **Token firmware.** `logintoken_lua.lua` returns a page. The password is sent as `sha256(password + the first integer on that page)`.
* **Older firmware, including V1.01.01T01.9.** That URL returns HTTP 404. The password is sent as plaintext with an empty `Frm_Logintoken`.

The first-integer rule is unchanged from the previous release. A token page whose first number is not the login token will not authenticate.

## Migrating an existing YAML setup

Version 2 replaces the YAML platform. On startup, an existing entry is imported into the UI and a repair asks you to delete it.

```yaml
device_tracker:
  - platform: experiaboxv10
    host: 192.168.2.254
    username: Admin
    password: !secret experiabox_password
    consider_home: 180
    interval_seconds: 30
```

`consider_home` and `interval_seconds` are copied into the integration options. `new_device_defaults` is ignored.

Remove these lines from `configuration.yaml` after the import:

* The whole `experiaboxv10` platform block.
* `hide_if_away` if it is still there. Home Assistant removed that option years ago, and a file that still contains it will not pass configuration check, so the import will not run.
* The Experia devices in `known_devices.yaml`. They are no longer updated. A device whose id is still its MAC address can collide with the new entity id.

Trackers that you renamed in `known_devices.yaml` get a new entity id. Update automations and person entries that used the old id.

The password is stored in Home Assistant's config entry storage, the same way other UI integrations store credentials.

## What this version does not do

* It only lists clients the router returns for `AccessMode=WLAN`. Clients that a Wi-Fi extender does not report as associated to the box itself will not appear. See issue #16.
* It treats every returned client as present. It does not read DHCP lease state. A fixed address that stays in that table while the device is offline will stay `home`. See issue #20.
* It is not a Fibaro HC3 script. See issue #23.

## Development

```bash
python -m pip install -r requirements.txt -r requirements_test.txt
ruff check .
ruff format --check .
pytest
```

The tests run on Python 3.14 with `pytest-homeassistant-custom-component` 0.13.373, which installs Home Assistant 2026.10.2. Router HTTP is replaced with scripted responses, including all three firmware variants. Nothing in the test suite contacts a router.

CI runs Ruff, pytest, [hassfest](https://github.com/home-assistant/actions), and [HACS validation](https://github.com/hacs/action).

## Limitations of the tests

Verified with mocks:

* Token login and the V1.01.01T01.9 plaintext login, including a 404 body that contains digits and one that does not.
* Parsing of the associated-client XML, including a missing MAC.
* Config flow success, connection failure, rejected login, a duplicate host, reconfigure, reauth, and options.
* Entity state for a known MAC, disabled entities for an unknown MAC, the consider-home grace period, a client that appears after startup, an unreachable router, unload, and YAML import.

Not verified, because there is no router in this environment:

* The legacy firmware variants against a real box, or any firmware other than the three above. The KPN WebUI support is built from the web app's own API definitions and still needs a live test.
* Whether extenders, guest Wi-Fi, or Ethernet clients show up in `AccessMode=WLAN`.
* Whether a fixed DHCP reservation stays in that table after the device leaves.
* The exact entity id Home Assistant assigns when an existing `known_devices.yaml` entry is still present.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Authors

* **Sjaak Meulen** — migration from Experia v8 to v10 — [sjaakiejj](https://github.com/sjaakiejj)

## License

MIT. See [LICENSE.md](LICENSE.md).

## Acknowledgments

* [Mark van den Berg](https://community.home-assistant.io/t/device-tracker-for-arcadyan-vgv7519-router-experia-box-v8/29362) for the Experia Box V8 script this integration started from.
