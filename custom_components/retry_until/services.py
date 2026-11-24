"""Native Retry Until actions."""

import asyncio
import logging
from time import monotonic

import probatio
from homeassistant.auth.permissions.const import CAT_ENTITIES, POLICY_CONTROL
from homeassistant.components.light import (
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_SUPPORTED_COLOR_MODES,
    brightness_supported,
    color_temp_supported,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ENTITY_MATCH_ALL,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    ServiceCall,
    callback,
    split_entity_id,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import ATTEMPTS_EXHAUSTED, DOMAIN
from .runtime import RetryConfigEntry, RetryRuntime

_LOGGER = logging.getLogger(__name__)

# Keep this in sync with the target selector in services.yaml.
_POWER_DOMAINS = frozenset(
    {
        "automation",
        "fan",
        "humidifier",
        "input_boolean",
        "light",
        "remote",
        "siren",
        "switch",
    }
)

_RETRY_FIELDS = {
    probatio.Optional("max_repeats", default=120): probatio.All(
        probatio.Coerce(int), probatio.Range(min=1, max=240)
    ),
    probatio.Optional("retry_interval_seconds", default=60): probatio.All(
        probatio.Coerce(int), probatio.Range(min=1, max=3600)
    ),
}
_OFF_SCHEMA = cv.make_entity_service_schema(_RETRY_FIELDS)
_ON_SCHEMA = cv.make_entity_service_schema(
    {
        **_RETRY_FIELDS,
        probatio.Optional(ATTR_BRIGHTNESS_PCT): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=100)
        ),
        probatio.Optional(ATTR_COLOR_TEMP_KELVIN): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1)
        ),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register both Retry Until actions."""
    hass.services.async_register(DOMAIN, STATE_ON, _async_retry, schema=_ON_SCHEMA)
    hass.services.async_register(DOMAIN, STATE_OFF, _async_retry, schema=_OFF_SCHEMA)


async def _async_retry(call: ServiceCall) -> None:
    """Run the action in a task owned by the integration's config entry."""
    hass = call.hass
    if not (entries := hass.config_entries.async_loaded_entries(DOMAIN)):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_configured"
        )
    entry: RetryConfigEntry = entries[0]
    await entry.async_create_background_task(
        hass, _async_run_retry(call, entry.runtime_data), "Retry Until action"
    )


async def _async_run_retry(call: ServiceCall, runtime: RetryRuntime) -> None:
    """Send a power command and wait for all targeted entities to report it."""
    hass = call.hass
    desired_state = call.service
    service = SERVICE_TURN_ON if desired_state == STATE_ON else SERVICE_TURN_OFF
    selection = TargetSelection(call.data)
    if ENTITY_MATCH_ALL in selection.entity_ids:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="explicit_target_required"
        )

    selected = async_extract_referenced_entity_ids(hass, selection)
    domains = {
        entity_id: split_entity_id(entity_id)[0]
        for entity_id in selected.referenced | selected.indirectly_referenced
    }
    entities = {
        entity_id
        for entity_id, domain in domains.items()
        if domain in _POWER_DOMAINS and hass.services.has_service(domain, service)
    }

    if user_id := call.context.user_id:
        user = await hass.auth.async_get_user(user_id)
        if user is None:
            raise UnknownUser(
                context=call.context, permission=POLICY_CONTROL, user_id=user_id
            )
        if any(
            not user.permissions.check_entity(entity_id, POLICY_CONTROL)
            for entity_id in entities
        ):
            raise Unauthorized(
                context=call.context,
                permission=POLICY_CONTROL,
                user_id=user_id,
                perm_category=CAT_ENTITIES,
            )

    if unsupported := selected.referenced - entities:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unsupported_targets",
            translation_placeholders={"entities": ", ".join(sorted(unsupported))},
        )
    registry = er.async_get(hass)
    if missing := {
        entity_id
        for entity_id in selected.referenced
        if hass.states.get(entity_id) is None and registry.async_get(entity_id) is None
    }:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_targets",
            translation_placeholders={"entities": ", ".join(sorted(missing))},
        )
    if not entities:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_targets"
        )

    changed = asyncio.Event()

    @callback
    def _state_changed(_event: Event[EventStateChangedData]) -> None:
        changed.set()

    def _pending() -> set[str]:
        return {
            entity_id
            for entity_id in entities
            if not hass.states.is_state(entity_id, desired_state)
        }

    unsubscribe = async_track_state_change_event(hass, entities, _state_changed)
    max_repeats = call.data["max_repeats"]
    interval = call.data["retry_interval_seconds"]
    pending = entities
    started = monotonic()
    count_signal = runtime.count_signal(desired_state)
    runtime.running[desired_state] += 1
    try:
        async_dispatcher_send(hass, count_signal)
        for _ in range(max_repeats):
            await _async_dispatch(call, domains, service, pending, interval)
            try:
                async with asyncio.timeout(interval):
                    while _pending():
                        changed.clear()
                        await changed.wait()
            except TimeoutError:
                pass

            pending = _pending()
            if not pending:
                return
    finally:
        unsubscribe()
        runtime.running[desired_state] -= 1
        async_dispatcher_send(hass, count_signal)

    async_dispatcher_send(
        hass,
        runtime.failure_signal,
        ATTEMPTS_EXHAUSTED,
        {
            "action": desired_state,
            "attempts": max_repeats,
            "elapsed_seconds": round(monotonic() - started, 3),
            "failed_entities": [
                {
                    "entity_id": entity_id,
                    "last_state": state.state
                    if (state := hass.states.get(entity_id))
                    else None,
                }
                for entity_id in sorted(pending)
            ],
        },
        call.context,
    )
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=ATTEMPTS_EXHAUSTED,
        translation_placeholders={
            "attempts": str(max_repeats),
            "state": desired_state,
            "entities": ", ".join(sorted(pending)),
        },
    )


async def _async_dispatch(
    call: ServiceCall,
    domains: dict[str, str],
    service: str,
    pending: set[str],
    interval: int,
) -> None:
    """Bound command dispatch and clean up sibling calls if a command fails."""
    tasks = [
        asyncio.create_task(_async_send(call, domains[entity_id], service, entity_id))
        for entity_id in sorted(pending)
    ]
    deadline = asyncio.timeout(interval)
    try:
        async with deadline:
            await asyncio.gather(*tasks)
    except TimeoutError:
        if not deadline.expired():
            raise
        _LOGGER.debug("Power command dispatch timed out after %s seconds", interval)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _async_send(
    call: ServiceCall, domain: str, service: str, entity_id: str
) -> None:
    """Dispatch one command with the caller's original context."""
    hass = call.hass
    data: dict[str, str | int] = {ATTR_ENTITY_ID: entity_id}
    if domain == LIGHT_DOMAIN and service == SERVICE_TURN_ON:
        state = hass.states.get(entity_id)
        attributes = state.attributes if state else {}
        modes = attributes.get(ATTR_SUPPORTED_COLOR_MODES)
        if ATTR_BRIGHTNESS_PCT in call.data and brightness_supported(modes):
            data[ATTR_BRIGHTNESS_PCT] = call.data[ATTR_BRIGHTNESS_PCT]
        if ATTR_COLOR_TEMP_KELVIN in call.data and color_temp_supported(modes):
            kelvin = call.data[ATTR_COLOR_TEMP_KELVIN]
            kelvin = max(kelvin, attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN) or kelvin)
            data[ATTR_COLOR_TEMP_KELVIN] = min(
                kelvin, attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN) or kelvin
            )

    try:
        await hass.services.async_call(
            domain, service, data, blocking=True, context=call.context
        )
    except (Unauthorized, UnknownUser):
        raise
    except HomeAssistantError as err:
        _LOGGER.debug("%s.%s failed for %s: %s", domain, service, entity_id, err)
