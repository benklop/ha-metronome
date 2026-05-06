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

The stock blueprint assumes your device sends **`left` / `right`** for rotation and the **remote button** commands below. If your knob only emits other commands (for example `step` or `rotate_type`), duplicate the automation and adjust the templates, or start from the event samples in [`zha_event.yaml`](zha_event.yaml) at the repository root.

### Knob behavior (stock blueprint)

The automation listens for `zha_event` on the selected ZHA device and maps commands to metronome services.

| Physical action | Typical `zha_event` `command` | What runs |
|-----------------|------------------------------|-----------|
| Short press | `remote_button_short_press` | **`ha_metronome.press`** — toggles start/stop. When starting, passes your blueprint inputs: initial BPM, beats per measure, click sound, accent, and media player (same values each time; tempo is not reset on every tap). |
| Double press | `remote_button_double_press` | **`ha_metronome.enter_measure_mode`** — rotation temporarily adjusts **beats per measure** instead of BPM. The mode ends automatically after the configured timeout, or immediately if you **long-press** (reset). |
| Long press | `remote_button_long_press` | **`ha_metronome.reset`** — moves to **beat 1**, leaves measure-adjust mode if it was active, and restores **initial BPM**, **beats per measure**, **click sound**, and **accent** to the blueprint defaults (the same inputs used for **press**). Playback keeps running or stopped; only timing, meter, sound, and beat position are realigned. |
| Turn | `left` / `right` | **`ha_metronome.rotate`** — in normal mode, changes BPM by **`bpm_step`** per detent (blueprint default 2). In measure-adjust mode (after double-press), changes beats per measure by **`beats_per_measure_step`** (blueprint default 1). |

**Measure-adjust mode:** While active, the metronome state attribute `mode` is `adjust_measure`. After the timeout, mode returns to `normal` without changing BPM or beats per measure.

### `ha_metronome.reset` (manual or script)

You can call **`ha_metronome.reset`** from **Developer tools → Actions** or any automation.

- **With fields** (as the blueprint does): each supplied field updates that part of state. Omitted fields are left unchanged.
- **With no fields at all**: BPM and beats per measure revert to the integration built-in defaults (120 and 4); sound and accent are not changed.

Always: **beat counter → 1**, and **measure-adjust mode** is exited if it was on.

A chronological example from a real Tuya-style knob is in [`zha_event.yaml`](zha_event.yaml) at the repository root.

The trigger uses the **ZHA device selector**; Home Assistant subscribes to `zha_event` with a **`device_id`** that matches the device you picked (the same `device_id` you see in event logs, e.g. in [`zha_event.yaml`](zha_event.yaml)).

### Troubleshooting: no rotation, empty automation trace

A trace only **runs the automation** when the **event trigger** matches. The **press** and **turn** events for the same knob almost always use the same `device_id` in `zha_event` — if **press** shows in the trace but **turn** does not, the trigger did not see that event (mismatched `device_id`) **or** the automation ran but no **choose** path matched (wrong `command` names for your device).

1. **Confirm events exist (not the metronome yet)**  
   **Developer tools → Events → Start listening** for **`zha_event`**, optional payload **empty** so you see everything. Turn the knob. If you never see `left`, `right`, `step`, or `rotate_type` for this node, the problem is **ZHA / device / mode**, not the blueprint. Some Tuya knobs need **dimmer vs scene** mode (or a long-press / attribute change) so rotation is emitted. Search for your model in [ZHA device handlers](https://github.com/zigpy/zha-device-handlers) and check a **quirk** is applied.

2. **If turn events have a different `device_id` than your picker** (rare)  
   In the event log, compare `data.device_id` on a **turn** to **Settings → Devices** → that knob. Re-select the device in the blueprint, or (for debugging) use [`examples/diagnostics_zha_knob_event.yaml`](examples/diagnostics_zha_knob_event.yaml) with **`device_ieee`** to confirm rotation events are present.

3. **Trigger ran but no `ha_metronome.rotate` step**  
   The stock blueprint only matches **`left`** and **`right`**. If your device sends **`step`**, **`rotate_type`**, or other commands for turns, the **choose** branch never calls **`rotate`** until you extend the automation. Listen to **`zha_event`** and copy the exact `command` (and `params`) your hardware uses, then add matching conditions and the same **`ha_metronome.rotate`** action (with direction derived from the event).

4. **Diagnostic automation** (same as step 2)  
   [`examples/diagnostics_zha_knob_event.yaml`](examples/diagnostics_zha_knob_event.yaml) notifies on every `zha_event` for one **`device_ieee`**. If that fires on **press** but never on **rotate**, rotation is not reaching ZHA. If it never fires, the **IEEE** string is wrong or there are no events from the node.

5. **Range / power**  
   Low battery, weak link, or **wake** the device (e.g. one press) before slow turns.

## Features (short)

- HTTP WAV stream: `/api/ha_metronome/stream`
- Services: knob-oriented **`press`**, **`reset`**, **`enter_measure_mode`**, **`rotate`**, plus **`start`** / **`stop`** / **`set_bpm`** / **`adjust_bpm`** / **`set_sound`** / **`play_on`** (see [`custom_components/ha_metronome/services.yaml`](custom_components/ha_metronome/services.yaml))
- State helper entity: **`ha_metronome.metronome`** (`on` / `off`) with attributes such as BPM, beats per measure, **`mode`** (`normal` or `adjust_measure`), accent, sound, stream URL, …

## Repository metadata (HACS / GitHub)

For [HACS general requirements](https://hacs.xyz/docs/publish/start), set the GitHub repository **Description** in repo settings, and add **Topics** such as: `home-assistant`, `hacs`, `hass`, `metronome`, `custom-components`.

**My link** (one-click HACS add): [Create a my link](https://my.home-assistant.io/create-link/?redirect=hacs_repository) with this repository’s URL.

## License and sounds

See `sounds/Metronomes/Credits.txt` for sample attribution.
