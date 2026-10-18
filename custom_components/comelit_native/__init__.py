"""Native ViP ring, door release, HA listening and optional LAN SIP audio."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import DOMAIN
from .protocol.client import NativeClient, Settings
from .protocol.sip import SipBridge
from .websocket import async_setup_api

PLATFORMS = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR, Platform.BUTTON]
LOG = logging.getLogger(__name__)


class Hub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.listeners = set()
        self.ring_listeners = set()
        self.pulse = False
        self.reset_handle = None
        self.stop_unsub = None
        self.sip = None
        self.client = NativeClient(Settings(**(dict(entry.data) | dict(entry.options))),
                                   self.ring, self.changed)
        self.sip = SipBridge(self.client, self.changed)

    @callback
    def subscribe(self, listener, ring=False):
        target = self.ring_listeners if ring else self.listeners
        target.add(listener)
        return lambda: target.discard(listener)

    @callback
    def changed(self):
        if self.sip is not None:
            self.sip.native_changed()
        for listener in tuple(self.listeners):
            listener()

    @callback
    def ring(self, event):
        self.pulse = True
        if self.reset_handle:
            self.reset_handle.cancel()
        self.reset_handle = asyncio.get_running_loop().call_later(2, self.reset)
        self.hass.bus.async_fire("comelit_ring", {"entry_id": self.entry.entry_id, **event})
        for listener in tuple(self.ring_listeners):
            listener(event)
        self.changed()

    @callback
    def reset(self):
        self.reset_handle = None
        self.pulse = False
        self.changed()

    async def stop(self, _event=None):
        if self.stop_unsub:
            self.stop_unsub()
            self.stop_unsub = None
        if self.reset_handle:
            self.reset_handle.cancel()
            self.reset_handle = None
        self.pulse = False
        if self.sip is not None:
            await self.sip.stop()
        await self.client.stop()


async def async_setup_entry(hass, entry):
    await async_setup_api(hass)
    hub = Hub(hass, entry)
    entry.runtime_data = hub
    try:
        await hub.client.start()
    except (OSError, ValueError) as exc:
        raise ConfigEntryNotReady(f"UDP konnte nicht gestartet werden: {exc}") from exc
    await hub.sip.start()
    hub.stop_unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.stop)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    async def authorize_remote_audio(_call):
        if (not hub.client.registered or not hub.client.settings.remote_gateway_key
                or hub.sip.transport is None):
            raise HomeAssistantError("Externes Audio-Gateway ist nicht eingerichtet")
        hub.sip.grant_remote_audio()
    hass.services.async_register(DOMAIN, "authorize_remote_audio", authorize_remote_audio)
    LOG.info("Comelit Native gestartet; Türöffnen und Rufannahme erfordern eine ausdrückliche Aktion")
    return True


async def async_update_options(hass, entry):
    """Change relay/SIP without restarting the working native ring listener."""
    settings = Settings(**(dict(entry.data) | dict(entry.options)))
    settings.validate()
    hub = entry.runtime_data
    cfg = hub.client.settings
    cfg.door_relay = settings.door_relay
    fields = ("sip_enabled", "sip_port", "sip_allowed_ips", "remote_gateway_key", "remote_auto_enabled")
    if any(getattr(cfg, name) != getattr(settings, name) for name in fields):
        await hub.sip.stop()
        for name in fields:
            setattr(cfg, name, getattr(settings, name))
        await hub.sip.start()
    hub.changed()


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.services.async_remove(DOMAIN, "authorize_remote_audio")
        await entry.runtime_data.stop()
        return True
    return False
