"""Config flow for Voice Labs: one entry, nothing to ask.

The voice_labs app starts this flow itself when it finds the integration
installed but not set up; it can also be added from the UI.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class VoiceLabsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Voice Labs."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Voice Labs", data={})
