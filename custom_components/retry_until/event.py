"""Retry Until failure events."""

from homeassistant.components.event import EventEntity
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import JsonObjectType

from .const import ATTEMPTS_EXHAUSTED
from .runtime import RetryConfigEntry, RetryRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RetryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the failure event entity."""
    async_add_entities([RetryFailureEvent(entry.runtime_data)])


class RetryFailureEvent(EventEntity):
    """The most recent exhausted Retry Until run."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "failure"

    def __init__(self, runtime: RetryRuntime) -> None:
        """Initialize the failure event."""
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.entry_id}_failure"
        self._attr_event_types = [ATTEMPTS_EXHAUSTED]

    async def async_added_to_hass(self) -> None:
        """Restore the most recent event and subscribe to new failures."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._runtime.failure_signal, self._async_failure
            )
        )

    @callback
    def _async_failure(
        self, event_type: str, details: JsonObjectType, context: Context
    ) -> None:
        """Publish the failure with its original action context."""
        self.async_set_context(context)
        self._trigger_event(event_type, details)
        self.async_write_ha_state()
