"""Config flow for Retry Until."""

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class RetryUntilConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Retry Until without any credentials or options."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm setup."""
        if user_input is not None:
            return self.async_create_entry(title="Retry Until", data={})
        return self.async_show_form(step_id="user")
