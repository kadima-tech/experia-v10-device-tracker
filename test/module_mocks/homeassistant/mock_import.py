from mock import MagicMock
import module_mocks.homeassistant.mock_device_tracker
import module_mocks.homeassistant.mock_const
import sys

sys.modules['voluptuous'] = MagicMock()
sys.modules['homeassistant'] = MagicMock()
sys.modules['homeassistant.const'] = module_mocks.homeassistant.mock_const
sys.modules['homeassistant.helpers.config_validation'] = MagicMock()
sys.modules['homeassistant.components.device_tracker'] = module_mocks.homeassistant.mock_device_tracker
