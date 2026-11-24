<p align="center"><img src="custom_components/retry_until/brand/icon.png"></p>
<h1 align="center">Retry Until</h1>

Two Home Assistant actions that repeat an on or off command until the selected entities report the requested state. 

## Setup

Requires Home Assistant 2026.11 or later. 

[![Open Retry Until in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=inventor7777&repository=HA-Retry-Until&category=integration)

For a manual installation, place `custom_components/retry_until` from this repository under your Home Assistant configuration directory. After install, restart Home Assistant and add **Retry Until** from **Settings → Devices & services → Add integration**.


## Actions

Select **Retry Until: On** or **Retry Until: Off** in the action editor. Here is an On example.

```yaml
action: retry_until.on
target:
  entity_id:
    - light.side_porch
    - switch.desk_lamp
data:
  max_repeats: 120
  retry_interval_seconds: 60
  brightness_pct: 65
  color_temp_kelvin: 3000
```

The above example is in YAML, but it fully supports the UI editor and uses native fields.
- The action detects each entity's domain and uses its native `turn_on` or `turn_off` action. Targets can be entities, devices, areas, floors, or labels.
- The first attempt sends a command to every target. Later attempts send only to targets still out of state. Each attempt allows up to the retry interval for commands to return, then up to the same interval for entities to report the requested state. The action finishes early when all targets match. Unloading the integration cancels active retries.
- The On action offers optional brightness and color temperature for lights that report support for the respective setting. The editor's color temperature slider spans 2,000–9,000 K. The action adjusts the requested value to each light's reported minimum and maximum when available. When settings are omitted, the light's normal turn-on behavior applies. Success checks the reported power state.
- Both actions default to 120 total attempts and a 60-second retry interval, and allow up to 240 attempts. Exhausting the attempts raises an action error naming the remaining entities. Use Home Assistant's **Continue on error** option if the sequence should proceed after that failure.
- All code was written by GPT-6.2 Sol, but I tested this extensively in my own instance before publishing.

## Monitoring

- `sensor.retry_until_on_running_count` and `sensor.retry_until_off_running_count` count active calls to each action. A call counts once even when it targets several entities. Counts return to zero when idle.
- `event.retry_until_failure` emits `attempts_exhausted` when a run uses all its attempts without reaching the requested state. The action also raises its usual error in the trace. The event attributes include `action`, `attempts`, `elapsed_seconds`, and `failed_entities`. Each failed entity has an `entity_id` and a `last_state`.
- To react in an automation, use **Event received** with `event.retry_until_failure` as the target and **Attempts exhausted** as the event type. A notification template can read the payload from `trigger.to_state.attributes`.

### Send a persistent notification when a retry fails

Create a new automation, choose **Edit in YAML**, and paste this example. Each exhausted run creates a notification in Home Assistant with the requested state, the attempt count, the elapsed time, and the targets that still did not match.

```yaml
alias: Notify when Retry Until fails
triggers:
  - trigger: event.received
    target:
      entity_id: event.retry_until_failure
    options:
      event_type:
        - attempts_exhausted
actions:
  - action: persistent_notification.create
    data:
      title: Retry Until failed
      message: |-
        {% set failure = trigger.to_state.attributes %}
        Retry Until could not turn every target {{ failure.action | upper }}
        after {{ failure.attempts }} attempts ({{ failure.elapsed_seconds | round(1) }} seconds).

        Targets still out of state:
        {% for entity in failure.failed_entities %}
        - `{{ entity.entity_id }}` (last state: {{ entity.last_state | default('no state reported', true) }})
        {% endfor %}
mode: queued
```
