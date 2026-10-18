"""One explicit door-release command; no automatic actuation."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import ComelitEntity
from .protocol.client import DoorCommandError
from .protocol.call import CallError

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([OpenDoor(entry.runtime_data), AnswerCall(entry.runtime_data),
                        HangupCall(entry.runtime_data), AllowRemoteAudio(entry.runtime_data)])


class AllowRemoteAudio(ComelitEntity, ButtonEntity):
    _attr_icon = "mdi:phone-lock"

    def __init__(self, hub):
        super().__init__(hub, "allow_remote_audio", "Audio extern freigeben")

    @property
    def available(self):
        return (bool(self.hub.client.settings.remote_gateway_key)
                and self.hub.client.registered and self.hub.sip.transport is not None)

    async def async_press(self):
        if not self.available:
            raise HomeAssistantError("Externes Audio-Gateway ist nicht eingerichtet")
        self.hub.sip.grant_remote_audio()


class OpenDoor(ComelitEntity, ButtonEntity):
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, hub):
        super().__init__(hub, "open_door", "Open door")

    @property
    def extra_state_attributes(self):
        client = self.hub.client
        return {"target_vip": client.settings.entrance_address,
                "relay": client.settings.door_relay,
                "command_status": client.door_state,
                "command_error": client.door_error,
                "transport_acknowledged": client.door_acknowledged,
                "release_cause": client.door_release_cause,
                "command_path": client.door_command_path,
                "command_peer": client.door_command_peer,
                "command_call_id": client.door_call_id,
                "last_command_sent": client.last_door_send,
                "last_command_relay": client.last_door_relay,
                "last_command_confirmed": client.last_door_confirmation}

    async def async_press(self):
        try:
            await self.hub.client.open_door()
        except DoorCommandError as exc:
            raise HomeAssistantError(str(exc)) from exc


class AnswerCall(ComelitEntity, ButtonEntity):
    _attr_icon = "mdi:phone-incoming"

    def __init__(self, hub):
        super().__init__(hub, "answer_call", "Ruf annehmen")

    @property
    def available(self):
        return self.hub.client.call.can_answer

    async def async_press(self):
        try:
            await self.hub.client.call.answer()
        except CallError as exc:
            raise HomeAssistantError(str(exc)) from exc


class HangupCall(ComelitEntity, ButtonEntity):
    _attr_icon = "mdi:phone-hangup"

    def __init__(self, hub):
        super().__init__(hub, "hangup_call", "Auflegen")

    @property
    def available(self):
        return self.hub.client.call.owned

    async def async_press(self):
        try:
            await self.hub.client.call.hangup()
        except CallError as exc:
            raise HomeAssistantError(str(exc)) from exc
