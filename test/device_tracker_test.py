import mock
from mock import patch
import unittest

# Import mocks
import module_mocks.homeassistant.mock_import
from module_mocks.request.session import MockSession

from experiaboxv10 import device_tracker

@patch("homeassistant.components.device_tracker.DeviceScanner", 'domain')
class Test(unittest.TestCase):

    @patch('requests.Session', MockSession )
    def test_initialise(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        self.assertEqual(scanner.last_results, [device_tracker.Device('40:5A:A4:6B:33:4B', 'Samsung', '192.168.2.16')])

    @patch('requests.Session', MockSession )
    def test_scan_devices(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        mac_addresses = scanner.scan_devices()

        self.assertEqual(mac_addresses, ['40:5A:A4:6B:33:4B'])

    @patch('requests.Session', MockSession )
    def test_get_device_name(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        device_name = scanner.get_device_name('40:5A:A4:6B:33:4B')

        self.assertEqual(device_name, 'Samsung')

    @patch('requests.Session', MockSession )
    def test_get_device_name_unknown(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        device_name = scanner.get_device_name('INVALID-MAC-ADDRESS')

        self.assertEqual(device_name, None)

    @patch('requests.Session', MockSession )
    def test_get_extra_attributes(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        extra_attributes = scanner.get_extra_attributes('40:5A:A4:6B:33:4B')

        self.assertEqual(extra_attributes['ip'], '192.168.2.16')

    @patch('requests.Session', MockSession )
    def test_get_extra_attributes_unknown(self):
        scanner = device_tracker.ZteH369ADeviceScanner({
            'local': '192.168.2.254', # const.CONF_HOST,
            'password': 'password',   # const.CONF_PASSWORD,
            'username': 'username'   # const.CONF_USERNAME,
        })

        extra_attributes = scanner.get_extra_attributes('INVALID-MAC-ADDRESS')

        self.assertEqual(extra_attributes, None)
