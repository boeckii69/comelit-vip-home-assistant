from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from .entity import ComelitEntity
from .const import VERSION


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Registration(entry.runtime_data), CallState(entry.runtime_data), SipState(entry.runtime_data)])


class Registration(ComelitEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:connection"

    def __init__(self, hub):
        super().__init__(hub, "registration", "Registrierung")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self.hub.client.state

    @property
    def extra_state_attributes(self):
        client = self.hub.client
        return {"error": client.error, "client_version": VERSION,
                "udp_port": client.local_port,
                "ctp_bind_endpoint": client.ctp_bind_endpoint,
                "ctp_route_source": client.ctp_route_source,
                "master_endpoint": f"{client.settings.master_ip}:{client.master_port}",
                "configured_master_port": client.settings.master_port,
                "crp_state": client.crp_state,
                "crp_destination": f"{client.settings.crp_broadcast_ip}:24100",
                "crp_requests": client.crp_requests,
                "crp_responses": client.crp_responses,
                "crp_return_path_matches": client.crp_return_path_matches,
                "last_crp_error": client.last_crp_error,
                "registration_attempt": client.registration_attempt,
                "registration_tx": client.registration_tx,
                "master_udp_received": client.master_udp_received,
                "master_ctp_accepted": client.master_ctp_accepted,
                "registration_ctp_received": client.registration_ctp_received,
                "registration_acknowledged": client.registration_acknowledged,
                "rejected_packets": client.rejected_packets,
                "last_rejection": client.last_rejection,
                "last_ctp": client.last_ctp,
                "last_udp_error": client.last_udp_error,
                "last_registration": client.last_registration,
                "last_ring": client.last_ring, "ring_count": client.ring_count,
                "last_loxone_send": client.last_loxone_send,
                "door_state": client.door_state,
                "door_error": client.door_error,
                "door_target": client.settings.entrance_address,
                "door_relay": client.settings.door_relay,
                "door_endpoint": f"{client.settings.entrance_ip}:{client.entrance_port}",
                "door_acknowledged": client.door_acknowledged,
                "door_release_cause": client.door_release_cause,
                "door_command_path": client.door_command_path,
                "door_command_peer": client.door_command_peer,
                "door_call_id": client.door_call_id,
                "door_command_count": client.door_command_count,
                "last_door_send": client.last_door_send,
                "last_door_relay": client.last_door_relay,
                "last_door_confirmation": client.last_door_confirmation,
                "hardware_verified": False}


class CallState(ComelitEntity, SensorEntity):
    _attr_icon = "mdi:phone"

    def __init__(self, hub):
        super().__init__(hub, "call_state", "Rufstatus")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self.hub.client.call.state

    @property
    def extra_state_attributes(self):
        return {"comelit_native_call": True, "client_version": VERSION,
                **self.hub.client.call.diagnostics()}


class SipState(ComelitEntity, SensorEntity):
    _attr_icon = "mdi:phone-voip"

    def __init__(self, hub):
        super().__init__(hub, "sip_state", "SIP Status")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self.hub.sip.state

    @property
    def extra_state_attributes(self):
        return {"client_version": VERSION, **self.hub.sip.diagnostics()}
