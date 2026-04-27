# HA Metronome

Custom [Home Assistant](https://www.home-assistant.io/) integration that streams a click-track to any `media_player` from WAV samples, with a rotary-knob style workflow (press / double-press / rotate) and optional [HACS](https://www.hacs.xyz/) installation.

## Install with HACS

1. Open HACS in Home Assistant.
2. **HACS** → **Integrations** (or **Custom repositories** first if needed).
3. **⋮** → **Custom repositories**.
4. Add repository URL: `https://github.com/benklop/ha-metronome` (use your fork if you forked it), category: **Integration**.
5. **Download** the integration, then **restart** Home Assistant.

## Manual install

Copy the `custom_components/ha_metronome` folder into your configuration directory (next to `configuration.yaml`):

`config/custom_components/ha_metronome/…`

Restart Home Assistant.

## UI configuration (recommended)

1. Open **Settings** → **Devices & services** → **Add integration** (or **Integrations** in older UI).
2. Search for **HA Metronome** and complete the short form (a single page with **Submit**).

You can remove a legacy empty `ha_metronome:` block from `configuration.yaml` if you add the integration this way. If you still have that block, Home Assistant will import a config entry for you on restart so the integration appears under **Settings** without a second step.

## Optional YAML (legacy / import)

If you have not used the UI yet, you can still use an empty key so a config entry is created from YAML on the next restart:

```yaml
ha_metronome:
```

You do not need this once the integration is added in the UI.

## Click samples

Copy [WAV click samples](https://github.com/benklop/ha-metronome/tree/main/sounds/Metronomes) into your config: `config/sounds/Metronomes/` (`*_hi.wav` / `*_lo.wav` pairs). If no samples are found, the integration falls back to simple synthesised clicks.

## Blueprint (ZHA knob)

The blueprint at `blueprints/automation/ha_metronome/metronome_knob_zha.yaml` can be copied to `config/blueprints/automation/` and used from the UI, or imported from this repository.

Some Tuya scene knobs (e.g. C3006) send several `zha_event` types per detent; the blueprint has **React to step / left&right / rotate_type** toggles so you only handle one. A chronological example from a real device is in [`zha_event.yaml`](zha_event.yaml) at the repository root.

The trigger uses the **ZHA device selector**; Home Assistant subscribes to `zha_event` with a **`device_id`** that matches the device you picked (the same `device_id` you see in event logs, e.g. in [`zha_event.yaml`](zha_event.yaml)).

### Troubleshooting: no rotation, empty automation trace

A trace only **runs the automation** when the **event trigger** matches. The **press** and **turn** events for the same knob almost always use the same `device_id` in `zha_event` — if **press** shows in the trace but **turn** does not, the trigger did not see that event (mismatched `device_id`) **or** the automation ran but no **choose** path ran a visible action (React-to toggles, command names).

1. **Confirm events exist (not the metronome yet)**  
   **Developer tools → Events → Start listening** for **`zha_event`**, optional payload **empty** so you see everything. Turn the knob. If you never see `left`, `right`, `step`, or `rotate_type` for this node, the problem is **ZHA / device / mode**, not the blueprint. Some Tuya knobs need **dimmer vs scene** mode (or a long-press / attribute change) so rotation is emitted. Search for your model in [ZHA device handlers](https://github.com/zigpy/zha-device-handlers) and check a **quirk** is applied.

2. **If turn events have a different `device_id` than your picker** (rare)  
   In the event log, compare `data.device_id` on a **turn** to **Settings → Devices** → that knob. Re-select the device in the blueprint, or (for debugging) use [`examples/diagnostics_zha_knob_event.yaml`](examples/diagnostics_zha_knob_event.yaml) with **`device_ieee`** to confirm rotation events are present.

3. **Trigger ran but no `ha_metronome.rotate` step**  
   At least one of **React to step** / **left&right** / **rotate_type** must be **on** for the `command` values your device actually sends. If all are off (or the wrong one is on for Tuya’s duplicate events), the **choose** block can exit without calling rotate. Turn on **Log unmatched `zha_event` (debug)** in the blueprint; a **logbook** line will show the unhandled `command` and `params` so you can match them to the React options.

4. **Diagnostic automation** (same as step 2)  
   [`examples/diagnostics_zha_knob_event.yaml`](examples/diagnostics_zha_knob_event.yaml) notifies on every `zha_event` for one **`device_ieee`**. If that fires on **press** but never on **rotate**, rotation is not reaching ZHA. If it never fires, the **IEEE** string is wrong or there are no events from the node.

5. **Range / power**  
   Low battery, weak link, or **wake** the device (e.g. one press) before slow turns.

## Features (short)

- HTTP WAV stream: `/api/ha_metronome/stream`
- Services: `ha_metronome.press`, `ha_metronome.rotate`, and direct `start` / `stop` / `set_bpm` / etc. (see `services.yaml` in the component folder)
- State helper entity: `ha_metronome.metronome` (attributes: BPM, beats per measure, mode, stream URL, …)

## Repository metadata (HACS / GitHub)

For [HACS general requirements](https://hacs.xyz/docs/publish/start), set the GitHub repository **Description** in repo settings, and add **Topics** such as: `home-assistant`, `hacs`, `hass`, `metronome`, `custom-components`.

**My link** (one-click HACS add): [Create a my link](https://my.home-assistant.io/create-link/?redirect=hacs_repository) with this repository’s URL.

## License and sounds

See `sounds/Metronomes/Credits.txt` for sample attribution.
