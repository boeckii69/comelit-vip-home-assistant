from homeassistant.helpers.entity import DeviceInfo, Entity
from .const import DOMAIN, VERSION


class ComelitEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub, key, name):
        self.hub = hub
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_name = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.entry.entry_id)}, name="Comelit",
            manufacturer="Comelit", model="ViP · TP6842 / UT8020",
            sw_version=VERSION)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.subscribe(self.async_write_ha_state))

    @property
    def available(self):
        return self.hub.client.registered
