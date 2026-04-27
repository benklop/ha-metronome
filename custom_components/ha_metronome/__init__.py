"""HA Metronome — streams a real-sample click-track WAV to any media player.

Architecture
------------
A MetronomeState object holds the running/paused flag, BPM, beat position,
selected click sounds, and the current interaction mode (normal vs adjust_measure).

An aiohttp StreamResponse endpoint at /api/ha_metronome/stream generates the
audio stream on-the-fly.  Each beat chunk is exactly
``sample_rate * 60 / bpm`` stereo-16-bit frames: click PCM followed by silence.
The player's own clock consumes samples at the playback rate, so the tempo
emerges naturally from the audio data — no polling, no scheduling jitter.

Double-tap and mode management
-------------------------------
Rather than dealing with YAML automation timing quirks, the component handles
all tap semantics internally:

  - Single tap  →  toggle start/stop (with configurable delay to distinguish)
  - Double tap  →  enter "adjust_measure" mode
  - In adjust_measure: rotating changes beats_per_measure instead of BPM
  - adjust_measure exits automatically after a configurable timeout, or on any press

Services exposed to blueprints
--------------------------------
  ha_metronome.press    — call this on every knob press;  handles single/double-tap
  ha_metronome.rotate   — call this on every rotation;  adjusts BPM or beats per mode

Direct control services (also usable from dashboards / scripts)
---------------------------------------------------------------
  ha_metronome.start              — resume/start clicking
  ha_metronome.stop               — pause (stream stays open, sends silence)
  ha_metronome.set_bpm            — set exact BPM; optional beats_per_measure
  ha_metronome.adjust_bpm         — nudge BPM by ±delta (clamped 20-300)
  ha_metronome.set_sound          — change click sound by name
  ha_metronome.play_on            — start + tell a media_player to open the stream

State entity
------------
  ha_metronome.metronome   state="on"|"off"
    attributes: bpm, beats_per_measure, mode, accent_enabled,
                sound, available_sounds, stream_url

Sound files
-----------
  <config>/sounds/Metronomes/<Name>_hi.wav   — accent click (beat 1 of measure)
  <config>/sounds/Metronomes/<Name>_lo.wav   — regular click (all other beats)

The _hi / _lo files are separate recordings from the CC0 sound pack by Ludwig
Peter Müller.  They have different tonal character; _hi is typically a sharper
transient accent.  Setting accent_enabled=false makes every beat use _lo.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import audioop
import wave as wave_module
from typing import Optional

import voluptuous as vol
from aiohttp import web

from homeassistant import config_entries
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ACCENT_ENABLED,
    ATTR_BEATS_PER_MEASURE,
    ATTR_BPM,
    ATTR_BPM_STEP,
    ATTR_DELTA,
    ATTR_DIRECTION,
    ATTR_DOUBLE_TAP_WINDOW_MS,
    ATTR_MEASURE_MODE_TIMEOUT_S,
    ATTR_MEDIA_PLAYER,
    ATTR_SOUND,
    BITS_PER_SAMPLE,
    BYTES_PER_FRAME,
    CHANNELS,
    DEFAULT_BPM,
    DEFAULT_BPM_STEP,
    DEFAULT_DOUBLE_TAP_WINDOW_MS,
    DEFAULT_MEASURE_MODE_TIMEOUT_S,
    DEFAULT_SOUND,
    DEFAULT_SOUNDS_SUBDIR,
    DOMAIN,
    KEY_STOP_UNSUB,
    KEY_HTTP_VIEW_REGISTERED,
    MAX_BPM,
    MIN_BPM,
    MODE_ADJUST_MEASURE,
    MODE_NORMAL,
    SAMPLE_RATE,
    SERVICE_ADJUST_BPM,
    SERVICE_PLAY_ON,
    SERVICE_PRESS,
    SERVICE_ROTATE,
    SERVICE_SET_BPM,
    SERVICE_SET_SOUND,
    SERVICE_START,
    SERVICE_STOP,
    STATE_ENTITY_ID,
    STREAM_PATH,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _make_wav_header(
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    bits: int = BITS_PER_SAMPLE,
) -> bytes:
    data_size = 0x7FFFFFFF
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size,
        b"WAVE",
        b"fmt ", 16,
        1, channels, sample_rate, byte_rate, block_align, bits,
        b"data", data_size,
    )


def _load_wav_pcm(path: str) -> Optional[bytes]:
    try:
        with wave_module.open(path, "rb") as w:
            pcm = w.readframes(w.getnframes())
            in_rate = int(w.getframerate())
            in_channels = int(w.getnchannels())
            in_width = int(w.getsampwidth())

            # Normalize to the stream format we advertise in the WAV header:
            # 48kHz, stereo, 16-bit signed little-endian PCM.
            #
            # Without this, if sample files are e.g. 24kHz, the metronome will
            # sound at ~2× tempo even though the BPM state is correct.
            if in_width == 1:
                # WAV stores 8-bit PCM as unsigned; audioop assumes signed.
                pcm = audioop.bias(pcm, 1, -128)

            if in_width != 2:
                pcm = audioop.lin2lin(pcm, in_width, 2)
                in_width = 2

            if in_rate != SAMPLE_RATE:
                pcm, _state = audioop.ratecv(
                    pcm, in_width, in_channels, in_rate, SAMPLE_RATE, None
                )
                in_rate = SAMPLE_RATE

            if in_channels != CHANNELS:
                if in_channels == 1 and CHANNELS == 2:
                    pcm = audioop.tostereo(pcm, in_width, 1.0, 1.0)
                    in_channels = 2
                elif in_channels == 2 and CHANNELS == 1:
                    pcm = audioop.tomono(pcm, in_width, 0.5, 0.5)
                    in_channels = 1
                else:
                    _LOGGER.warning(
                        "Unsupported channel conversion %s -> %s for %s",
                        in_channels,
                        CHANNELS,
                        path,
                    )
                    return None

            return pcm
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Could not load sound file: %s", path)
        return None


def _synth_click(freq: int, duration_s: float = 0.020) -> bytes:
    """Fallback synthesised click — used only when WAV files are unavailable."""
    n = int(SAMPLE_RATE * duration_s)
    buf = bytearray(n * BYTES_PER_FRAME)
    decay = 1.0 / (n * 0.25)
    for i in range(n):
        t = i / SAMPLE_RATE
        env = math.exp(-i * decay)
        s = int(32767 * 0.85 * math.sin(2 * math.pi * freq * t) * env)
        s = max(-32768, min(32767, s))
        struct.pack_into("<hh", buf, i * BYTES_PER_FRAME, s, s)
    return bytes(buf)


def _discover_sounds(sounds_dir: str) -> list[str]:
    if not os.path.isdir(sounds_dir):
        return []
    names: set[str] = set()
    for fname in os.listdir(sounds_dir):
        if fname.endswith("_hi.wav"):
            lo = fname.replace("_hi.wav", "_lo.wav")
            if os.path.isfile(os.path.join(sounds_dir, lo)):
                names.add(fname[: -len("_hi.wav")])
    return sorted(names)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class MetronomeState:
    """All runtime state, shared between the HTTP view and service handlers."""

    def __init__(
        self,
        sounds_dir: str,
        default_sound: str,
        available_sounds: list[str],
    ) -> None:
        # Playback
        self.playing: bool = False
        self.bpm: int = DEFAULT_BPM
        self.beats_per_measure: int = 4
        self._beat_index: int = 0
        self.accent_enabled: bool = True
        # Whether we've applied "startup overrides" at least once.
        # The knob blueprint sends bpm/beats/sound on every press; we only want those
        # to act as initial configuration, not overwrite the user's current tempo
        # every time they toggle start/stop.
        self._startup_overrides_applied: bool = False

        # Interaction mode
        self.mode: str = MODE_NORMAL
        self._measure_exit_handle: Optional[asyncio.TimerHandle] = None

        # Double-tap tracking
        self._last_press_time: float = 0.0
        self._pending_single_tap: Optional[asyncio.TimerHandle] = None
        # Defaults — overridable per press/rotate call
        self.double_tap_window: float = DEFAULT_DOUBLE_TAP_WINDOW_MS / 1000
        self.measure_timeout: float = DEFAULT_MEASURE_MODE_TIMEOUT_S

        # Sounds
        self.sounds_dir = sounds_dir
        self.available_sounds = available_sounds
        self._click_hi: bytes = b""
        self._click_lo: bytes = b""
        self._sound_name: str = ""
        self._load_sound(default_sound)

    # ------------------------------------------------------------------
    # Beat / tempo
    # ------------------------------------------------------------------

    @property
    def beat_index(self) -> int:
        return self._beat_index

    def advance_beat(self) -> None:
        self._beat_index = (self._beat_index + 1) % self.beats_per_measure

    def reset_beat(self) -> None:
        self._beat_index = 0

    def set_bpm(self, bpm: int) -> None:
        self.bpm = max(MIN_BPM, min(MAX_BPM, bpm))

    def adjust_bpm(self, delta: int) -> None:
        self.set_bpm(self.bpm + delta)

    # ------------------------------------------------------------------
    # Sound loading
    # ------------------------------------------------------------------

    def _load_sound(self, name: str) -> bool:
        if name not in self.available_sounds:
            matches = [s for s in self.available_sounds if s.lower() == name.lower()]
            if not matches:
                _LOGGER.error("Sound '%s' not found. Available: %s",
                              name, ", ".join(self.available_sounds[:5]))
                return False
            name = matches[0]

        hi = _load_wav_pcm(os.path.join(self.sounds_dir, f"{name}_hi.wav"))
        lo = _load_wav_pcm(os.path.join(self.sounds_dir, f"{name}_lo.wav"))
        if hi is None or lo is None:
            return False

        self._click_hi = hi
        self._click_lo = lo
        self._sound_name = name
        _LOGGER.info("Loaded sound set: %s", name)
        return True

    def set_sound(self, name: str) -> bool:
        return self._load_sound(name)

    @property
    def sound_name(self) -> str:
        return self._sound_name

    @property
    def click_hi(self) -> bytes:
        return self._click_hi

    @property
    def click_lo(self) -> bytes:
        return self._click_lo

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def enter_measure_mode(self, timeout: float, loop: asyncio.AbstractEventLoop,
                           on_exit) -> None:
        """Switch to adjust_measure mode and schedule auto-exit."""
        self._cancel_measure_timer()
        self.mode = MODE_ADJUST_MEASURE
        self.measure_timeout = timeout
        _LOGGER.info("Entering beats-per-measure adjustment mode (timeout %.1fs)", timeout)

        def _auto_exit():
            self._measure_exit_handle = None
            on_exit()

        self._measure_exit_handle = loop.call_later(timeout, _auto_exit)

    def exit_measure_mode(self) -> None:
        self._cancel_measure_timer()
        self.mode = MODE_NORMAL
        _LOGGER.info("Exiting beats-per-measure adjustment mode")

    def reset_measure_timer(self, loop: asyncio.AbstractEventLoop, on_exit) -> None:
        """Reset the auto-exit countdown (called on each rotation in measure mode)."""
        self._cancel_measure_timer()

        def _auto_exit():
            self._measure_exit_handle = None
            on_exit()

        self._measure_exit_handle = loop.call_later(self.measure_timeout, _auto_exit)

    def _cancel_measure_timer(self) -> None:
        if self._measure_exit_handle is not None:
            self._measure_exit_handle.cancel()
            self._measure_exit_handle = None

    def cancel_interaction_timers(self) -> None:
        """Cancel pending single-tap and measure-mode timers (reload/shutdown)."""
        if self._pending_single_tap is not None:
            self._pending_single_tap.cancel()
            self._pending_single_tap = None
        self._cancel_measure_timer()
        if self.mode == MODE_ADJUST_MEASURE:
            self.mode = MODE_NORMAL


# ---------------------------------------------------------------------------
# HTTP streaming view
# ---------------------------------------------------------------------------

class MetronomeStreamView(HomeAssistantView):
    """Continuous WAV stream at /api/ha_metronome/stream.

    Sends silence while paused; sends one beat-chunk per loop when playing.
    """

    url = STREAM_PATH
    name = f"api:{DOMAIN}:stream"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.StreamResponse:
        _LOGGER.debug("Stream connection from %s", request.remote)
        response = web.StreamResponse(
            headers={
                "Content-Type": "audio/wav",
                "Cache-Control": "no-cache, no-store",
            }
        )
        await response.prepare(request)
        await response.write(_make_wav_header())

        try:
            async for chunk, duration_s in self._generate_audio():
                await response.write(chunk)
                # Pace the stream in real time.
                # Without this, the server can run ahead and the media_player buffers
                # minutes of audio, making BPM changes appear to lag.
                if duration_s > 0:
                    await asyncio.sleep(duration_s)
                else:
                    await asyncio.sleep(0)
        except (ConnectionResetError, asyncio.CancelledError):
            _LOGGER.debug("Stream client disconnected")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error in metronome stream")

        return response

    async def _generate_audio(self):
        silence_frames = SAMPLE_RATE // 10  # 100 ms
        silence_block = bytes(silence_frames * BYTES_PER_FRAME)

        while True:
            state: MetronomeState | None = self.hass.data.get(DOMAIN)
            if state is None or not state.playing:
                yield silence_block, (silence_frames / SAMPLE_RATE)
                continue

            bpm = state.bpm
            beat_frames = int(SAMPLE_RATE * 60.0 / bpm)

            # Beat 0 of each measure uses the accent click (_hi) when enabled
            if state.accent_enabled and state.beat_index == 0:
                click = state.click_hi
            else:
                click = state.click_lo
            state.advance_beat()

            click_frames = len(click) // BYTES_PER_FRAME
            silence_frames = max(0, beat_frames - click_frames)
            yield (click + bytes(silence_frames * BYTES_PER_FRAME)), (beat_frames / SAMPLE_RATE)


# ---------------------------------------------------------------------------
# Integration setup
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Load integration (HTTP view once) and import config entry for legacy yaml."""

    if not hass.data.get(KEY_HTTP_VIEW_REGISTERED):
        hass.http.register_view(MetronomeStreamView(hass))
        hass.data[KEY_HTTP_VIEW_REGISTERED] = True
    if DOMAIN in config and not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_IMPORT}
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Set up runtime state, services, and the metronome state entity from a config entry."""

    sounds_dir = hass.config.path(DEFAULT_SOUNDS_SUBDIR)
    available_sounds = await hass.async_add_executor_job(_discover_sounds, sounds_dir)

    if not available_sounds:
        _LOGGER.warning(
            "No sounds found in %s — falling back to synthesised clicks.", sounds_dir
        )
        synth_hi = _synth_click(1500)
        synth_lo = _synth_click(1000)

        class _FallbackState(MetronomeState):
            def _load_sound(self, name: str) -> bool:
                self._click_hi = synth_hi
                self._click_lo = synth_lo
                self._sound_name = "synthesised"
                self.available_sounds = ["synthesised"]
                return True

        state = _FallbackState(sounds_dir, "synthesised", ["synthesised"])
    else:
        default = DEFAULT_SOUND if DEFAULT_SOUND in available_sounds else available_sounds[0]
        state = MetronomeState(sounds_dir, default, available_sounds)

    hass.data[DOMAIN] = state

    try:
        base_url = get_url(hass, allow_internal=True, allow_external=False, allow_ip=True)
    except NoURLAvailableError:
        base_url = "http://homeassistant.local:8123"
    stream_url = f"{base_url}{STREAM_PATH}"

    def _publish_state() -> None:
        hass.states.async_set(
            STATE_ENTITY_ID,
            "on" if state.playing else "off",
            {
                "bpm": state.bpm,
                "beats_per_measure": state.beats_per_measure,
                "mode": state.mode,
                "accent_enabled": state.accent_enabled,
                "sound": state.sound_name,
                "available_sounds": state.available_sounds,
                "stream_url": stream_url,
                "friendly_name": "Metronome",
            },
        )

    _publish_state()

    # ----------------------------------------------------------------
    # Helpers for press service
    # ----------------------------------------------------------------

    async def _do_start(
        media_player,
        bpm,
        beats_per_measure,
        sound,
        accent_enabled,
        *,
        apply_startup_overrides: bool,
    ):
        """Start the metronome and optionally play on a speaker."""
        if apply_startup_overrides and not state._startup_overrides_applied:
            if bpm is not None:
                state.set_bpm(bpm)
            if beats_per_measure is not None:
                state.beats_per_measure = max(1, beats_per_measure)
                state.reset_beat()
            if sound is not None:
                await hass.async_add_executor_job(state.set_sound, sound)
            if accent_enabled is not None:
                state.accent_enabled = accent_enabled
            state._startup_overrides_applied = True
        state.playing = True
        _publish_state()

        if media_player:
            _LOGGER.info("Streaming metronome to %s  url=%s", media_player, stream_url)
            await hass.services.async_call(
                "media_player", "play_media",
                {
                    "entity_id": media_player,
                    "media_content_id": stream_url,
                    "media_content_type": "music",
                },
                blocking=False,
            )

    async def _do_stop(media_player):
        state.playing = False
        state.reset_beat()
        _publish_state()
        if media_player:
            await hass.services.async_call(
                "media_player", "media_stop",
                {"entity_id": media_player},
                blocking=False,
            )

    def _on_measure_mode_exit():
        """Called when measure mode times out or is cancelled."""
        state.exit_measure_mode()
        _publish_state()

    # ----------------------------------------------------------------
    # Service: press
    #
    # Single-tap: toggle start / stop
    # Double-tap: enter beats-per-measure adjustment mode
    #             (any subsequent press exits it immediately)
    # ----------------------------------------------------------------

    async def handle_press(call: ServiceCall) -> None:
        # Extract optional parameters (used on start)
        media_player    = call.data.get(ATTR_MEDIA_PLAYER)
        bpm             = call.data.get(ATTR_BPM)
        beats           = call.data.get(ATTR_BEATS_PER_MEASURE)
        sound           = call.data.get(ATTR_SOUND)
        accent_enabled  = call.data.get(ATTR_ACCENT_ENABLED)
        dtw = call.data.get(ATTR_DOUBLE_TAP_WINDOW_MS, DEFAULT_DOUBLE_TAP_WINDOW_MS) / 1000
        mto = call.data.get(ATTR_MEASURE_MODE_TIMEOUT_S, DEFAULT_MEASURE_MODE_TIMEOUT_S)

        # --- If we're already in measure-adjustment mode, any press exits it ---
        if state.mode == MODE_ADJUST_MEASURE:
            _on_measure_mode_exit()
            return

        # --- Double-tap detection ---
        now = hass.loop.time()
        is_double = (now - state._last_press_time) < dtw
        state._last_press_time = now

        if is_double:
            # Cancel any pending single-tap action
            if state._pending_single_tap is not None:
                state._pending_single_tap.cancel()
                state._pending_single_tap = None
            # Enter measure-adjustment mode
            state.enter_measure_mode(mto, hass.loop, _on_measure_mode_exit)
            _publish_state()
            return

        # --- Schedule single-tap action (delayed so double-tap can cancel it) ---
        if state._pending_single_tap is not None:
            state._pending_single_tap.cancel()
            state._pending_single_tap = None

        def _fire_single_tap():
            state._pending_single_tap = None
            if state.playing:
                hass.async_create_task(_do_stop(media_player))
            else:
                hass.async_create_task(
                    _do_start(
                        media_player,
                        bpm,
                        beats,
                        sound,
                        accent_enabled,
                        apply_startup_overrides=True,
                    )
                )

        state._pending_single_tap = hass.loop.call_later(dtw, _fire_single_tap)

    # ----------------------------------------------------------------
    # Service: rotate
    #
    # Normal mode:         adjust BPM by direction × bpm_step
    # adjust_measure mode: adjust beats_per_measure by direction, reset timer
    # ----------------------------------------------------------------

    async def handle_rotate(call: ServiceCall) -> None:
        direction = int(call.data.get(ATTR_DIRECTION, 1))   # +1 or -1
        bpm_step  = int(call.data.get(ATTR_BPM_STEP, DEFAULT_BPM_STEP))
        mto = call.data.get(ATTR_MEASURE_MODE_TIMEOUT_S, state.measure_timeout)

        if state.mode == MODE_ADJUST_MEASURE:
            new_beats = state.beats_per_measure + direction
            state.beats_per_measure = max(1, min(16, new_beats))
            state.reset_beat()
            # Reset the auto-exit timer on each rotation
            state.reset_measure_timer(hass.loop, _on_measure_mode_exit)
        else:
            state.adjust_bpm(direction * bpm_step)

        _publish_state()

    # ----------------------------------------------------------------
    # Direct control services
    # ----------------------------------------------------------------

    async def handle_start(_call: ServiceCall) -> None:
        state.playing = True
        _publish_state()

    async def handle_stop(_call: ServiceCall) -> None:
        state.playing = False
        state.reset_beat()
        _publish_state()

    async def handle_set_bpm(call: ServiceCall) -> None:
        state.set_bpm(int(call.data[ATTR_BPM]))
        if ATTR_BEATS_PER_MEASURE in call.data:
            state.beats_per_measure = max(1, int(call.data[ATTR_BEATS_PER_MEASURE]))
            state.reset_beat()
        _publish_state()

    async def handle_adjust_bpm(call: ServiceCall) -> None:
        state.adjust_bpm(int(call.data.get(ATTR_DELTA, 1)))
        _publish_state()

    async def handle_set_sound(call: ServiceCall) -> None:
        name = call.data[ATTR_SOUND]
        ok = await hass.async_add_executor_job(state.set_sound, name)
        if ok:
            _publish_state()

    async def handle_play_on(call: ServiceCall) -> None:
        if ATTR_BPM in call.data:
            state.set_bpm(int(call.data[ATTR_BPM]))
        if ATTR_BEATS_PER_MEASURE in call.data:
            state.beats_per_measure = max(1, int(call.data[ATTR_BEATS_PER_MEASURE]))
            state.reset_beat()
        if ATTR_SOUND in call.data:
            await hass.async_add_executor_job(state.set_sound, call.data[ATTR_SOUND])
        if ATTR_ACCENT_ENABLED in call.data:
            state.accent_enabled = bool(call.data[ATTR_ACCENT_ENABLED])

        state.playing = True
        _publish_state()

        media_player = call.data.get(ATTR_MEDIA_PLAYER)
        if media_player:
            await hass.services.async_call(
                "media_player", "play_media",
                {
                    "entity_id": media_player,
                    "media_content_id": stream_url,
                    "media_content_type": "music",
                },
                blocking=False,
            )

    # ----------------------------------------------------------------
    # Register all services
    # ----------------------------------------------------------------

    _bool = vol.Boolean()

    hass.services.async_register(DOMAIN, SERVICE_START, handle_start)
    hass.services.async_register(DOMAIN, SERVICE_STOP, handle_stop)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_BPM, handle_set_bpm,
        schema=vol.Schema({
            vol.Required(ATTR_BPM): vol.All(vol.Coerce(int), vol.Range(MIN_BPM, MAX_BPM)),
            vol.Optional(ATTR_BEATS_PER_MEASURE): vol.All(vol.Coerce(int), vol.Range(1, 16)),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADJUST_BPM, handle_adjust_bpm,
        schema=vol.Schema({vol.Optional(ATTR_DELTA, default=1): vol.Coerce(int)}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SOUND, handle_set_sound,
        schema=vol.Schema({vol.Required(ATTR_SOUND): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_ON, handle_play_on,
        schema=vol.Schema({
            vol.Optional(ATTR_BPM): vol.All(vol.Coerce(int), vol.Range(MIN_BPM, MAX_BPM)),
            vol.Optional(ATTR_BEATS_PER_MEASURE): vol.All(vol.Coerce(int), vol.Range(1, 16)),
            vol.Optional(ATTR_SOUND): cv.string,
            vol.Optional(ATTR_ACCENT_ENABLED): _bool,
            vol.Optional(ATTR_MEDIA_PLAYER): cv.entity_id,
        }),
    )

    # Knob-oriented services
    hass.services.async_register(
        DOMAIN, SERVICE_PRESS, handle_press,
        schema=vol.Schema({
            vol.Optional(ATTR_MEDIA_PLAYER): cv.entity_id,
            vol.Optional(ATTR_BPM): vol.All(vol.Coerce(int), vol.Range(MIN_BPM, MAX_BPM)),
            vol.Optional(ATTR_BEATS_PER_MEASURE): vol.All(vol.Coerce(int), vol.Range(1, 16)),
            vol.Optional(ATTR_SOUND): cv.string,
            vol.Optional(ATTR_ACCENT_ENABLED): _bool,
            vol.Optional(ATTR_DOUBLE_TAP_WINDOW_MS): vol.All(vol.Coerce(float), vol.Range(100, 1000)),
            vol.Optional(ATTR_MEASURE_MODE_TIMEOUT_S): vol.All(vol.Coerce(float), vol.Range(2, 30)),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ROTATE, handle_rotate,
        schema=vol.Schema({
            vol.Required(ATTR_DIRECTION): vol.All(vol.Coerce(int), vol.In([-1, 1])),
            vol.Optional(ATTR_BPM_STEP, default=DEFAULT_BPM_STEP): vol.All(vol.Coerce(int), vol.Range(1, 20)),
            vol.Optional(ATTR_MEASURE_MODE_TIMEOUT_S): vol.All(vol.Coerce(float), vol.Range(2, 30)),
        }),
    )

    @callback
    def _on_hass_stop(_event: Event) -> None:
        """Cancel deferred tap/measure timers so call_later does not run during shutdown."""
        s = hass.data.get(DOMAIN)
        if isinstance(s, MetronomeState):
            s.cancel_interaction_timers()

    hass.data[KEY_STOP_UNSUB] = hass.bus.async_listen(
        EVENT_HOMEASSISTANT_STOP, _on_hass_stop
    )

    _LOGGER.info("HA Metronome ready — %d sounds, stream at %s", len(available_sounds), stream_url)
    return True


async def async_unload_entry(hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Tear down this config entry: services, state, and any pending loop timers."""

    if unsub := hass.data.pop(KEY_STOP_UNSUB, None):
        unsub()

    state = hass.data.get(DOMAIN)
    if state is None:
        return True

    state.cancel_interaction_timers()
    state.playing = False

    for service in (
        SERVICE_START,
        SERVICE_STOP,
        SERVICE_SET_BPM,
        SERVICE_ADJUST_BPM,
        SERVICE_SET_SOUND,
        SERVICE_PLAY_ON,
        SERVICE_PRESS,
        SERVICE_ROTATE,
    ):
        hass.services.async_remove(DOMAIN, service)

    hass.states.async_remove(STATE_ENTITY_ID)
    del hass.data[DOMAIN]

    _LOGGER.info("HA Metronome unloaded")
    return True
