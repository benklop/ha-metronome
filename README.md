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

## Configuration

Add an empty key so Home Assistant loads the integration:

```yaml
ha_metronome:
```

Copy [WAV click samples](https://github.com/benklop/ha-metronome/tree/main/sounds/Metronomes) into your config: `config/sounds/Metronomes/` (`*_hi.wav` / `*_lo.wav` pairs). If no samples are found, the integration falls back to simple synthesised clicks.

## Blueprint (ZHA knob)

The blueprint at `blueprints/automation/ha_metronome/metronome_knob_zha.yaml` can be copied to `config/blueprints/automation/` and used from the UI, or imported from this repository.

## Features (short)

- HTTP WAV stream: `/api/ha_metronome/stream`
- Services: `ha_metronome.press`, `ha_metronome.rotate`, and direct `start` / `stop` / `set_bpm` / etc. (see `services.yaml` in the component folder)
- State helper entity: `ha_metronome.metronome` (attributes: BPM, beats per measure, mode, stream URL, …)

## Repository metadata (HACS / GitHub)

For [HACS general requirements](https://hacs.xyz/docs/publish/start), set the GitHub repository **Description** in repo settings, and add **Topics** such as: `home-assistant`, `hacs`, `hass`, `metronome`, `custom-components`.

**My link** (one-click HACS add): [Create a my link](https://my.home-assistant.io/create-link/?redirect=hacs_repository) with this repository’s URL.

## License and sounds

See `sounds/Metronomes/Credits.txt` for sample attribution.
