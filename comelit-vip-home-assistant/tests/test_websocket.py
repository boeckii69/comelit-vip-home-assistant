"""Permission and routing tests with explicit HA API doubles, not a HA boot."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_native
import protocol.call


def load_api():
    names = ["homeassistant", "homeassistant.auth", "homeassistant.auth.permissions",
             "homeassistant.auth.permissions.const", "homeassistant.components",
             "homeassistant.components.websocket_api", "homeassistant.components.http",
             "homeassistant.core", "homeassistant.helpers", "homeassistant.helpers.entity_registry",
             "api_under_test", "api_under_test.protocol"]
    modules = {name: ModuleType(name) for name in names}
    modules["homeassistant.auth.permissions.const"].POLICY_CONTROL = "control"
    modules["homeassistant.core"].callback = lambda f: f
    ws = modules["homeassistant.components.websocket_api"]
    ws.websocket_command = lambda schema: lambda f: f
    ws.async_response = lambda f: f
    ws.async_register_command = Mock()
    modules["homeassistant.components.http"].StaticPathConfig = lambda *args: args
    registry = modules["homeassistant.helpers.entity_registry"]
    registry.async_get = lambda hass: hass.registry
    root = Path(__file__).parents[1] / "custom_components" / "comelit_native"
    modules["api_under_test"].__path__ = [str(root)]
    modules["api_under_test.protocol.call"] = protocol.call
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("api_under_test.websocket", root / "websocket.py")
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
    return api


API = load_api()


class WebsocketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.listeners = set()
        def subscribe(cb):
            self.listeners.add(cb)
            return lambda: self.listeners.discard(cb)
        self.call = NS(answer=AsyncMock(), hangup=AsyncMock(), state="ringing",
                       subscribe_audio=subscribe)
        self.entry = NS(entry_id="test", domain="comelit_native",
                        runtime_data=NS(client=NS(running=True, call=self.call)))
        self.entity = NS(platform="comelit_native", config_entry_id="test", unique_id="test_call_state")
        self.hass = NS(config_entries=NS(async_get_entry=lambda eid: self.entry),
                       registry=NS(async_get=lambda eid: self.entity), data={},
                       http=NS(async_register_static_paths=AsyncMock()))
        self.permission = Mock(return_value=True)
        self.conn = NS(user=NS(is_active=True, permissions=NS(check_entity=self.permission)),
                       subscriptions={}, send_error=Mock(), send_result=Mock(), send_event=Mock())
        self.msg = {"id": 1, "entity_id": "sensor.aussenanlage_comelit_rufstatus"}

    async def test_only_explicit_action_controls_selected_call(self):
        await API.ws_call(self.hass, self.conn, self.msg | {"action": "answer"})
        self.call.answer.assert_awaited_once()
        self.call.hangup.assert_not_awaited()
        self.permission.assert_called_with(self.msg["entity_id"], "control")
        await API.ws_call(self.hass, self.conn, self.msg | {"action": "unknown"})
        self.conn.send_error.assert_called_with(1, "call_failed", "Unbekannte Rufaktion")

    async def test_denied_inactive_missing_or_wrong_entity_cannot_control_or_hear(self):
        for scenario in ("denied", "inactive", "other", "door", "unloaded"):
            self.setUp()
            if scenario == "denied": self.permission.return_value = False
            if scenario == "inactive": self.conn.user.is_active = False
            if scenario == "other": self.entity.platform = "another_integration"
            if scenario == "door": self.entity.unique_id = "test_open_door"
            if scenario == "unloaded": self.entry.runtime_data.client.running = False
            with self.subTest(scenario=scenario):
                await API.ws_call(self.hass, self.conn, self.msg | {"action": "answer"})
                API.ws_audio(self.hass, self.conn, self.msg)
                self.call.answer.assert_not_awaited()
                self.assertFalse(self.listeners)
                self.assertEqual(self.conn.send_error.call_count, 2)

    async def test_subscription_never_answers_rechecks_permissions_and_unsubscribes(self):
        API.ws_audio(self.hass, self.conn, self.msg)
        self.call.answer.assert_not_awaited()
        self.conn.send_result.assert_called_once_with(1)
        listener = next(iter(self.listeners))
        listener(b"\x08\0")
        self.conn.send_event.assert_called_with(1, {"type": "pcm", "sample_rate": 8000, "data": "CAA="})
        self.permission.return_value = False
        listener(b"\x08\0")
        self.assertEqual(self.conn.send_event.call_count, 1)
        self.conn.subscriptions[1]()
        self.assertFalse(self.listeners)

    async def test_static_route_and_commands_register_only_once(self):
        await API.async_setup_api(self.hass)
        await API.async_setup_api(self.hass)
        self.hass.http.async_register_static_paths.assert_awaited_once()
        self.assertEqual(API.websocket_api.async_register_command.call_count, 2)
