"""Options migration doubles: disabling SIP must permit the unchanged 0.1.7."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_native
import protocol.client


def load_flow():
    class Base:
        def __init_subclass__(cls, **kwargs):
            pass
        def async_create_entry(self, **kwargs):
            return kwargs
    modules = {name: ModuleType(name) for name in
               ("voluptuous", "homeassistant", "homeassistant.config_entries", "homeassistant.core",
                "flow_test_comelit", "flow_test_comelit.protocol")}
    modules["homeassistant.config_entries"].ConfigFlow = Base
    modules["homeassistant.config_entries"].OptionsFlow = Base
    modules["homeassistant.core"].callback = lambda func: func
    modules["flow_test_comelit.protocol.client"] = protocol.client
    root = Path(__file__).parents[1] / "custom_components" / "comelit_native"
    modules["flow_test_comelit"].__path__ = [str(root)]
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("flow_test_comelit.config_flow", root / "config_flow.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


FLOW = load_flow()


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = FLOW.NativeOptionsFlow()
        self.entry = NS(data={"local_ip": "192.0.2.10"}, options={"door_relay": 1})
        self.flow.config_entry = self.entry
        self.update = Mock(side_effect=lambda entry, data: setattr(entry, "data", data))
        self.flow.hass = NS(config_entries=NS(async_update_entry=self.update))

    async def test_enable_preserves_network_and_relay_settings(self):
        result = await self.flow.async_step_init({"door_relay": 1, "sip_enabled": True,
            "sip_port": 5060, "sip_allowed_ips": "192.0.2.50"})
        self.assertEqual(result["data"]["sip_allowed_ips"], "192.0.2.50")
        self.assertEqual(self.entry.data, {"local_ip": "192.0.2.10"})
        self.update.assert_not_called()

    async def test_disable_removes_sip_keys_from_data_and_options_for_rollback(self):
        self.entry.data.update(sip_enabled=True, sip_port=5060, sip_allowed_ips="192.0.2.50")
        self.entry.options.update(sip_enabled=True, sip_port=5060, sip_allowed_ips="192.0.2.51")
        result = await self.flow.async_step_init({"door_relay": 1, "sip_enabled": False,
            "sip_port": 5060, "sip_allowed_ips": "192.0.2.51"})
        self.assertEqual(result["data"], {"door_relay": 1})
        self.assertEqual(self.entry.data, {"local_ip": "192.0.2.10"})
        settings = protocol.client.Settings(**(self.entry.data | result["data"]))
        self.assertFalse(settings.sip_enabled)

    async def test_clear_gateway_key_keeps_local_sip_and_allows_0110_rollback(self):
        self.entry.data['remote_gateway_key'] = 'a'*40
        self.entry.data['remote_auto_enabled'] = True
        self.entry.options['remote_gateway_key'] = 'b'*40
        result = await self.flow.async_step_init({'door_relay': 1, 'sip_enabled': True,
            'sip_port': 5062, 'sip_allowed_ips': '192.0.2.51', 'remote_gateway_key': ''})
        self.assertNotIn('remote_gateway_key', result['data'])
        self.assertNotIn('remote_gateway_key', self.entry.data)
        self.assertNotIn('remote_auto_enabled', self.entry.data)
        self.assertNotIn('remote_auto_enabled', result['data'])
        self.assertTrue(result['data']['sip_enabled'])
        self.assertEqual(result['data']['sip_port'],5062)

    async def test_automatic_mode_can_be_enabled_and_removed_for_0111_rollback(self):
        values={'door_relay':1,'sip_enabled':True,'sip_port':5062,
                'sip_allowed_ips':'192.0.2.51','remote_gateway_key':'a'*40,
                'remote_auto_enabled':True}
        result=await self.flow.async_step_init(values)
        self.assertTrue(result['data']['remote_auto_enabled'])
        self.entry.data['remote_auto_enabled']=True
        values['remote_auto_enabled']=False
        result=await self.flow.async_step_init(values)
        self.assertNotIn('remote_auto_enabled',result['data'])
        self.assertNotIn('remote_auto_enabled',self.entry.data)
        self.assertEqual(result['data']['remote_gateway_key'],'a'*40)
