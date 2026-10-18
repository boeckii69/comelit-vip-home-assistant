"""Explicit configuration only; no discovery or port scans."""
from dataclasses import asdict

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN, NAME

DOCUMENTATION_URL = "https://github.com/boeckii69/comelit-vip-home-assistant"
from .protocol.client import Settings

SIP_KEYS = ("sip_enabled", "sip_port", "sip_allowed_ips", "remote_gateway_key", "remote_auto_enabled")


def without_sip(values):
    return {key: value for key, value in values.items() if key not in SIP_KEYS}


def _secret_selector():
    from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NativeOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                settings = Settings(**user_input)
                settings.validate()
            except (ValueError, TypeError):
                errors["base"] = "invalid_config"
            else:
                # One local responder owns UDP 24100; do not install duplicates.
                if self._async_current_entries():
                    return self.async_abort(reason="single_instance_allowed")
                await self.async_set_unique_id(f"{settings.master_ip}_{settings.source_address}")
                self._abort_if_unique_id_configured()
                data = asdict(settings)
                return self.async_create_entry(title=NAME,
                    data=data if settings.sip_enabled else without_sip(data))
        values = asdict(Settings())
        for key in ("local_ip", "crp_broadcast_ip", "master_ip", "entrance_ip", "loxone_ip"):
            values[key] = ""
        values.update(user_input or {})
        schema = {}
        for key, value in values.items():
            expected = bool if key.endswith("_enabled") else int if key.endswith("_port") or key == "door_relay" else str
            if key == "door_relay":
                expected = vol.In([1, 2])
            marker = vol.Optional if key in ("sip_allowed_ips", "remote_gateway_key", "loxone_ip") else vol.Required
            schema[marker(key, default=value)] = _secret_selector() if key == "remote_gateway_key" else expected
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors,
            description_placeholders={"documentation_url": DOCUMENTATION_URL})


class NativeOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        current = dict(self.config_entry.data) | dict(self.config_entry.options)
        if user_input is not None:
            user_input = dict(user_input)
            user_input.setdefault("sip_allowed_ips", "")
            user_input.setdefault("remote_gateway_key", "")
            user_input.setdefault("remote_auto_enabled", False)
            if not user_input.get("sip_enabled", False):
                user_input.update(sip_enabled=False, sip_allowed_ips="", sip_port=5062,
                                  remote_gateway_key="", remote_auto_enabled=False)
            try:
                Settings(**(current | user_input)).validate()
            except (ValueError, TypeError):
                errors["base"] = "invalid_options"
            else:
                if not user_input["sip_enabled"]:
                    # Disabling also removes the new keys, so the unmodified
                    # confirmed 0.1.7 Settings constructor can load this entry.
                    original = dict(self.config_entry.data)
                    legacy = without_sip(original)
                    if legacy != original:
                        self.hass.config_entries.async_update_entry(self.config_entry, data=legacy)
                    return self.async_create_entry(title="", data=without_sip(user_input))
                if not user_input["remote_auto_enabled"] or not user_input["remote_gateway_key"]:
                    user_input.pop("remote_auto_enabled")
                    if "remote_auto_enabled" in self.config_entry.data:
                        original = dict(self.config_entry.data)
                        original.pop("remote_auto_enabled")
                        self.hass.config_entries.async_update_entry(self.config_entry, data=original)
                if not user_input["remote_gateway_key"]:
                    user_input.pop("remote_gateway_key")
                    if "remote_gateway_key" in self.config_entry.data:
                        original = dict(self.config_entry.data)
                        original.pop("remote_gateway_key")
                        self.hass.config_entries.async_update_entry(self.config_entry, data=original)
                return self.async_create_entry(title="", data=user_input)
        values = asdict(Settings()) | current | (user_input or {})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("door_relay", default=values["door_relay"]): vol.In([1, 2]),
            vol.Required("sip_enabled", default=values["sip_enabled"]): bool,
            vol.Required("sip_port", default=values["sip_port"]): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
            vol.Optional("sip_allowed_ips", default=values["sip_allowed_ips"]): str,
            vol.Optional("remote_gateway_key", default=values["remote_gateway_key"]): _secret_selector(),
            vol.Required("remote_auto_enabled", default=values["remote_auto_enabled"]): bool,
        }), errors=errors)
