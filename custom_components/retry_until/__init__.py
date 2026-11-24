"""Retry Until integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .runtime import RetryConfigEntry, RetryRuntime
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_PLATFORMS = (Platform.SENSOR, Platform.EVENT)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions so they can be edited even when the entry is unloaded."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RetryConfigEntry) -> None:
    """Enable the actions and set up their status entities."""
    entry.runtime_data = RetryRuntime(entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: RetryConfigEntry) -> None:
    """Unload status entities before Home Assistant drains the retry tasks."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, _PLATFORMS)
