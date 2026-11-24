"""Test the registered Retry Until actions."""

import asyncio
from unittest.mock import patch

import probatio
import pytest
from homeassistant.components import light
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant.common import MockConfigEntry

ON_COUNT = "sensor.retry_until_on_running_count"
OFF_COUNT = "sensor.retry_until_off_running_count"
FAILURE = "event.retry_until_failure"


@pytest.fixture
async def retry_entry(
    hass: HomeAssistant, enable_custom_integrations
) -> MockConfigEntry:
    """Set up the custom integration."""
    entry = MockConfigEntry(domain="retry_until")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_action_descriptions(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """Both actions expose native targets and only On offers light settings."""
    # Only light enums are needed. The full lookup imports optional Core dependencies.
    with patch(
        "homeassistant.helpers.service._base_components", return_value={"light": light}
    ):
        descriptions = (await async_get_all_descriptions(hass))["retry_until"]
    assert set(descriptions) == {"on", "off"}
    assert descriptions["on"]["target"] == descriptions["off"]["target"]
    assert "switch" in descriptions["off"]["target"]["entity"][0]["domain"]
    assert set(descriptions["off"]["fields"]) == {
        "max_repeats",
        "retry_interval_seconds",
    }
    assert set(descriptions["on"]["fields"]) - set(descriptions["off"]["fields"]) == {
        "brightness_pct",
        "color_temp_kelvin",
    }
    names = await async_get_translations(hass, "en", "services", ["retry_until"])
    assert names["component.retry_until.services.on.name"] == "On"
    assert names["component.retry_until.services.off.name"] == "Off"


async def test_retry_targets_only_entities_that_missed_the_state(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """A light gets its settings once while an unresponsive switch is retried."""
    wide_range_light = "light.desk"
    standard_light = "light.floor"
    switch = "switch.desk_plug"
    light_calls = []
    switch_calls = []
    for entity_id, maximum in ((wide_range_light, 9000), (standard_light, 6500)):
        hass.states.async_set(
            entity_id,
            "off",
            {
                "supported_color_modes": ["color_temp"],
                "min_color_temp_kelvin": 2000,
                "max_color_temp_kelvin": maximum,
            },
        )
    hass.states.async_set(switch, "off")

    async def turn_on_light(call: ServiceCall) -> None:
        light_calls.append(dict(call.data))
        hass.states.async_set(call.data["entity_id"], "on")

    async def turn_on_switch(call: ServiceCall) -> None:
        switch_calls.append(dict(call.data))
        if len(switch_calls) == 2:
            hass.states.async_set(switch, "on")

    hass.services.async_register("light", "turn_on", turn_on_light)
    hass.services.async_register("switch", "turn_on", turn_on_switch)

    await hass.services.async_call(
        "retry_until",
        "on",
        {
            "max_repeats": 2,
            "retry_interval_seconds": 1,
            "brightness_pct": 65,
            "color_temp_kelvin": 8000,
        },
        target={"entity_id": [wide_range_light, standard_light, switch]},
        blocking=True,
    )

    assert sorted(light_calls, key=lambda call: call["entity_id"]) == [
        {
            "entity_id": wide_range_light,
            "brightness_pct": 65,
            "color_temp_kelvin": 8000,
        },
        {"entity_id": standard_light, "brightness_pct": 65, "color_temp_kelvin": 6500},
    ]
    assert switch_calls == [{"entity_id": switch}, {"entity_id": switch}]


async def test_off_retries_turn_off(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """The Off action dispatches turn_off and waits for the off state."""
    entity_id = "light.desk"
    hass.states.async_set(entity_id, "on")
    calls = []

    async def turn_off(call: ServiceCall) -> None:
        calls.append(dict(call.data))
        if len(calls) == 2:
            hass.states.async_set(entity_id, "off")

    hass.services.async_register("light", "turn_off", turn_off)

    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            "retry_until",
            "off",
            {"brightness_pct": 50},
            target={"entity_id": entity_id},
            blocking=True,
        )

    await hass.services.async_call(
        "retry_until",
        "off",
        {"max_repeats": 2, "retry_interval_seconds": 1},
        target={"entity_id": entity_id},
        blocking=True,
    )
    assert calls == [{"entity_id": entity_id}, {"entity_id": entity_id}]


async def test_running_counts_follow_concurrent_calls(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """Overlapping calls count independently by action, including on cancellation."""
    switches = ["switch.first", "switch.second", "switch.third"]
    started = {entity_id: asyncio.Event() for entity_id in switches}
    for entity_id in switches:
        hass.states.async_set(entity_id, "off")

    async def turn_on(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        started[entity_id].set()
        await asyncio.Future()

    async def turn_off(call: ServiceCall) -> None:
        await turn_on(call)

    hass.services.async_register("switch", "turn_on", turn_on)
    hass.services.async_register("switch", "turn_off", turn_off)
    assert hass.states.is_state(ON_COUNT, "0")
    assert hass.states.is_state(OFF_COUNT, "0")

    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        runs = [
            group.create_task(
                hass.services.async_call(
                    "retry_until",
                    action,
                    target={"entity_id": entity_id},
                    blocking=True,
                )
            )
            for action, entity_id in zip(("on", "on", "off"), switches, strict=True)
        ]
        for event in started.values():
            await event.wait()
        assert hass.states.is_state(ON_COUNT, "2")
        assert hass.states.is_state(OFF_COUNT, "1")

        runs[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await runs[0]
        assert hass.states.is_state(ON_COUNT, "1")
        assert hass.states.is_state(OFF_COUNT, "1")

        for run in runs[1:]:
            run.cancel()

    assert hass.states.is_state(ON_COUNT, "0")
    assert hass.states.is_state(OFF_COUNT, "0")
    assert hass.states.is_state(FAILURE, "unknown")


async def test_attempts_exhausted_reports_failure(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """Publish only the remaining targets while still raising the action error."""
    succeeded = "switch.first"
    failed = "switch.second"
    for entity_id in (succeeded, failed):
        hass.states.async_set(entity_id, "on")

    async def turn_off(call: ServiceCall) -> None:
        if call.data["entity_id"] == succeeded:
            hass.states.async_set(succeeded, "off")

    hass.services.async_register("switch", "turn_off", turn_off)
    context = Context()
    with pytest.raises(HomeAssistantError) as error:
        await hass.services.async_call(
            "retry_until",
            "off",
            {"max_repeats": 1, "retry_interval_seconds": 1},
            context=context,
            target={"entity_id": [succeeded, failed]},
            blocking=True,
        )

    assert error.value.translation_key == "attempts_exhausted"
    event = hass.states.get(FAILURE)
    assert event is not None
    assert event.state != "unknown"
    assert event.context.id == context.id
    assert event.attributes["event_type"] == "attempts_exhausted"
    assert event.attributes["action"] == "off"
    assert event.attributes["attempts"] == 1
    assert event.attributes["elapsed_seconds"] >= 1
    assert event.attributes["failed_entities"] == [
        {"entity_id": failed, "last_state": "on"}
    ]
    assert hass.states.is_state(OFF_COUNT, "0")


@pytest.mark.parametrize(
    ("domain", "translation_key"),
    [
        ("sensor", "unsupported_targets"),
        ("update", "unsupported_targets"),
        ("button", "unsupported_targets"),
        ("switch", "unknown_targets"),
    ],
)
async def test_retry_rejects_invalid_entity(
    hass: HomeAssistant, retry_entry: MockConfigEntry, domain: str, translation_key: str
) -> None:
    """Invalid explicit targets fail before any command is sent."""

    async def unexpected_call(call: ServiceCall) -> None:
        pytest.fail(f"Unexpected power command for {call.data}")

    hass.services.async_register(domain, "turn_on", unexpected_call)
    if domain != "switch":
        hass.services.async_register("switch", "turn_on", unexpected_call)
    hass.states.async_set("switch.desk_plug", "off")

    with pytest.raises(ServiceValidationError) as error:
        await hass.services.async_call(
            "retry_until",
            "on",
            target={"entity_id": [f"{domain}.example", "switch.desk_plug"]},
            blocking=True,
        )

    assert error.value.translation_key == translation_key
    assert error.value.translation_placeholders == {"entities": f"{domain}.example"}


async def test_retry_cancels_stalled_command_and_retries(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """A registered switch can be retried before it first reports a state."""
    entity = er.async_get(hass).async_get_or_create("switch", "test", "plug")
    stalled = asyncio.Event()
    cancelled = asyncio.Event()
    calls = 0

    async def turn_on(call: ServiceCall) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await stalled.wait()
            finally:
                cancelled.set()
        else:
            assert cancelled.is_set()
            hass.states.async_set(entity.entity_id, "on")

    hass.services.async_register("switch", "turn_on", turn_on)

    async with asyncio.timeout(5):
        await hass.services.async_call(
            "retry_until",
            "on",
            {"max_repeats": 2, "retry_interval_seconds": 1},
            target={"entity_id": entity.entity_id},
            blocking=True,
        )

    assert calls == 2


async def test_unload_cancels_active_retry(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """Unloading cancels the action and drains its active command."""
    entity_id = "switch.desk_plug"
    hass.states.async_set(entity_id, "off")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def turn_on(call: ServiceCall) -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    hass.services.async_register("switch", "turn_on", turn_on)

    async with asyncio.timeout(5):
        run = asyncio.create_task(
            hass.services.async_call(
                "retry_until",
                "on",
                target={"entity_id": entity_id},
                blocking=True,
            )
        )
        await started.wait()
        assert await hass.config_entries.async_unload(retry_entry.entry_id)
        with pytest.raises(asyncio.CancelledError):
            await run

    assert cancelled.is_set()


async def test_command_failure_cancels_siblings(
    hass: HomeAssistant, retry_entry: MockConfigEntry
) -> None:
    """An unexpected command failure propagates after sibling cleanup."""
    entity_ids = ["switch.desk", "switch.floor"]
    for entity_id in entity_ids:
        hass.states.async_set(entity_id, "off")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def turn_on(call: ServiceCall) -> None:
        if call.data["entity_id"] == entity_ids[0]:
            await started.wait()
            raise RuntimeError("command failed")
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    hass.services.async_register("switch", "turn_on", turn_on)

    async with asyncio.timeout(5):
        with pytest.raises(RuntimeError, match="command failed"):
            await hass.services.async_call(
                "retry_until",
                "on",
                target={"entity_id": entity_ids},
                blocking=True,
            )

    assert cancelled.is_set()
