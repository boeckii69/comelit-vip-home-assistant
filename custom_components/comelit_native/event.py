from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback
from .entity import ComelitEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Ring(entry.runtime_data)])


class Ring(ComelitEntity, EventEntity):
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]

    def __init__(self, hub):
        super().__init__(hub, "ring", "Ring")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.subscribe(self.receive, ring=True))

    @callback
    def receive(self, event):
        self._trigger_event("ring", event)
        self.async_write_ha_state()
