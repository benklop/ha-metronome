"""Constants for the HA Metronome integration."""

DOMAIN = "ha_metronome"

# Audio format — must match the sample files
SAMPLE_RATE = 48000
CHANNELS = 2               # stereo
BITS_PER_SAMPLE = 16
BYTES_PER_FRAME = CHANNELS * (BITS_PER_SAMPLE // 8)  # 4

# BPM limits
DEFAULT_BPM = 120
MIN_BPM = 20
MAX_BPM = 300

# Default sound set (name without _hi/_lo.wav suffix)
DEFAULT_SOUND = "Perc_MetronomeQuartz"

# Relative path inside HA config dir where the sound files live
DEFAULT_SOUNDS_SUBDIR = "sounds/Metronomes"

# Tap / mode defaults
DEFAULT_DOUBLE_TAP_WINDOW_MS = 400   # two taps within this → double-tap
DEFAULT_MEASURE_MODE_TIMEOUT_S = 5   # seconds before measure-mode auto-exits
DEFAULT_BPM_STEP = 2                 # BPM change per rotation step

# Mode values (stored in state attribute)
MODE_NORMAL = "normal"
MODE_ADJUST_MEASURE = "adjust_measure"

# Services
SERVICE_START = "start"
SERVICE_STOP = "stop"
SERVICE_SET_BPM = "set_bpm"
SERVICE_ADJUST_BPM = "adjust_bpm"
SERVICE_PLAY_ON = "play_on"
SERVICE_SET_SOUND = "set_sound"
# Knob-oriented services (used by blueprint)
SERVICE_PRESS = "press"     # handles single/double-tap internally
SERVICE_ROTATE = "rotate"   # adjusts BPM or beats depending on mode

# Service data keys
ATTR_BPM = "bpm"
ATTR_DELTA = "delta"
ATTR_MEDIA_PLAYER = "media_player"
ATTR_BEATS_PER_MEASURE = "beats_per_measure"
ATTR_SOUND = "sound"
ATTR_ACCENT_ENABLED = "accent_enabled"
ATTR_DIRECTION = "direction"          # +1 (CW) or -1 (CCW)
ATTR_BPM_STEP = "bpm_step"
ATTR_DOUBLE_TAP_WINDOW_MS = "double_tap_window_ms"
ATTR_MEASURE_MODE_TIMEOUT_S = "measure_mode_timeout_s"

# State entity id  (set via hass.states.async_set — no formal platform needed)
STATE_ENTITY_ID = f"{DOMAIN}.metronome"

# HTTP stream path
STREAM_PATH = f"/api/{DOMAIN}/stream"
