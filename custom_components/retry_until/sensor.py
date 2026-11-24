"""Live counts of active Retry Until runs."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .runtime import RetryConfigEntry, RetryRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RetryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one sensor for each action."""
    async_add_entities(
        RunningCountSensor(entry.runtime_data, action)
        for action in (STATE_ON, STATE_OFF)
    )


class RunningCountSensor(SensorEntity):
    """Number of active calls to a Retry Until action."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime: RetryRuntime, action: str) -> None:
        """Initialize the running count."""
        self._runtime = runtime
        self._action = action
        self._attr_unique_id = f"{runtime.entry_id}_{action}_running_count"
        self._attr_translation_key = f"{action}_running_count"

    @property
    def native_value(self) -> int:
        """Return the current number of running calls."""
        return self._runtime.running[self._action]

    async def async_added_to_hass(self) -> None:
        """Subscribe to count changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._runtime.count_signal(self._action),
                self.async_write_ha_state,
            )
        )
