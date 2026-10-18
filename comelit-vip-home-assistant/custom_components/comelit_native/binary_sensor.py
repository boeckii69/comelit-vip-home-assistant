from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import ComelitEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Doorbell(entry.runtime_data)])


class Doorbell(ComelitEntity, BinarySensorEntity):
    _attr_icon = "mdi:doorbell"

    def __init__(self, hub):
        super().__init__(hub, "doorbell", "Doorbell")

    @property
    def is_on(self):
        return self.hub.pulse
