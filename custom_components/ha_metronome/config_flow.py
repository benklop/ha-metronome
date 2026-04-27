"""Config flow for HA Metronome."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, INTEGRATION_TITLE

STEP_USER = "user"


class HaMetronomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: single metronome instance, no per-entry options in v1."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(
                title=INTEGRATION_TITLE,
                data={},
            )
        return self.async_show_form(
            step_id=STEP_USER,
            data_schema=vol.Schema({}),
        )

    async def async_step_import(self, import_config: dict | None = None):
        """Create entry from `ha_metronome:` in configuration.yaml (legacy)."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=INTEGRATION_TITLE,
            data={},
        )
