"""
aai-streamer: OBS Studio script for live captions via AssemblyAI.

See README.md for setup instructions.

Architecture:
  Background thread:  Microphone → AssemblyAI v3 WebSocket → CaptionState
  OBS timer (100ms):  CaptionState.get() → format → update Text source
"""

import os
import sys
import glob
import logging

# Add the project's venv site-packages and src/ to sys.path so OBS can
# find our dependencies (assemblyai, sounddevice, numpy) and our modules.
_script_dir = os.path.dirname(os.path.abspath(__file__))

# Venv layout differs by OS:
#   Linux/macOS: .venv/lib/pythonX.Y/site-packages
#   Windows:     .venv/Lib/site-packages
_venv_candidates = (
    glob.glob(os.path.join(_script_dir, ".venv", "lib", "python*", "site-packages"))
    + glob.glob(os.path.join(_script_dir, ".venv", "Lib", "site-packages"))
)
for _sp in _venv_candidates:
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

_src_dir = os.path.join(_script_dir, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import obspython as obs

# Route Python logging to print() so it shows up in OBS Script Log.
# Guard against duplicate handlers when OBS reloads the script.
_obs_logger = logging.getLogger("aai_streamer")
if not any(isinstance(h, type) and h.__class__.__name__ == "_OBSLogHandler"
           for h in _obs_logger.handlers):
    class _OBSLogHandler(logging.Handler):
        def emit(self, record):
            try:
                print(f"[{record.levelname}] {record.name}: {record.getMessage()}")
            except Exception:
                pass

    _obs_logger.handlers.clear()
    _obs_logger.addHandler(_OBSLogHandler())
    _obs_logger.setLevel(logging.INFO)
    _obs_logger.propagate = False

from aai_streamer.caption_state import CaptionState
from aai_streamer.config import AudioConfig, CaptionConfig, TranscriptionConfig, api_key_from_env
from aai_streamer.formatter import CaptionFormatter
from aai_streamer.microphone import list_microphones
from aai_streamer.transcription import TranscriptionEngine

log = logging.getLogger("aai_streamer")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_caption_state = CaptionState()
_engine: TranscriptionEngine | None = None
_formatter: CaptionFormatter | None = None

# Settings values (updated by script_update)
_api_key: str = ""
_mic_device: int | None = None  # None = system default
_text_source_name: str = ""
_caption_mode: str = "subtitle"
_speech_model: str = "u3-rt-pro"
_max_lines: int = 2
_chars_per_line: int = 40
_max_words: int = 5
_fade_out_seconds: float = 4.0

# Advanced WebSocket settings
_end_of_turn_confidence: float = 0.0  # 0 = server default
_min_turn_silence: int = 0  # 0 = server default (ms)
_max_turn_silence: int = 0  # 0 = server default (ms)
_vad_threshold: float = 0.0  # 0 = server default
_filter_profanity: bool = False

# Positioning
_center_horizontal: bool = False
_center_vertical: bool = False


# ---------------------------------------------------------------------------
# OBS script interface
# ---------------------------------------------------------------------------

def script_description():
    return (
        "<h2>aai-streamer</h2>"
        "<p>Live captions powered by AssemblyAI real-time transcription.</p>"
        "<p>Create a <b>Text (FreeType2)</b> source, then select it below. "
        "Style the text source in OBS (font, size, color, position).</p>"
    )


def script_defaults(settings):
    obs.obs_data_set_default_string(settings, "api_key", "")
    obs.obs_data_set_default_int(settings, "mic_device", -1)  # -1 = default
    obs.obs_data_set_default_string(settings, "text_source", "")
    obs.obs_data_set_default_string(settings, "caption_mode", "subtitle")
    obs.obs_data_set_default_string(settings, "speech_model", "u3-rt-pro")
    obs.obs_data_set_default_int(settings, "max_lines", 2)
    obs.obs_data_set_default_int(settings, "chars_per_line", 40)
    obs.obs_data_set_default_int(settings, "max_words", 5)
    obs.obs_data_set_default_double(settings, "fade_out_seconds", 4.0)
    # Advanced
    obs.obs_data_set_default_double(settings, "end_of_turn_confidence", 0.0)
    obs.obs_data_set_default_int(settings, "min_turn_silence", 0)
    obs.obs_data_set_default_int(settings, "max_turn_silence", 0)
    obs.obs_data_set_default_double(settings, "vad_threshold", 0.0)
    obs.obs_data_set_default_bool(settings, "filter_profanity", False)
    # Positioning
    obs.obs_data_set_default_bool(settings, "center_horizontal", False)
    obs.obs_data_set_default_bool(settings, "center_vertical", False)


def script_properties():
    props = obs.obs_properties_create()

    # API key
    obs.obs_properties_add_text(props, "api_key", "AssemblyAI API Key", obs.OBS_TEXT_PASSWORD)

    # Microphone selector
    mic_list = obs.obs_properties_add_list(
        props, "mic_device", "Microphone",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_INT,
    )
    obs.obs_property_list_add_int(mic_list, "System Default", -1)
    try:
        for mic in list_microphones():
            label = f"{mic['name']} ({mic['channels']}ch, {mic['sample_rate']} Hz)"
            if mic["is_default"]:
                label += " [default]"
            obs.obs_property_list_add_int(mic_list, label, mic["index"])
    except Exception as e:
        log.warning("Could not enumerate microphones: %s", e)

    # Text source selector
    source_list = obs.obs_properties_add_list(
        props, "text_source", "Text Source",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(source_list, "(none)", "")
    sources = obs.obs_enum_sources()
    if sources:
        for source in sources:
            source_id = obs.obs_source_get_unversioned_id(source)
            if source_id in ("text_gdiplus", "text_gdiplus_v2", "text_ft2_source", "text_ft2_source_v2"):
                name = obs.obs_source_get_name(source)
                obs.obs_property_list_add_string(source_list, name, name)
        obs.source_list_release(sources)

    # Caption mode
    mode_list = obs.obs_properties_add_list(
        props, "caption_mode", "Caption Mode",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(mode_list, "Subtitle (wrapped lines)", "subtitle")
    obs.obs_property_list_add_string(mode_list, "Word Pop (recent words)", "wordpop")

    # Speech model
    model_list = obs.obs_properties_add_list(
        props, "speech_model", "Speech Model",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING,
    )
    obs.obs_property_list_add_string(model_list, "Universal 3 RT Pro", "u3-rt-pro")
    obs.obs_property_list_add_string(model_list, "English Streaming (more frequent partials)", "universal-streaming-english")
    obs.obs_property_list_add_string(model_list, "Multilingual Streaming (more frequent partials)", "universal-streaming-multilingual")

    # Caption display settings
    obs.obs_properties_add_int(props, "max_lines", "Max Lines (subtitle)", 1, 5, 1)
    obs.obs_properties_add_int(props, "chars_per_line", "Chars per Line (subtitle)", 20, 80, 5)
    obs.obs_properties_add_int(props, "max_words", "Visible Words (wordpop)", 1, 10, 1)
    obs.obs_properties_add_float_slider(
        props, "fade_out_seconds", "Display Hold Time (s)", 1.0, 15.0, 0.5,
    )

    # Positioning
    obs.obs_properties_add_bool(props, "center_horizontal", "Center Horizontally")
    obs.obs_properties_add_bool(props, "center_vertical", "Center Vertically")

    # Start/Stop buttons
    obs.obs_properties_add_button(props, "start_btn", "Start Captions", _on_start_clicked)
    obs.obs_properties_add_button(props, "stop_btn", "Stop Captions", _on_stop_clicked)

    # --- Advanced Settings (collapsible group) ---
    adv = obs.obs_properties_create()

    obs.obs_properties_add_float_slider(
        adv, "end_of_turn_confidence",
        "End-of-Turn Confidence Threshold",
        0.0, 1.0, 0.05,
    )
    obs.obs_properties_add_text(
        adv, "_eot_help",
        "0 = server default. Higher = waits for more confidence before ending a turn.",
        obs.OBS_TEXT_INFO,
    )

    obs.obs_properties_add_int(
        adv, "min_turn_silence",
        "Min Turn Silence (ms)",
        0, 5000, 100,
    )
    obs.obs_properties_add_text(
        adv, "_min_help",
        "0 = server default. Minimum silence before a turn can end.",
        obs.OBS_TEXT_INFO,
    )

    obs.obs_properties_add_int(
        adv, "max_turn_silence",
        "Max Turn Silence (ms)",
        0, 10000, 100,
    )
    obs.obs_properties_add_text(
        adv, "_max_help",
        "0 = server default. Maximum silence before a turn is forced to end.",
        obs.OBS_TEXT_INFO,
    )

    obs.obs_properties_add_float_slider(
        adv, "vad_threshold",
        "VAD Threshold",
        0.0, 1.0, 0.05,
    )
    obs.obs_properties_add_text(
        adv, "_vad_help",
        "0 = server default. Voice activity detection sensitivity (lower = more sensitive).",
        obs.OBS_TEXT_INFO,
    )

    obs.obs_properties_add_bool(adv, "filter_profanity", "Filter Profanity")

    obs.obs_properties_add_group(
        props, "advanced", "Advanced Settings",
        obs.OBS_GROUP_NORMAL, adv,
    )

    return props


def script_update(settings):
    global _api_key, _mic_device, _text_source_name, _caption_mode
    global _speech_model, _max_lines, _chars_per_line, _max_words
    global _fade_out_seconds, _formatter
    global _end_of_turn_confidence, _min_turn_silence, _max_turn_silence
    global _vad_threshold, _filter_profanity
    global _center_horizontal, _center_vertical

    _api_key = obs.obs_data_get_string(settings, "api_key") or api_key_from_env()
    mic_val = obs.obs_data_get_int(settings, "mic_device")
    _mic_device = None if mic_val == -1 else mic_val
    _text_source_name = obs.obs_data_get_string(settings, "text_source")
    _caption_mode = obs.obs_data_get_string(settings, "caption_mode")
    _speech_model = obs.obs_data_get_string(settings, "speech_model")
    _max_lines = obs.obs_data_get_int(settings, "max_lines")
    _chars_per_line = obs.obs_data_get_int(settings, "chars_per_line")
    _max_words = obs.obs_data_get_int(settings, "max_words")
    _fade_out_seconds = obs.obs_data_get_double(settings, "fade_out_seconds")

    # Advanced
    _end_of_turn_confidence = obs.obs_data_get_double(settings, "end_of_turn_confidence")
    _min_turn_silence = obs.obs_data_get_int(settings, "min_turn_silence")
    _max_turn_silence = obs.obs_data_get_int(settings, "max_turn_silence")
    _vad_threshold = obs.obs_data_get_double(settings, "vad_threshold")
    _filter_profanity = obs.obs_data_get_bool(settings, "filter_profanity")

    # Positioning
    _center_horizontal = obs.obs_data_get_bool(settings, "center_horizontal")
    _center_vertical = obs.obs_data_get_bool(settings, "center_vertical")

    _formatter = CaptionFormatter(CaptionConfig(
        mode=_caption_mode,
        max_words=_max_words,
        fade_out_seconds=_fade_out_seconds,
        max_lines=_max_lines,
        chars_per_line=_chars_per_line,
    ))


def script_load(settings):
    log.info("aai-streamer script loaded")


def script_unload():
    _stop_engine()
    log.info("aai-streamer script unloaded")


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------

def _on_start_clicked(props, prop):
    _start_engine()
    return True


def _on_stop_clicked(props, prop):
    _stop_engine()
    return True


def _start_engine():
    global _engine, _caption_state

    if _engine is not None and _engine.is_running:
        log.info("Already running")
        return

    if not _api_key:
        log.error("No API key set. Enter your AssemblyAI API key or set ASSEMBLYAI_API_KEY.")
        return

    if not _text_source_name:
        log.error("No text source selected. Create a Text source and select it in settings.")
        return

    _caption_state = CaptionState()

    tx_config = TranscriptionConfig(
        api_key=_api_key,
        speech_model=_speech_model,
        format_turns=True,
        end_of_turn_confidence_threshold=_end_of_turn_confidence or None,
        min_turn_silence=_min_turn_silence or None,
        max_turn_silence=_max_turn_silence or None,
        vad_threshold=_vad_threshold or None,
        filter_profanity=_filter_profanity,
    )
    audio_config = AudioConfig(device=_mic_device)

    _engine = TranscriptionEngine(tx_config, audio_config)
    _engine.start(_caption_state)

    # Timer to push captions to OBS text source every 100ms
    obs.timer_add(_update_text_source, 100)

    log.info("Captions started (mic=%s, source=%s, mode=%s)",
             _mic_device, _text_source_name, _caption_mode)


def _stop_engine():
    global _engine

    obs.timer_remove(_update_text_source)

    if _engine is not None:
        _engine.stop()
        _engine = None

    # Clear the text source
    _set_text("")

    log.info("Captions stopped")


# ---------------------------------------------------------------------------
# Timer callback — runs on OBS main thread, updates the Text source
# ---------------------------------------------------------------------------

_last_debug_turn = (-1, False, 0)  # (turn_order, end_of_turn, word_count)
_tick_count = 0

def _update_text_source():
    global _last_debug_turn, _tick_count

    if _caption_state is None or _formatter is None:
        return

    snapshot = _caption_state.get()
    _tick_count += 1

    # Heartbeat every 5s to prove timer is running
    if _tick_count % 50 == 1:
        print(f"[aai] heartbeat tick={_tick_count} empty={snapshot.is_empty}")

    # Log every new snapshot (different turn, finality, or word count)
    snap_key = (snapshot.turn_order, snapshot.end_of_turn, len(snapshot.words))
    if not snapshot.is_empty and snap_key != _last_debug_turn:
        _last_debug_turn = snap_key
        print(f"[aai] turn={snapshot.turn_order} final={snapshot.end_of_turn} "
              f"words={len(snapshot.words)} text={snapshot.transcript[:60]}")

    text = _formatter.format(snapshot)
    _set_text(text)

    if _center_horizontal or _center_vertical:
        _center_source()


def _set_text(text: str):
    """Update the OBS text source. Must be called from the main thread."""
    if not _text_source_name:
        return

    source = obs.obs_get_source_by_name(_text_source_name)
    if source is not None:
        settings = obs.obs_data_create()
        obs.obs_data_set_string(settings, "text", text)
        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)
        obs.obs_source_release(source)


def _center_source():
    """Reposition the text source to center it on the canvas."""
    if not _text_source_name:
        return

    source = obs.obs_get_source_by_name(_text_source_name)
    if source is None:
        return

    src_w = obs.obs_source_get_width(source)
    src_h = obs.obs_source_get_height(source)

    # Get canvas size
    ovi = obs.obs_video_info()
    obs.obs_get_video_info(ovi)
    canvas_w = ovi.base_width
    canvas_h = ovi.base_height

    # Find the scene item in the current scene
    current_scene_source = obs.obs_frontend_get_current_scene()
    if current_scene_source is None:
        obs.obs_source_release(source)
        return

    scene = obs.obs_scene_from_source(current_scene_source)
    scene_item = obs.obs_scene_find_source(scene, _text_source_name)

    if scene_item is not None:
        pos = obs.vec2()
        obs.obs_sceneitem_get_pos(scene_item, pos)

        if _center_horizontal and canvas_w > 0 and src_w > 0:
            pos.x = (canvas_w - src_w) / 2.0
        if _center_vertical and canvas_h > 0 and src_h > 0:
            pos.y = (canvas_h - src_h) / 2.0

        obs.obs_sceneitem_set_pos(scene_item, pos)

    obs.obs_source_release(current_scene_source)
    obs.obs_source_release(source)
