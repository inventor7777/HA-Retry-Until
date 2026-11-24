"""Config-entry state for Retry Until."""

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Context
from homeassistant.util.json import JsonObjectType
from homeassistant.util.signal_type import SignalType

from .const import DOMAIN


@dataclass
class RetryRuntime:
    """Track live runs and publish updates to the entry's entities."""

    entry_id: str
    running: dict[str, int] = field(default_factory=lambda: {STATE_ON: 0, STATE_OFF: 0})

    def count_signal(self, action: str) -> SignalType[()]:
        """Return the signal for changes to one action's running count."""
        return SignalType(f"{DOMAIN}_{self.entry_id}_{action}_count")

    @property
    def failure_signal(self) -> SignalType[str, JsonObjectType, Context]:
        """Return the signal for failed runs."""
        return SignalType(f"{DOMAIN}_{self.entry_id}_failure")


type RetryConfigEntry = ConfigEntry[RetryRuntime]
